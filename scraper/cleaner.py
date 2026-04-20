"""
Veri Temizleme Modülü.
Kocaeli Kentsel Haber İzleme Sistemi — scraper/cleaner.py

Scraper'lardan gelen ham metin verilerini sıralı adımlarla temizler:
  1. HTML etiketlerini kaldır
  2. Reklam / alakasız blokları çıkar
  3. Fazla boşluk ve satır sonlarını normalize et
  4. Gereksiz özel karakterleri kaldır (noktalama korunur)
  5. Metin normalizasyonu: küçük harf, Türkçe karakter korumalı
"""

import re
from typing import Optional

from bs4 import BeautifulSoup


# ── Sabitler (Constants) ─────────────────────────────────────────────────────

# CSS class / id ile HTML üzerinde aranacak reklam bölgesi şablonları
REKLAM_CLASS_SABITI = re.compile(
    r"reklam|advertisement|advert|ad[-_]?banner|ad[-_]?box|sidebar|"
    r"social[-_]?share|share[-_]?button|related[-_]?news|widget|"
    r"popup|cookie[-_]?banner|newsletter|comment|footer[-_]?links|"
    r"nav(?:igation)?[-_]?bar|breadcrumb|tag[-_]?cloud",
    re.IGNORECASE,
)

# Metin içinde sıkça karşılaşılan ve kazınması istenmeyen reklam / boilerplate cümleleri
REKLAM_CUMLE_KALIPLARI_SABITI = [
    r"devamını\s+oku",
    r"haberin\s+devamı",
    r"reklam\s+alanı",
    r"sponsorlu\s+içerik",
    r"google\s+ads?",
    r"abone\s+ol",
    r"üye\s+ol",
    r"haber\s+bülteni",
    r"paylaş",
    r"tweet(?:le)?",
    r"whatsapp",
    r"facebook",
    r"instagram",
    r"tüm\s+hakları\s+saklıdır",
    r"copyright\s*©",
    r"kaynak\s*:\s*(?:aa|iha|dha|reuters)",
]

# Reklam cümlelerini paragraf içinden temizlemek için pre-compiled Regex
REKLAM_CUMLE_REGEX_SABITI = re.compile(
    r"(?:^|\n)\s*(?:" + "|".join(REKLAM_CUMLE_KALIPLARI_SABITI) + r").*",
    re.IGNORECASE | re.MULTILINE,
)

# Saklanacak karakterler: Alfanümerik, boşluklar, Türkçe harfler ve temel noktalama işaretleri
# Formatı bozan emojiler ve semboller bu regex dışında kalıp silinir
GEREKSIZ_KARAKTER_REGEX_SABITI = re.compile(
    r"[^\w\s"
    r".,;:!?'\"\-–—()\[\]{}"
    r"/%&@#…"
    r"çÇğĞıİöÖşŞüÜâÂîÎûÛ"
    r"]",
    re.UNICODE,
)

# Metnin okunabilirliğini bozan ardışık boşlukları temizlemek için Regex sabitleri
COKLU_BOSLUK_REGEX_SABITI = re.compile(r"[ \t]+")
COKLU_SATIRSONU_REGEX_SABITI = re.compile(r"\n{3,}")

# Türkçe harfleri hatasız küçültmek için Mapping sabiti
TR_UPPER_MAPPING_SABITI = str.maketrans("İIÇĞÖŞÜ", "iıçğöşü")


# ── Adım Fonksiyonları ───────────────────────────────────────────────────────


def html_temizle(metin: str) -> str:
    """
    Adım 1 — Gelen metindeki HTML etiketlerini tamamen kaldırır, saf metin döndürür.
    
    Args:
        metin (str): HTML içeren ham metin.
        
    Returns:
        str: Sadece okunaklı verileri içeren temizlenmiş string.
    """
    if not metin:
        return ""
    
    # BeautifulSoup parse ederek sadece görünen metinleri alıyoruz
    soup = BeautifulSoup(metin, "html.parser")
    return soup.get_text(separator="\n", strip=False)


def _html_etiketlerinden_reklam_temizle(soup: BeautifulSoup) -> None:
    """
    DOM ağacı üzerinden, CSS class veya ID değerlerine bakarak reklam, yorum 
    veya yan panel gibi gereksiz etiketleri hafızada yok eder (decompose).
    (30 satır kuralı - Fonksiyon küçültme).
    """
    # İstenmeyen class isimlerine sahip div'leri sil
    for element in soup.find_all(class_=REKLAM_CLASS_SABITI):
        element.decompose()
        
    # İstenmeyen id değerine sahip div'leri sil
    for element in soup.find_all(id=REKLAM_CLASS_SABITI):
        element.decompose()

    # Tasarımsal ve işlevsel gereksiz tag'leri sil
    for tag in soup.find_all(["script", "style", "iframe", "ins", "noscript"]):
        tag.decompose()


def reklam_bloklari_cikar(html_veya_metin: str) -> str:
    """
    Adım 2 — HTML nesnelerindeki ve saf metin içeresindeki istenmeyen reklam / sidebar bloklarını çıkarır.
    
    Args:
        html_veya_metin (str): HTML etiketli yahut saf metin.
        
    Returns:
        str: Reklamlardan ve sosyal medya butonlarından arındırılmış metin.
    """
    if not html_veya_metin:
        return ""

    # Eğer gelen metnin içinde bariz HTML etiketleri bulunuyorsa DOM ağacını temizle
    if "<" in html_veya_metin and ">" in html_veya_metin:
        soup = BeautifulSoup(html_veya_metin, "html.parser")
        _html_etiketlerinden_reklam_temizle(soup)
        metin = soup.get_text(separator="\n", strip=False)
    else:
        metin = html_veya_metin

    # Metin halindeyken (örn: "devamını oku", "haberin devamı") gereksiz link cümlelerini at
    metin = REKLAM_CUMLE_REGEX_SABITI.sub("", metin)

    return metin


def bosluk_normalize(metin: str) -> str:
    """
    Adım 3 — Fazla boşluk, tab ve art arda gelen satır sonu (\n) karakterlerini normalize eder.
    
    Args:
        metin (str): Temizlenecek metin.
        
    Returns:
        str: Düzgün aralıklara sahip metin.
    """
    if not metin:
        return ""

    # Her satırı ayrı ayrı sağdan/soldan (trim) boşluklardan temizle
    satirlar = metin.split("\n")
    satirlar = [s.strip() for s in satirlar]

    # Temizlenmiş satırları birleştir ve 3'ten fazla \n karakterini daralt
    metin = "\n".join(satirlar)
    metin = COKLU_SATIRSONU_REGEX_SABITI.sub("\n\n", metin)

    # Aynı satırdaki (kelime aralarındaki) fazla boşluk/tab'leri yekpare boşluğa dönüştür
    metin = COKLU_BOSLUK_REGEX_SABITI.sub(" ", metin)

    return metin.strip()


def ozel_karakter_temizle(metin: str) -> str:
    """
    Adım 4 — Metindeki emojileri ve çözülemeyen gereksiz özel sembolleri kaldırır. Noktalamaları korur.
    
    Args:
        metin (str): Kontrol edilecek veri.
        
    Returns:
        str: Harf, rakam ve temel noktalama formatına indirgenmiş metin.
    """
    if not metin:
        return ""
    return GEREKSIZ_KARAKTER_REGEX_SABITI.sub("", metin)


def metin_normalize(metin: str) -> str:
    """
    Adım 5 — NLP (Doğal Dil İşleme) standartlarına uygun olarak metni Türkçe karakter güvenliğiyle küçük harfe çevirir.
    
    Args:
        metin (str): Büyük-küçük harf karışık veri.
        
    Returns:
        str: Sistematik formata gelmiş, tamamen lower() yapılmış veri.
    """
    if not metin:
        return ""

    # Önce Türkçe özel harfleri dönüştür, sonra Python'un genel lower() fonksiyonunu uygula
    sonuc = metin.translate(TR_UPPER_MAPPING_SABITI)
    sonuc = sonuc.lower()

    return sonuc


# ── Ana Pipeline (Sıralı İşlem Akışı) ─────────────────────────────────────────


def temizle(metin: Optional[str]) -> str:
    """
    Sırasıyla 5 temizlik adımını uygulayan ana (Tam temizleme) pipeline fonksiyonudur.

    Adımlar:
        1. HTML tag kaldır
        2. Reklam / alakasız blokları çıkar
        3. Fazla boşluk / satır sonu normalize et
        4. Gereksiz özel karakterleri kaldır
        5. Metin normalizasyonu (küçük harf, Türkçe korumalı)

    Args:
        metin: Ham kaynak metin veya HTML içeriği.

    Returns:
        str: Tamamen temizlenmiş, normalize edilmiş sistem metni. None gelirse "" döner.
    """
    if not metin:
        return ""

    sonuc = metin
    sonuc = html_temizle(sonuc)
    sonuc = reklam_bloklari_cikar(sonuc)
    sonuc = bosluk_normalize(sonuc)
    sonuc = ozel_karakter_temizle(sonuc)
    sonuc = metin_normalize(sonuc)

    return sonuc


def baslik_temizle(baslik: Optional[str]) -> str:
    """
    Başlık alanları için modifiye (Hafif) temizleme fonksiyonudur.
    Başlıklar model sınıflandırması ve Frontend sergilenmesi için okunaklı orjinal vaka formlarında
    (Büyük - küçük harf uyumlu) korunmalıdır. Küçük harfe (lower) dönüştürme atlanır.

    Args:
        baslik (str): Ham manşet veya başlık metni.

    Returns:
        str: Temizlenmiş lakin büyük/küçük harf stili korunmuş başlık.
    """
    if not baslik:
        return ""

    sonuc = baslik
    sonuc = html_temizle(sonuc)
    sonuc = reklam_bloklari_cikar(sonuc)
    sonuc = bosluk_normalize(sonuc)
    sonuc = ozel_karakter_temizle(sonuc)

    # Başlık veri yapısı gereği \n (Satır atlama) karakteri barındırmamalı, tek bir string olmalıdır
    sonuc = sonuc.replace("\n", " ").strip()
    sonuc = COKLU_BOSLUK_REGEX_SABITI.sub(" ", sonuc)

    return sonuc


# ── Modül Doğrudan Bağımsız Çalıştırılırsa (Test Akışı) ──────────────────────

if __name__ == "__main__":
    # Konsol üzerinde çalışan basit test örnekleri
    ornek_html = """
    <div class="article-body">
        <p>Kocaeli'nin İzmit ilçesinde meydana gelen   trafik kazasında
        2 kişi yaralandı.</p>
        <div class="reklam">REKLAM ALANI — Sponsorlu içerik</div>
        <p>Kaza, Ankara Caddesi üzerinde saat 14:30 sıralarında gerçekleşti.  </p>
        <script>var x = 1;</script>
        <div class="social-share">Paylaş: Facebook | Twitter</div>
        <p>   İtfaiye ekipleri olay yerine sevk edildi.   </p>
    </div>
    """

    print("=" * 60)
    print("ADIM ADIM TEMİZLEME TESTİ")
    print("=" * 60)

    adim1 = html_temizle(ornek_html)
    print(f"\n1) HTML temizle:\n---\n{adim1}\n---")

    adim2 = reklam_bloklari_cikar(ornek_html)
    print(f"\n2) Reklam blokları çıkar:\n---\n{adim2}\n---")

    adim3 = bosluk_normalize(adim2)
    print(f"\n3) Boşluk normalize:\n---\n{adim3}\n---")

    adim4 = ozel_karakter_temizle(adim3)
    print(f"\n4) Özel karakter temizle:\n---\n{adim4}\n---")

    adim5 = metin_normalize(adim4)
    print(f"\n5) Metin normalize:\n---\n{adim5}\n---")

    print("\n" + "=" * 60)
    print("TAM PİPELINE (temizle):")
    print("=" * 60)
    print(f"\n{temizle(ornek_html)}")

    print("\n" + "=" * 60)
    print("BAŞLIK TEMİZLE:")
    print("=" * 60)
    test_baslik = "<b>Kocaeli'de Trafik Kazası: </b> 2 Yaralı!!   "
    print(f"Girdi : '{test_baslik}'")
    print(f"Çıktı : '{baslik_temizle(test_baslik)}'")
