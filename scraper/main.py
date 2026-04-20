"""
Ana Haber Scraping ve İşleme Orkestratörü.
Kocaeli Kentsel Haber İzleme Sistemi — scraper/main.py

Bu modül şu adımları sırayla işletir:
1. Tüm 5 haber kaynağından son 3 günlük haberleri çeker.
2. Metin temizleme (cleaner) uygular.
3. Haber türü sınıflandırması (classifier) yapar.
4. Konum çıkarımı ve geocoding işlemlerini uygular.
5. Embedding (vektörleştirme) üretip cross-source duplicate (farklı sitelerdeki aynı haber) kontrolü yapar. Eşik değer: %90 benzerlik.
6. İşlenen paketi MongoDB veritabanına kaydeder.
"""

import logging
import sys
import time
from typing import Any, Dict, List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

# Proje dizinini (yazlab2_1) import path'e ekleyerek tam uyumluluk sağla
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

# Modül importları (Veritabanı işlemleri ve İşlem modülleri)
from db.mongo import ensure_indexes, haber_ekle, haber_kaynak_ekle, tum_embeddingleri_getir, haber_link_mevcut_mu
from scraper import cleaner, classifier, location_extractor, geocoder
from scraper.sources import (
    cagdaskocaeli,
    ozgurkocaeli,
    seskocaeli,
    yenikocaeli,
    bizimyaka,
)

# ── Loglama Ayarları ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scraper.main")


# ── Sabitler ve Modeller (Constants) ─────────────────────────────────────────

# Ağırlıklı arama için Türkçe destekli NLP embedding modelinin HuggingFace repo ismi
EMBEDDING_MODEL_ISMI_SABITI = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_model = None  # Singleton Lazy Loading için hafıza referansı

# Aynı haberi saptama için gereken Cosine Similarity yüzdesi (0.90 = %90 Benzerlik)
BENZERLIK_ESIGI_SABITI = 0.90


# ── NLP ve Vektörleştirme Operasyonları ──────────────────────────────────────


def _sinir_agi_modelini_getir() -> SentenceTransformer:
    """
    Embedding (Vektörel Dönüşüm) modelini RAM'e yükleyerek Singleton (Tekil) referans oluşturur.
    
    Returns:
        SentenceTransformer: Cümle vektörleştirici Transformer modeli.
    """
    global _model
    if _model is None:
        logger.info("Dünya standartlarında NLP Embedding modeli yükleniyor: %s", EMBEDDING_MODEL_ISMI_SABITI)
        logger.info("İlk çalışma anında model verileri indirilebilir, lütfen bekleyin...")
        _model = SentenceTransformer(EMBEDDING_MODEL_ISMI_SABITI)
        logger.info("Embedding NLP modeli başarıyla kullanıma hazır!")
    return _model 


def _vektor_embedding_olustur(baslik: str, icerik: str) -> List[float]:
    """
    Parametre olarak gönderilen haber detaylarını sayısal vektör dizisine çevirir.
    (Makine benzerlik algısı için başlık komple alınır, içeriğin ise 500 karakteri yeterlidir).

    Args:
        baslik (str): Haber manşeti.
        icerik (str): Haber metni.

    Returns:
        List[float]: Sayılardan oluşan NLP vektörü.
    """
    model = _sinir_agi_modelini_getir()
    metin = f"{baslik}. {icerik[:500]}"
    vector = model.encode(metin)
    return vector.tolist()


def _cifte_kayit_tespit_et(yeni_embedding: List[float], mevcut_embeddingler: List[Dict]) -> Optional[str]:
    """
    Sklearn yardımıyla Matrix formundaki eski haber vektörleriyle, yeni gelen haber vektörünü
    Cosine Similarity matematik formülünü uygulatarak hızla karşılaştırır. BENZERLIK_ESIGI_SABITI'ni
    aşan kayıt varsa onun Orijinal Linkini döner.

    Args:
        yeni_embedding (List[float]): Kontrol edilecek veri matrisi.
        mevcut_embeddingler (List[Dict]): Veritabanından gelen veya o an scrape edilmiş geçmiş matrisler.

    Returns:
        Optional[str]: Eşleşen link varsa string olarak, yoksa None.
    """
    if not mevcut_embeddingler or not yeni_embedding:
        return None

    # Yeni vektörü 2 boyutlu array formuna evir (1, N matrisi)
    yeni_matris = np.array(yeni_embedding).reshape(1, -1)
    
    # Mevcut vektörleri alt alta toplu 2 boyutlu array yap (M, N matrisi)
    gecmis_matrisler = np.array([kayit["embedding"] for kayit in mevcut_embeddingler])

    # İki küme arasındaki benzerlik korelasyonunu hesapla
    benzerlikler = cosine_similarity(yeni_matris, gecmis_matrisler)[0]

    # Ortaya çıkanlardan en yüksek (Tepe noktası) olasılığa sahip skoru seç
    max_idx = np.argmax(benzerlikler)
    max_skor = benzerlikler[max_idx]

    if max_skor >= BENZERLIK_ESIGI_SABITI:
        eslesen_link = mevcut_embeddingler[max_idx]["haber_linki"]
        logger.info(
            " Duplicate (Kopya) tespit edildi! Skor: %.2f (Eşik: %.2f) -> %s",
            max_skor, BENZERLIK_ESIGI_SABITI, eslesen_link
        )
        return eslesen_link

    return None


# ── Modüler Veri İşleme Birimleri ────────────────────────────────────────────


def _tekil_haberi_isle_ve_kaydet(
    ham_haber: Dict[str, Any], link: str, kaynak_adi: str,
    mevcut_embeddingler: List[Dict], istatistik: Dict[str, int]
) -> None:
    """
    Arka arkaya 4-5 farklı modülü koşturarak ham HTML metnini akıllı veriye (Temiz, Sınıflandırılmış,
    Lokasyonlanmış, Kopyasız) dönüştürür ve MongoDB'ye yazar. (>30 satır engeli ve SRP kuralı için bölünmüştür).

    Args:
        ham_haber (Dict): Spider'dan (scraper/sources) dönen veriler.
        link (str): Kaynak adresi.
        kaynak_adi (str): Medya firması adı.
        mevcut_embeddingler (List): Kapsamdaki eşleştirme cache verisi.
        istatistik (Dict): Raporlama arşivi referansı.
    """
    # Adım 1 - Metin Temizliği (HTML ve Reklam Arındırma)
    temiz_baslik = cleaner.baslik_temizle(ham_haber["baslik"])
    temiz_icerik = cleaner.temizle(ham_haber["icerik"])
    
    ham_haber["baslik"] = temiz_baslik
    ham_haber["icerik"] = temiz_icerik

    # Adım 2 - Haber Tipi Modeli (Trafik Kazası, Yangın vb.)
    ham_haber["haber_turu"] = classifier.siniflandir(temiz_baslik, temiz_icerik)
    
    # Adım 3 - Konum ve Geocoding Entegrasyonu
    birlesik_metin = f"{temiz_baslik} {temiz_icerik}"
    konum_metni = location_extractor.konum_cikar(birlesik_metin)
    ham_haber["konum_metin"] = konum_metni

    if konum_metni:
        lat, lon = geocoder.geocode(konum_metni)
        ham_haber["konum_lat"], ham_haber["konum_lon"] = lat, lon
    else:
        ham_haber["konum_lat"], ham_haber["konum_lon"] = None, None

    # Adım 4 - NLP Benzerlik Kontrolü ve DB Kayıt
    yeni_emb = _vektor_embedding_olustur(temiz_baslik, temiz_icerik)
    ham_haber["embedding"] = yeni_emb
    
    eslesen_link = _cifte_kayit_tespit_et(yeni_emb, mevcut_embeddingler)
    if eslesen_link:
        logger.info(" [%s] Haberi '%s' ile cross-source eşleşti. Kaynak havuzuna ekleniyor.", kaynak_adi, eslesen_link)
        haber_kaynak_ekle(eslesen_link, kaynak_adi)
        istatistik["atlanan_cross_source"] += 1
        return
        
    # Benzersiz yepyeni data! MongoDB'ye Ekle.
    result_id = haber_ekle(ham_haber)
    if result_id:
        logger.info(" ✓ Veritabanına temiz şekilde eklendi: %s", temiz_baslik[:60])
        istatistik["eklenen"] += 1
        # Olası aynı veriler girmesin diye RAM Vector önbelleğini yenile
        mevcut_embeddingler.append({
            "haber_linki": link,
            "embedding": yeni_emb,
            "kaynaklar": ham_haber["kaynaklar"],
        })
    else:
        istatistik["atlanan_mevcut_url"] += 1


# ── Ana Orkestratör (Veri Toplayıcı Arayüz) ──────────────────────────────────


def _kaynak_botunu_calistir(kaynak_modulu: Any, kaynak_adi: str, mevcut_embeddingler: List[Dict]) -> Dict[str, int]:
    """
    Hedef Gazete / Medya modülünü tetikleyerek haber sarmalını başlatır. İstisna
    atılan (tarihi eski / URL duplicate etc) haberleri direkt rededer; taze verileri
    işleyici fonksiyon olan `_tekil_haberi_isle_ve_kaydet` metoduna teslim eder.

    Args:
        kaynak_modulu (Any): Hedef modül (örn. cagdaskocaeli).
        kaynak_adi (str): Loglama yapmak adına string ismi.
        mevcut_embeddingler (List): Geçmiş NLP uzay verileri.

    Returns:
        Dict: Modül kapanış raporu istatistiği.
    """
    logger.info("--- %s kaynağı için Scraper süreci başlıyor ---", kaynak_adi)
    
    istatistik = {
        "toplam_link": 0, "atlanan_mevcut_url": 0, "atlanan_tarih": 0,
        "atlanan_hata": 0, "atlanan_cross_source": 0, "eklenen": 0,
    }

    esik_tarih = kaynak_modulu._toplam_gun_hesapla()
    linkler = kaynak_modulu.haber_linklerini_topla()
    istatistik["toplam_link"] = len(linkler)

    for i, link in enumerate(linkler, 1):
        if haber_link_mevcut_mu(link):
            logger.debug("[%d/%d] URL zaten mevcut, atlanıyor: %s", i, len(linkler), link)
            istatistik["atlanan_mevcut_url"] += 1
            continue
            
        ham_haber = kaynak_modulu.haber_detay_cek(link)
        if ham_haber is None:
            istatistik["atlanan_hata"] += 1
            continue
            
        if ham_haber["yayin_tarihi"] is not None and ham_haber["yayin_tarihi"] < esik_tarih:
            logger.debug("[%d/%d] Tarih sınırı (Zaman Aşımı), atlanıyor: %s", i, len(linkler), link)
            istatistik["atlanan_tarih"] += 1
            continue

        logger.info("[%d/%d] NLP/Cleaning safhasına geçiliyor: %s", i, len(linkler), ham_haber["baslik"][:60])
        
        # Kompleks haber işleme pipeline methodunu çapır
        _tekil_haberi_isle_ve_kaydet(ham_haber, link, kaynak_adi, mevcut_embeddingler, istatistik)
        time.sleep(kaynak_modulu.ISTEK_BEKLEME)
        
    logger.info("--- %s sitesi bitti. Rapor: %s ---", kaynak_adi, istatistik)
    return istatistik


def tum_haberleri_baslat() -> None:
    """Sırayla tüm (5 adet) medya kaynaklarında gezinerek veritabanını yeni haberlerle günceller."""
    logger.info("=" * 60)
    logger.info(" KOCAELİ KENTSEL HABER İZLEME SİSTEMİ ORKESTRATÖRÜ ÇALIŞTIRILIYOR ")
    logger.info("=" * 60)

    logger.info("Veritabanı indeksleri güvenceye alınıyor...")
    ensure_indexes()

    kaynaklar_listesi = [
        ("cagdaskocaeli.com.tr", cagdaskocaeli),
        ("ozgurkocaeli.com.tr", ozgurkocaeli),
        ("seskocaeli.com", seskocaeli),
        ("bizimyaka.com", bizimyaka),
        ("yenikocaeli.com", yenikocaeli),
    ]

    logger.info("MongoDB'den geçmiş Cross-Source Duplicate (Embedding) kayıtları Getiriliyor...")
    mevcut_embeddingler = tum_embeddingleri_getir()
    logger.info("%d adet farklı vektör referansı (RAM'e) başarıyla indüklendi.", len(mevcut_embeddingler))

    toplam = {
        "toplam_link": 0, "atlanan_mevcut_url": 0, "atlanan_tarih": 0,
        "atlanan_hata": 0, "atlanan_cross_source": 0, "eklenen": 0,
    }

    for isim, modul in kaynaklar_listesi:
        logger.info("\nAktif İstasyon: %s", isim)
        try:
            rapor = _kaynak_botunu_calistir(modul, isim, mevcut_embeddingler)
            # Dönen listeyi global total'e ekle
            for k, v in rapor.items():
                toplam[k] += v
        except Exception as hata:
            logger.error("'%s' kaynağı kritikal bir bozulma yaşadı: %s", isim, hata, exc_info=True)

    logger.info("\n" + "=" * 60)
    logger.info(" TÜM SCRAPER BOTLARI VE HABER AKIŞI TAMAMLANDI ")
    logger.info(" Genel İşlem Skoru:")
    for key, val in toplam.items():
        logger.info("  %s : %s", key, val)
    logger.info("=" * 60)


# ── Modül Doğrudan Bağımsız Çalıştırılırsa (Test Akışı) ──────────────────────

if __name__ == "__main__":
    # Konsola test maksatlı başlatım verildiğinde otonom olarak devreye gir
    tum_haberleri_baslat()
