"""
Yeni Kocaeli (yenikocaeli.com) haber scraper modülü.
Son 3 günlük haberleri çeker ve MongoDB'ye kaydeder.

Not: yenikocaeli.com farklı bir CMS kullanır. URL yapısı:
  /haber/{kategori}/{slug}/{id}.html
Tarih formatı Türkçe ay isimleri içerir (ör. "21 Mart 2026 13:40").
"""

import json
import locale
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Proje kök dizinini import path'e ekle
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from db.mongo import haber_ekle, haber_link_mevcut_mu, ensure_indexes

# ── Sabitler ─────────────────────────────────────────────────────────────────

BASE_URL = "https://yenikocaeli.com"
SITE_ADI = "yenikocaeli.com"

# Haber linkleri toplanacak sayfalar
KAYNAK_SAYFALAR = [
    BASE_URL + "/",
    BASE_URL + "/haberleri/guncel",
    BASE_URL + "/haberleri/polis-adliye",
    BASE_URL + "/haberleri/siyaset",
    BASE_URL + "/haberleri/ekonomi",
    BASE_URL + "/haberleri/spor",
    BASE_URL + "/haberleri/yasam",
    BASE_URL + "/haberleri/egitim",
    BASE_URL + "/haberleri/saglik",
    BASE_URL + "/haberleri/kultur-sanat",
    BASE_URL + "/haberleri/cevre",
]

# Geçerli haber URL kalıpları
# Örnek: https://yenikocaeli.com/haber/polis-adliye/cadde-ortasinda-dehset/193408.html
HABER_URL_PATTERN = re.compile(
    r"^https?://yenikocaeli\.com/haber/[^/]+/[^/]+/\d+\.html"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Türkiye saat dilimi (UTC+3)
TZ_TR = timezone(timedelta(hours=3))

# İstekler arası bekleme (saniye) — siteyi yormamak için
ISTEK_BEKLEME = 1.0

# Türkçe ay isimleri eşleştirmesi (locale bağımsız çalışsın)
TR_AY_MAP = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4,
    "mayıs": 5, "haziran": 6, "temmuz": 7, "ağustos": 8,
    "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}

logger = logging.getLogger(__name__)


# ── Yardımcı Fonksiyonlar ────────────────────────────────────────────────────

def _fetch(url: str, timeout: int = 20) -> Optional[BeautifulSoup]:
    """URL'yi çeker ve BeautifulSoup nesnesi döndürür.

    Not: yenikocaeli.com bazen yavaş yanıt veriyor, timeout biraz yüksek.
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return BeautifulSoup(response.text, "html.parser")
    except requests.RequestException as e:
        logger.warning("Sayfa çekilemedi: %s → %s", url, e)
        return None


def _toplam_gun_hesapla() -> datetime:
    """Şu andan 3 gün öncesinin başlangıç anını döndürür."""
    simdi = datetime.now(TZ_TR)
    return (simdi - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)


def _tarih_parse(tarih_str: Optional[str]) -> Optional[datetime]:
    """Tarih string'ini datetime'a çevirir.

    Desteklenen formatlar:
        - ISO 8601: "2026-03-21T13:40:00+03:00"
        - Türkçe:   "21 Mart 2026 13:40"
        - Noktalı:  "21.03.2026 13:40"
    """
    if not tarih_str:
        return None

    tarih_str = tarih_str.strip()

    # 1) ISO 8601
    try:
        dt = datetime.fromisoformat(tarih_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_TR)
        return dt
    except (ValueError, TypeError):
        pass

    # 2) Klasik formatlar
    for fmt in ["%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S%z"]:
        try:
            return datetime.strptime(tarih_str, fmt).replace(tzinfo=TZ_TR)
        except (ValueError, TypeError):
            continue

    # 3) Türkçe ay isimli format: "21 Mart 2026 13:40" veya "21 Mart 2026"
    tr_match = re.match(
        r"(\d{1,2})\s+([A-Za-zÇçĞğİıÖöŞşÜü]+)\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?",
        tarih_str,
    )
    if tr_match:
        gun = int(tr_match.group(1))
        ay_str = tr_match.group(2).lower()
        yil = int(tr_match.group(3))
        saat = int(tr_match.group(4)) if tr_match.group(4) else 0
        dakika = int(tr_match.group(5)) if tr_match.group(5) else 0

        ay = TR_AY_MAP.get(ay_str)
        if ay:
            try:
                return datetime(yil, ay, gun, saat, dakika, tzinfo=TZ_TR)
            except ValueError:
                pass

    return None


def _normalize_url(href: str) -> Optional[str]:
    """Göreceli ya da mutlak URL'yi normalize eder."""
    if not href:
        return None
    url = urljoin(BASE_URL, href.strip())
    if HABER_URL_PATTERN.match(url):
        # Fragment ve query parametrelerini temizle
        url = url.split("?")[0].split("#")[0]
        return url
    return None


# ── Link Toplama ─────────────────────────────────────────────────────────────

def haber_linklerini_topla() -> List[str]:
    """Kaynak sayfalardan tüm benzersiz haber linklerini toplar."""
    linkler: set = set()

    for sayfa_url in KAYNAK_SAYFALAR:
        logger.info("Link taranıyor: %s", sayfa_url)
        soup = _fetch(sayfa_url)
        if soup is None:
            continue

        for a_tag in soup.find_all("a", href=True):
            url = _normalize_url(a_tag["href"])
            if url:
                linkler.add(url)

        time.sleep(ISTEK_BEKLEME * 0.5)  # Linkleri toplarken daha kısa bekle

    logger.info("Toplam %d benzersiz haber linki bulundu.", len(linkler))
    return sorted(linkler)


# ── Haber Detay Çekme ────────────────────────────────────────────────────────

def haber_detay_cek(url: str) -> Optional[Dict[str, Any]]:
    """Tek bir haber sayfasından detay bilgileri çeker.

    Returns:
        Haber dict veya None (çekilemezse / tarih dışındaysa).
    """
    soup = _fetch(url)
    if soup is None:
        return None

    baslik = None
    icerik = None
    yayin_tarihi = None

    # ── 1) JSON-LD'den veri çek (en güvenilir) ──────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string
            if not raw:
                continue
            data = json.loads(raw)

            # Bazen @graph içinde olabilir
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") in (
                        "NewsArticle", "Article", "WebPage", "BlogPosting",
                    ):
                        data = item
                        break
                else:
                    continue

            if isinstance(data, dict) and data.get("@type") in (
                "NewsArticle", "Article", "WebPage", "BlogPosting",
            ):
                baslik = baslik or data.get("headline")
                icerik = icerik or data.get("articleBody")
                yayin_tarihi = yayin_tarihi or _tarih_parse(
                    data.get("datePublished") or data.get("dateCreated")
                )
        except (json.JSONDecodeError, TypeError):
            continue

    # ── 2) HTML'den fallback ─────────────────────────────────────────────────

    # Başlık
    if not baslik:
        h1 = soup.find("h1")
        if h1:
            baslik = h1.get_text(strip=True)

    if not baslik:
        og_title = soup.find("meta", property="og:title")
        if og_title:
            baslik = og_title.get("content", "").strip()

    # Tarih — yenikocaeli.com'da tarih genellikle <time> etiketi veya
    # metin içinde Türkçe formatta bulunur
    if not yayin_tarihi:
        time_el = soup.find("time")
        if time_el:
            yayin_tarihi = _tarih_parse(
                time_el.get("datetime") or time_el.get_text(strip=True)
            )

    if not yayin_tarihi:
        meta_date = soup.find("meta", property="article:published_time")
        if meta_date:
            yayin_tarihi = _tarih_parse(meta_date.get("content"))

    # Tarih — sayfadaki metin içinden ara (Türkçe ay isimli format)
    if not yayin_tarihi:
        tarih_pattern = re.compile(
            r"\d{1,2}\s+(?:Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|"
            r"Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s+\d{4}"
            r"(?:\s+\d{1,2}:\d{2})?",
            re.IGNORECASE,
        )
        # Tüm metin içinde ara
        page_text = soup.get_text()
        tarih_match = tarih_pattern.search(page_text)
        if tarih_match:
            yayin_tarihi = _tarih_parse(tarih_match.group())

    # İçerik — yenikocaeli.com'da article-content veya itemprop=articleBody
    if not icerik:
        # itemprop="articleBody" dene
        article_body = soup.find(attrs={"itemprop": "articleBody"})
        if not article_body:
            # class ile dene
            article_body = soup.find("div", class_=re.compile(
                r"article[_-]?content|news[_-]?content|content[_-]?text|haber[_-]?icerik",
                re.IGNORECASE,
            ))
        if not article_body:
            # <article> etiketi ara
            article_body = soup.find("article")

        if article_body:
            # Reklam / sidebar elementlerini çıkar
            for unwanted in article_body.find_all(
                class_=re.compile(
                    r"reklam|advertisement|sidebar|social|share|related|banner|widget|"
                    r"comment|yorum|tag|etiket|breadcrumb",
                    re.IGNORECASE,
                )
            ):
                unwanted.decompose()
            # Script ve style etiketlerini çıkar
            for tag in article_body.find_all(["script", "style", "iframe", "ins", "nav"]):
                tag.decompose()
            icerik = article_body.get_text(separator="\n", strip=True)

    if not icerik:
        # Fallback: og:description
        og_desc = soup.find("meta", property="og:description")
        if og_desc:
            icerik = og_desc.get("content", "").strip()

    # ── 3) Doğrulama ────────────────────────────────────────────────────────
    if not baslik:
        logger.warning("Başlık bulunamadı: %s", url)
        return None

    if not icerik:
        logger.warning("İçerik bulunamadı: %s", url)
        return None

    return {
        "baslik": baslik.strip(),
        "icerik": icerik.strip(),
        "yayin_tarihi": yayin_tarihi,
        "site_adi": SITE_ADI,
        "haber_linki": url,
        "kaynaklar": [SITE_ADI],
    }


# ── Ana Scraping Fonksiyonu ──────────────────────────────────────────────────

def scrape() -> Dict[str, int]:
    """Yeni Kocaeli sitesini scrape eder.

    Returns:
        { "toplam_link": int, "atlanan_mevcut": int,
          "atlanan_tarih": int, "atlanan_hata": int, "eklenen": int }
    """
    logger.info("═══ Yeni Kocaeli scraping başlatılıyor ═══")

    ensure_indexes()
    esik_tarih = _toplam_gun_hesapla()

    istatistik = {
        "toplam_link": 0,
        "atlanan_mevcut": 0,
        "atlanan_tarih": 0,
        "atlanan_hata": 0,
        "eklenen": 0,
    }

    # 1. Linkleri topla
    linkler = haber_linklerini_topla()
    istatistik["toplam_link"] = len(linkler)

    # 2. Her haber için detay çek ve kaydet
    for i, link in enumerate(linkler, 1):
        # Duplicate URL kontrolü (DB'de zaten var mı?)
        if haber_link_mevcut_mu(link):
            logger.debug("[%d/%d] Zaten mevcut, atlanıyor: %s", i, len(linkler), link)
            istatistik["atlanan_mevcut"] += 1
            continue

        # Haber detaylarını çek
        haber = haber_detay_cek(link)
        if haber is None:
            istatistik["atlanan_hata"] += 1
            continue

        # Son 3 gün filtresi
        if haber["yayin_tarihi"] is not None and haber["yayin_tarihi"] < esik_tarih:
            logger.debug(
                "[%d/%d] Tarih dışı (%s), atlanıyor: %s",
                i, len(linkler), haber["yayin_tarihi"].date(), link,
            )
            istatistik["atlanan_tarih"] += 1
            continue

        # MongoDB'ye kaydet
        result = haber_ekle(haber)
        if result:
            logger.info(
                "[%d/%d] ✓ Eklendi: %s", i, len(linkler), haber["baslik"][:60]
            )
            istatistik["eklenen"] += 1
        else:
            istatistik["atlanan_mevcut"] += 1

        # Siteyi yormamak için bekle
        time.sleep(ISTEK_BEKLEME)

    logger.info(
        "═══ Yeni Kocaeli scraping tamamlandı ═══\n"
        "  Toplam link  : %d\n"
        "  Eklenen      : %d\n"
        "  Mevcut (atl.): %d\n"
        "  Tarih dışı   : %d\n"
        "  Hata         : %d",
        istatistik["toplam_link"],
        istatistik["eklenen"],
        istatistik["atlanan_mevcut"],
        istatistik["atlanan_tarih"],
        istatistik["atlanan_hata"],
    )
    return istatistik


# ── Modül doğrudan çalıştırılırsa ───────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    sonuc = scrape()
    print(f"\nSonuç: {sonuc}")
