"""
Geocoding Modülü.
Kocaeli Kentsel Haber İzleme Sistemi — scraper/geocoder.py

Haberlerden çıkarılmış doğal adres (konum) metinlerini Google Geocoding API ile matematiksel x,y koordinatlarına (Latitude/Longitude) dönüştürür.

Özellikler:
  - Google Geocoding API bağımlılığı barındırır (.env içinden güvenli okuma yapar)
  - Performans için önceden sorgulanmış adresleri MongoDB konum_cache'inden çeker (Maliyet optimizasyonu)
  - Koordinat haritalandırılamazsa (None, None) döner, haber harita ekranına düşmez
  - Yalnızca Kocaeli sınırları (Bounding Box algoritması) içindeki sonuçları kabul ederek güvenilirlik sağlar
"""

import logging
import os
import sys
from typing import Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

# Proje ana kök dizinini geçici olarak sys path'e ilave ederek db/mongo yerel importunu güvenli kılıyoruz
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from db.mongo import konum_cache_getir, konum_cache_kaydet

# ── .env Yükle ───────────────────────────────────────────────────────────────
load_dotenv()


# ── Sabitler (Constants) ─────────────────────────────────────────────────────

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# İsteklerin atılacağı ana Google Haritalar API uç noktası
GEOCODING_API_URL_SABITI = "https://maps.googleapis.com/maps/api/geocode/json"

# API'nin sonsuz bekleme (hang) durumuna düşmesini engelleyen zaman aşımı limiti (Saniye)
API_ZAMAN_ASIMI_SABITI = 10

# Harici sonuçları dışlamak adına Kocaeli bölgesinin dikdörtgen koordinat sınırları (Cords Bounds)
# southwest: Güneybatı köşesi, northeast: Kuzeydoğu köşesi
KOCAELI_SINIRLARI_SABITI = {
    "southwest": {"lat": 40.5, "lng": 29.3},
    "northeast": {"lat": 40.9, "lng": 30.3},
}

# Şehir merkezine yakın olup ufak sapmalar gösteren GPS verileri için tolere payı (Yaklaşık 20 KM)
SINIR_TOLERANS_SABITI = 0.2


# Basit Konsol Logger konfigürasyonu
logger = logging.getLogger(__name__)


# ── Geocoding Yardımcı Fonksiyonları ─────────────────────────────────────────


def _kocaeli_bolgesinde_mi(lat: float, lng: float) -> bool:
    """
    Bulunan koordinatların coğrafi olarak Kocaeli Bounding Box (Sınır Kutusu) 
    içerisinde kalıp kalmadığını kontrol eder.
    Aynı isimli fakat farklı şehirdeki adreslerin (Örn: İzmit/Kocaeli vs İzmit/Konya) filtrelenmesi içindir.

    Args:
        lat (float): Enlem.
        lng (float): Boylam.

    Returns:
        bool: Eğer sınırlar (+tolerans payı) içerisindeyse True, değilse False.
    """
    sw = KOCAELI_SINIRLARI_SABITI["southwest"]
    ne = KOCAELI_SINIRLARI_SABITI["northeast"]

    return (
        sw["lat"] - SINIR_TOLERANS_SABITI <= lat <= ne["lat"] + SINIR_TOLERANS_SABITI
        and sw["lng"] - SINIR_TOLERANS_SABITI <= lng <= ne["lng"] + SINIR_TOLERANS_SABITI
    )


def _google_api_istegi_yap(konum_metin: str) -> Optional[dict]:
    """
    Google Geocoding API sunucularına HTTP GET isteği gönderir ve JSON yanıtını döndürür.
    Hata durumlarında log ekler ve None döner. 
    (>30 satır kuralına istinaden API haberleşme kısmı ayrıştırılmıştır).

    Args:
        konum_metin (str): Aranacak lokasyon dizgesi.

    Returns:
        Optional[dict]: API'den dönen parse edilmiş JSON sözlüğü. Başarısızlıkta None.
    """
    # Kocaeli ağırlıklı arama kuralını Bounding parametresi ile biçimlendir
    bounds_str = (
        f"{KOCAELI_SINIRLARI_SABITI['southwest']['lat']},"
        f"{KOCAELI_SINIRLARI_SABITI['southwest']['lng']}|"
        f"{KOCAELI_SINIRLARI_SABITI['northeast']['lat']},"
        f"{KOCAELI_SINIRLARI_SABITI['northeast']['lng']}"
    )

    params = {
        "address": konum_metin,
        "key": GOOGLE_MAPS_API_KEY,
        "language": "tr",
        "region": "tr",
        "bounds": bounds_str,
    }

    try:
        response = requests.get(GEOCODING_API_URL_SABITI, params=params, timeout=API_ZAMAN_ASIMI_SABITI)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error("Geocoding API iletişim hatası: %s — '%s'", e, konum_metin)
        return None


def _api_yanit_degerlendir(data: dict, konum_metin: str) -> Optional[Tuple[float, float]]:
    """
    API'den dönen raw JSON verisini ayıklar, durum kodunu denetler ve koordinat doğrulamasını gerçekleştirir.
    (>30 satır kuralına istinaden doğrulama kısmı ayrıştırılmıştır).

    Args:
        data (dict): JSON formatında dönen ham Google Haritalar cevabı.
        konum_metin (str): Loglamak için orjinal istek metni.

    Returns:
        Optional[Tuple[float, float]]: Sağlıklı (lat, lng) verisi, sağlıksızsa None.
    """
    if data.get("status") != "OK":
        logger.warning(
            "Geocoding durumu OK değil — status=%s, adres='%s'",
            data.get("status"),
            konum_metin,
        )
        return None

    results = data.get("results", [])
    if not results:
        logger.warning("Geocoding sonuç içeriği boş: '%s'", konum_metin)
        return None

    try:
        # En tutarlı ilk sonucun (Index 0) lat, lng özelliklerini alıyoruz
        location = results[0]["geometry"]["location"]
        lat = location["lat"]
        lng = location["lng"]

        # Sapma testi: Acaba bulunan mahalle/ilçe farklı bir şehirde mi?
        if not _kocaeli_bolgesinde_mi(lat, lng):
            logger.warning(
                "Kocaeli sınırları dışında bir sonuç saptandı: (%.4f, %.4f) — '%s'",
                lat, lng, konum_metin,
            )
            return None

        logger.info(
            "Geocoding başarıyla doğrulandı: '%s' → (%.6f, %.6f)",
            konum_metin, lat, lng,
        )
        return (lat, lng)

    except (KeyError, IndexError, TypeError) as e:
        logger.error("JSON parse edilirken yapısal bozukluk tespit edildi: %s — '%s'", e, konum_metin)
        return None


def _api_geocode(konum_metin: str) -> Optional[Tuple[float, float]]:
    """
    Google Geocoding API'nin tamamen kapsülleyen yöneticisidir.
    İstek atmayı ve yanıt çözümlemeyi organize eder.

    Args:
        konum_metin (str): Aranacak konum metni (ör. "Yahya Kaptan, İzmit").

    Returns:
        Optional[Tuple[float, float]]: (lat, lon) tuple veya başarısızsa None.
    """
    if not GOOGLE_MAPS_API_KEY:
        logger.error("CRITICAL: GOOGLE_MAPS_API_KEY tanımlı değil. Lütfen .env dosyanızı ayarlayın.")
        return None

    data = _google_api_istegi_yap(konum_metin)
    if not data:
        return None

    return _api_yanit_degerlendir(data, konum_metin)


# ── Ana Dışa Vurulan Sınıflandırma Fonksiyonları ─────────────────────────────


def geocode(konum_metin: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Dışarıdan çağrılan sistemin resmi Geocoding fonksiyonudur. Cache (Önbellek) tabanlı çalışarak
    gegereksiz API maliyetlerini engeller.

    İşlem sırası:
        1. MongoDB 'konum_cache' tablosundan varlığı kontrol edilir. Varsa maliyetsiz dönüş yapar.
        2. Yoksa Google Geocoding API'den gerçek sorgu yapılır.
        3. Sorgu başarılıysa 'konum_cache' tablosuna save atılır.

    Args:
        konum_metin (str): Hedef arama dizesi.

    Returns:
        Tuple[Optional[float], Optional[float]]: Enlem (lat) ve Boylam (lon). Bulunamazsa (None, None).
    """
    if not konum_metin or not konum_metin.strip():
        return (None, None)

    konum_metin = konum_metin.strip()

    # 1. MongoDB Cache (Önbellek) Okuması
    cache_sonuc = konum_cache_getir(konum_metin)
    if cache_sonuc is not None:
        logger.debug("Cache isabeti (MongoDB'den okundu): '%s' → (%.6f, %.6f)", 
                     konum_metin, cache_sonuc["lat"], cache_sonuc["lon"])
        return (cache_sonuc["lat"], cache_sonuc["lon"])

    # 2. Cache bulamadı, maliyetli API çağrısına gir.
    sonuc = _api_geocode(konum_metin)

    if sonuc is None:
        logger.info("Konum harita servisi tarafından çözülemedi: '%s'", konum_metin)
        return (None, None)

    lat, lon = sonuc

    # 3. Öğrenilen yeni adresi DB Önbelleğine yaz, bir daha sormayalım.
    konum_cache_kaydet(konum_metin, lat, lon)
    logger.debug("Yeni lokasyon Önbelleğe (MongoDB) yazıldı: '%s' → (%.6f, %.6f)", konum_metin, lat, lon)

    return (lat, lon)


def toplu_geocode(konum_metinleri: list) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """
    Birden fazla string formatındaki lokasyona paralel/seri şekilde Geocode denetimi uygular.

    Args:
        konum_metinleri (list): Ham dizi lokasyonlar.

    Returns:
        Dict: Metnin kendi adını anahtar, (lat, lon) kümesini değer alan sözlük.
    """
    sonuclar = {}
    for metin in konum_metinleri:
        if metin and metin.strip():
            sonuclar[metin] = geocode(metin)
    return sonuclar


def haber_geocode(haber: dict) -> dict:
    """
    Haber çıkarma botundan gelen tekil ham sözlükteki "konum_metin" alanını okur 
    ve koordinatları çıkartıp sözlük içerisine enjekte eder. Yerinde mutasyon.

    Args:
        haber (dict): Standart haber nesnesi.

    Returns:
        dict: Geocode alanları doldurulmuş haber nesnesi.
    """
    konum_metin = haber.get("konum_metin")

    if not konum_metin:
        haber["konum_lat"] = None
        haber["konum_lon"] = None
        return haber

    lat, lon = geocode(konum_metin)
    haber["konum_lat"] = lat
    haber["konum_lon"] = lon

    return haber


def toplu_haber_geocode(haberler: list) -> list:
    """
    Toplanmış haberler listesini satır satır koordinatlarla günceller.

    Args:
        haberler (list): Dictionary listesi (MongoDB collection şablonu)

    Returns:
        list: Koordinata sahip son hal listesi.
    """
    for haber in haberler:
        haber_geocode(haber)
    return haberler


# ── Modül test akışı ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=" * 60)
    print("GOOGLE MAPS GEOCODING TEST VE CACHE DOĞRULAMASI")
    print("=" * 60)

    if not GOOGLE_MAPS_API_KEY:
        print("\n⚠  GOOGLE_MAPS_API_KEY tanımlı değil, sadece var olan Cache (MongoDB) test edilecek.")
        print("   .env dosyasına aşağıdaki satırı ekleyin:")
        print("   GOOGLE_MAPS_API_KEY=your_api_key_here\n")

    test_konumlar = [
        "Yahya Kaptan Mahallesi, İzmit, Kocaeli",
        "Gebze, Kocaeli",
        "Darıca, Kocaeli",
        "Ankara Caddesi, İzmit, Kocaeli",
        "Kocaeli",
    ]

    for konum in test_konumlar:
        lat, lon = geocode(konum)
        if lat is not None:
            print(f"  ✓ '{konum}' → ({lat:.6f}, {lon:.6f})")
        else:
            print(f"  ✗ '{konum}' → koordinat çözümlenemedi!")

    print(f"\n{'─' * 60}")
    print("JSON Haber Geocode Entegrasyon Testi:")
    test_haber = {
        "baslik": "İzmit'te trafik feci kaza",
        "konum_metin": "İzmit, Kocaeli",
    }
    haber_geocode(test_haber)
    print(f"  Güncellenen konum_lat: {test_haber.get('konum_lat')}")
    print(f"  Güncellenen konum_lon: {test_haber.get('konum_lon')}")
