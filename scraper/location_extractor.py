"""
Konum Çıkarımı Modülü.
Kocaeli Kentsel Haber İzleme Sistemi — scraper/location_extractor.py

Haber metinlerinden konum bilgisi çıkarır:
  1. Regex ile mahalle, cadde, sokak, ilçe kalıplarını eşleştirerek tespit yapar.
  2. Kocaeli ilçe / mahalle sözlüğü ile doğrulama uygular.
  3. Tespit edilen en detaylı adrese öncelik verir: sokak/cadde > mahalle > ilçe > "Kocaeli" genel.

Uyarı: Konum bulunamazsa None döner ve ilgili haber haritada render edilmez.
"""

import re
from typing import List, Optional, Tuple


# ── Sabitler (Constants) ───────────────────────────────────────────────────

# Sadece Kocaeli'ne ait geçerli ilçe adları
KOCAELI_ILCELERI_SABITI: List[str] = [
    "İzmit", "Gebze", "Darıca", "Çayırova", "Dilovası", "Körfez",
    "Derince", "Gölcük", "Başiskele", "Kartepe", "Kandıra", "Karamürsel",
]

# Küçük harflerle ilçeye ulaşmak için O(1) arama haritası
ILCE_LOWER_MAP_SABITI = {ilce.lower(): ilce for ilce in KOCAELI_ILCELERI_SABITI}

# Bütün ilçe adlarını kapsayan birleştirilmiş Greedy RegExp şablonu (Uzundan kısaya)
ILCE_SABLONU_SABITI = re.compile(
    r"\b(" + "|".join(re.escape(i) for i in sorted(KOCAELI_ILCELERI_SABITI, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Mahalle eşleştirme şablonu (Örn: "Yahya Kaptan Mahallesi", "Yenimahalle Mah.")
MAHALLE_SABLONU_SABITI = re.compile(
    r"((?:[A-ZÇĞİÖŞÜa-zçğıöşü]+\s*){1,4})\s*[Mm]ahallesi|[Mm]ah\.",
    re.UNICODE,
)

# Cadde eşleştirme şablonu (Örn: "Ankara Caddesi")
CADDE_SABLONU_SABITI = re.compile(
    r"((?:[A-ZÇĞİÖŞÜa-zçğıöşü0-9]+\s*){1,4})\s*[Cc]addesi|[Cc]ad\.",
    re.UNICODE,
)

# Sokak eşleştirme şablonu (Örn: "1234. Sokak", "Gül Sk.")
SOKAK_SABLONU_SABITI = re.compile(
    r"((?:[A-ZÇĞİÖŞÜa-zçğıöşü0-9.]+\s*){1,4})\s*[Ss]oka(?:ğı|k)|[Ss]k\.",
    re.UNICODE,
)

# Bulvar eşleştirme şablonu
BULVAR_SABLONU_SABITI = re.compile(
    r"((?:[A-ZÇĞİÖŞÜa-zçğıöşü0-9\-]+\s*){1,4})\s*[Bb]ulvarı|[Bb]lv\.",
    re.UNICODE,
)

# "ilçesi" kelimesiyle biten format şablonu (Örn: "İzmit ilçesi")
ILCE_EKI_SABLONU_SABITI = re.compile(
    r"((?:[A-ZÇĞİÖŞÜa-zçğıöşü]+\s*){1,2})\s*[İi]lçesi",
    re.UNICODE,
)

# Lokasyon bildiren hal eklerine (-de/-da) sahip isimleri yakalama şablonu
DE_DA_SABLONU_SABITI = re.compile(
    r"(" + "|".join(re.escape(i) for i in sorted(KOCAELI_ILCELERI_SABITI, key=len, reverse=True)) + 
    r")['']?(?:de|da|nde|nda|te|ta)\b",
    re.IGNORECASE,
)

# Konum belirleme öncelik skorları (Yüksek puan spesifik lokasyonu garanti eder)
ONCELIK_SOKAK_SABITI = 4
ONCELIK_CADDE_SABITI = 4
ONCELIK_BULVAR_SABITI = 4
ONCELIK_MAHALLE_SABITI = 3
ONCELIK_ILCE_SABITI = 2
ONCELIK_KOCAELI_SABITI = 1


# ── Yardımcı Fonksiyonlar ────────────────────────────────────────────────────


def _temizle(metin: str) -> str:
    """
    Eşleşen ham adres metindeki bozuklukları giderip Title Case formuna çevirir.

    Args:
        metin (str): Ham Regex eşleşmesi.

    Returns:
        str: Baş harfleri büyük, düzgün boşluklu adres bileşeni.
    """
    sonuc = re.sub(r"\s+", " ", metin).strip()
    return sonuc.title()


def _regex_eslesmeleri_bul(metin: str, pattern: re.Pattern, oncelik: int) -> List[Tuple[str, int]]:
    """
    Belirtilen Regex kalıbını metinde arar, eşleşmeleri temizleyerek listeye ekler.

    Args:
        metin (str): Aranacak metin.
        pattern (re.Pattern): Sokak, cadde vb. Regex Constant'ı.
        oncelik (int): Eşleşme grubunun puan değeri.

    Returns:
        List[Tuple[str, int]]: [(Adres_Dizgisi, Puan)] formatında liste.
    """
    sonuclar = []
    for match in pattern.finditer(metin):
        tam_eslesme = match.group(0).strip()
        if tam_eslesme:
            sonuclar.append((_temizle(tam_eslesme), oncelik))
    return sonuclar


def _ilce_eslesmelerini_topla(metin: str, mevcut_adaylar: List[Tuple[str, int]]) -> List[Tuple[str, int]]:
    """
    Metindeki tüm olası ilçe kalıplarını arayıp adaylar listesine benzersiz şekilde ekler.
    (>30 satırlık fonksiyonları bölme kuralı).

    Args:
        metin (str): Haber metni.
        mevcut_adaylar (List): Hali hazırda bulunmuş adres adayları.

    Returns:
        List[Tuple[str, int]]: Eklenen ilçe referanslarıyla genişletilmiş konum adayları.
    """
    adaylar = list(mevcut_adaylar)

    # 1. "İlçesi" olarak bitenler
    for match in ILCE_EKI_SABLONU_SABITI.finditer(metin):
        aday_ilce = match.group(1).strip().lower()
        if aday_ilce in ILCE_LOWER_MAP_SABITI:
            adaylar.append((ILCE_LOWER_MAP_SABITI[aday_ilce], ONCELIK_ILCE_SABITI))

    # 2. "-de/-da" ekleri ile ilçeler
    for match in DE_DA_SABLONU_SABITI.finditer(metin):
        aday_ilce = match.group(1).strip().lower()
        if aday_ilce in ILCE_LOWER_MAP_SABITI:
            adaylar.append((ILCE_LOWER_MAP_SABITI[aday_ilce], ONCELIK_ILCE_SABITI))

    # 3. Yalın halde ilçe ismi geçiyorsa
    for match in ILCE_SABLONU_SABITI.finditer(metin):
        aday_ilce = match.group(1).strip().lower()
        if aday_ilce in ILCE_LOWER_MAP_SABITI:
            orijinal = ILCE_LOWER_MAP_SABITI[aday_ilce]
            # Yinelenen eklemeyi engelle
            if not any(a[0] == orijinal and a[1] == ONCELIK_ILCE_SABITI for a in adaylar):
                adaylar.append((orijinal, ONCELIK_ILCE_SABITI))

    return adaylar


def _tum_adaylari_topla(metin: str) -> List[Tuple[str, int]]:
    """
    Metin üzerinde tüm RegEx şablonlarını sırasıyla çalıştırarak tüm lokasyon
    seçeneklerini tekilleştirmeden döndürür. (Kod tekrarını engeller)

    Args:
        metin (str): Haber içeriği.

    Returns:
        List[Tuple[str, int]]: Olası adres ve skor kombinasyonları.
    """
    adaylar: List[Tuple[str, int]] = []
    
    adaylar.extend(_regex_eslesmeleri_bul(metin, SOKAK_SABLONU_SABITI, ONCELIK_SOKAK_SABITI))
    adaylar.extend(_regex_eslesmeleri_bul(metin, CADDE_SABLONU_SABITI, ONCELIK_CADDE_SABITI))
    adaylar.extend(_regex_eslesmeleri_bul(metin, BULVAR_SABLONU_SABITI, ONCELIK_BULVAR_SABITI))
    adaylar.extend(_regex_eslesmeleri_bul(metin, MAHALLE_SABLONU_SABITI, ONCELIK_MAHALLE_SABITI))
    
    # İlçe aramalarını entegre et
    adaylar = _ilce_eslesmelerini_topla(metin, adaylar)

    # Hiçbir şey bulunamadıysa direkt Kocaeli kelimesini değerlendir
    if not adaylar and re.search(r"\bKocaeli\b", metin, re.IGNORECASE):
        adaylar.append(("Kocaeli", ONCELIK_KOCAELI_SABITI))

    return adaylar


def _ilce_bul(metin: str) -> Optional[str]:
    """
    Metinden ilk eşleşen Kocaeli ilçesini getirir. 
    Daha detaylı adreslerin yanına Google Geocoding doğruluk payını artırmak için eklenir.

    Args:
        metin (str): Lokasyon aranacak Haber içeriği.

    Returns:
        Optional[str]: Kocaeli ilçesi veya None.
    """
    match = ILCE_SABLONU_SABITI.search(metin)
    if match:
        aday = match.group(1).strip().lower()
        if aday in ILCE_LOWER_MAP_SABITI:
            return ILCE_LOWER_MAP_SABITI[aday]
            
    match = DE_DA_SABLONU_SABITI.search(metin)
    if match:
        aday = match.group(1).strip().lower()
        if aday in ILCE_LOWER_MAP_SABITI:
            return ILCE_LOWER_MAP_SABITI[aday]
            
    return None


# ── Ana Fonksiyonlar ─────────────────────────────────────────────────────────


def konum_cikar(metin: str) -> Optional[str]:
    """
    Haber metninden Google Haritalar api sorgusuna gönderilmek üzere 
    en spesifik ve tutarlı konum dizesini çıkarır.

    Öncelik sırası: sokak/cadde/bulvar > mahalle > ilçe > "Kocaeli"

    Args:
        metin (str): Haber başlığı ve içeriği birleştirilmiş kaynak.

    Returns:
        Optional[str]: Hazırlanmış konum dizesi (örn. "Yahya Kaptan Mahallesi, İzmit, Kocaeli") 
        veya lokasyon kestirimi yapılamadıysa None.
    """
    if not metin or not metin.strip():
        return None

    adaylar = _tum_adaylari_topla(metin)
    if not adaylar:
        return None

    # Bulunan referansları öncelik skorlarına göre azalan şekilde sırala
    adaylar.sort(key=lambda x: x[1], reverse=True)
    en_iyi_konum = adaylar[0][0]
    en_iyi_oncelik = adaylar[0][1]

    # Sokak, cadde, mahalle bulduğumuzda haritaların sapmamasını garantilemek için ilçe de iliştiriyoruz
    if en_iyi_oncelik >= ONCELIK_MAHALLE_SABITI:
        ilce = _ilce_bul(metin)
        if ilce and ilce.lower() not in en_iyi_konum.lower():
            return f"{en_iyi_konum}, {ilce}, Kocaeli"
        return f"{en_iyi_konum}, Kocaeli"

    # Yalnızca herhangi bir ilçe bulduysa "Gebze, Kocaeli" şeklinde ayarla
    if en_iyi_oncelik == ONCELIK_ILCE_SABITI:
        return f"{en_iyi_konum}, Kocaeli"

    return en_iyi_konum


def tum_konumlari_cikar(metin: str) -> List[Tuple[str, int]]:
    """
    Haber içerisindeki algılanabilen bütün bağımsız konum verilerini öncelikleriyle döndürür.
    Analiz ve hata testleri amacı ile kullanılır.

    Args:
        metin (str): Analiz edilecek ham metin.

    Returns:
        List[Tuple[str, int]]: Tekrar etmeyen ve önceliğe göre sıralı `(Mekan, Puan)` tuple listesi.
    """
    if not metin:
        return []

    ham_adaylar = _tum_adaylari_topla(metin)

    # Tekrar eden lokasyonları set yardımıyla Case-Insensitive olarak eleyerek temizliyoruz
    gorulenler = set()
    benzersiz = []
    
    for konum, oncelik in sorted(ham_adaylar, key=lambda x: x[1], reverse=True):
        anahtar = konum.lower()
        if anahtar not in gorulenler:
            gorulenler.add(anahtar)
            benzersiz.append((konum, oncelik))

    return benzersiz


# ── Modül Doğrudan Bağımsız Çalıştırılırsa (Test Akışı) ──────────────────────

if __name__ == "__main__":
    test_metinleri = [
        (
            "İzmit Yahya Kaptan Mahallesi'nde trafik kazası",
            "Kocaeli'nin İzmit ilçesinde Yahya Kaptan Mahallesi'nde meydana gelen "
            "trafik kazasında 2 kişi yaralandı. Kaza, Ankara Caddesi üzerinde gerçekleşti."
        ),
        (
            "Gebze'de yangın",
            "Gebze'de bir apartmanın 3. katında çıkan yangın itfaiye ekiplerince söndürüldü."
        ),
        (
            "Darıca Fevzi Çakmak Mahallesi'nde hırsızlık",
            "Darıca Fevzi Çakmak Mahallesi 15. Sokak üzerinde bir eve giren hırsız yakalandı."
        ),
    ]

    print("=" * 70)
    print("KONUM ÇIKARIM TESTİ")
    print("=" * 70)

    for baslik, icerik in test_metinleri:
        birlesik = f"{baslik} {icerik}"
        konum = konum_cikar(birlesik)
        tum = tum_konumlari_cikar(birlesik)

        print(f"\n{'─' * 70}")
        print(f"Başlık : {baslik}")
        print(f"Konum  : {konum or '(bulunamadı)'}")
        if tum:
            print(f"Adaylar: {tum}")
