"""
MongoDB bağlantı ve CRUD modülü.
Kocaeli Kentsel Haber İzleme Sistemi için veritabanı katmanı.

Koleksiyonlar:
  - haberler    : Tüm haber kayıtları
  - konum_cache : Geocoding sonuç önbelleği
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pymongo import MongoClient, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

# ── .env Yükle ──────────────────────────────────────────────────────────────
# Ortam değişkenlerini .env dosyasından okur
load_dotenv()

# ── Sabitler (Constants) ────────────────────────────────────────────────────
VARSAYILAN_VERITABANI = "kocaeli_haberler"
VARSAYILAN_URI = f"mongodb://localhost:27017/{VARSAYILAN_VERITABANI}"

KOLEKSIYON_HABERLER = "haberler"
KOLEKSIYON_KONUM = "konum_cache"

MONGODB_URI = os.getenv("MONGODB_URI", VARSAYILAN_URI)


# ── Bağlantı Yönetimi ───────────────────────────────────────────────────────

# Global bağlantı nesneleri (Singleton yaklaşımı için)
_client: Optional[MongoClient] = None
_db: Optional[Database] = None


def get_client() -> MongoClient:
    """
    Singleton MongoClient nesnesini döndürür.
    Bağlantı yoksa yeni bir bağlantı oluşturur.
    """
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI)
    return _client


def get_database() -> Database:
    """
    Singleton Database nesnesi döndürür.
    URI'deki veritabanı adını otomatik algılar; yoksa varsayılanı kullanır.
    """
    global _db
    if _db is None:
        client = get_client()
        db_name = client.get_default_database()
        if db_name is not None:
            _db = db_name
        else:
            _db = client[VARSAYILAN_VERITABANI]
    return _db


def close_connection() -> None:
    """
    Açık olan MongoDB bağlantısını güvenli bir şekilde kapatır.
    """
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None


# ── Koleksiyon Erişimçileri ─────────────────────────────────────────────────

def get_haberler_collection() -> Collection:
    """
    Haberler koleksiyonuna erişim sağlar.
    
    Returns:
        Collection: Haberler koleksiyon nesnesi.
    """
    return get_database()[KOLEKSIYON_HABERLER]


def get_konum_cache_collection() -> Collection:
    """
    Konum cache (önbellek) koleksiyonuna erişim sağlar.
    
    Returns:
        Collection: Konum cache koleksiyon nesnesi.
    """
    return get_database()[KOLEKSIYON_KONUM]


# ── İndeks Kurulumu ─────────────────────────────────────────────────────────

def ensure_indexes() -> None:
    """
    Hızlı arama için gerekli MongoDB indekslerini oluşturur (zaten varsa atlar).

    Oluşturulan indeksler:
    - haber_linki  : unique (Tekil)
    - yayin_tarihi : descending (Azalan - son haberler önce)
    - haber_turu   : ascending (Artan)
    - konum_metin  : ascending
    """
    haberler = get_haberler_collection()
    haberler.create_index("haber_linki", unique=True, name="idx_haber_linki_unique")
    haberler.create_index([("yayin_tarihi", DESCENDING)], name="idx_yayin_tarihi")
    haberler.create_index("haber_turu", name="idx_haber_turu")
    haberler.create_index("konum_metin", name="idx_konum_metin")

    konum_cache = get_konum_cache_collection()
    konum_cache.create_index("konum_metin", unique=True, name="idx_konum_metin_unique")


# ── Haber CRUD İşlemleri ────────────────────────────────────────────────────

def haber_ekle(haber: Dict[str, Any]) -> Optional[str]:
    """
    Tek bir haber kaydını veritabanına ekler. Veri bütünlüğünü sağlar.

    Args:
        haber: Haber belgesi. (baslik, icerik, haber_turu, vs.)

    Returns:
        str: Eklenen belgenin ObjectId değeri (string olarak). 
        Eğer kayıt zaten varsa None döner.
    """
    # Eksik olan temel alanları varsayılan değerlerle dolduruyoruz
    haber.setdefault("kaynaklar", [haber.get("site_adi", "")])
    haber.setdefault("konum_metin", None)
    haber.setdefault("konum_lat", None)
    haber.setdefault("konum_lon", None)
    haber.setdefault("yayin_tarihi", None)
    haber.setdefault("embedding", None)
    haber.setdefault("olusturulma_tarihi", datetime.now(timezone.utc))

    try:
        result = get_haberler_collection().insert_one(haber)
        return str(result.inserted_id)
    except DuplicateKeyError:
        # Haberin linki zaten varsa işlem yapmadan geçiyoruz
        return None


def haber_toplu_ekle(haberler: List[Dict[str, Any]]) -> int:
    """
    Birden fazla haberi toplu şekilde db'ye kaydeder. Duplicate olanları atlar.

    Args:
        haberler: Eklenecek haber sözlükleri listesi.

    Returns:
        int: Başarıyla veritabanına kaydedilen haber sayısı.
    """
    eklenen = 0
    for h in haberler:
        if haber_ekle(h) is not None:
            eklenen += 1
    return eklenen


def haber_getir(haber_id: str) -> Optional[Dict[str, Any]]:
    """
    String formatındaki _id değeri ile tek bir haber belgesini getirir.

    Args:
        haber_id: MongoDB ObjectId string metni.

    Returns:
        dict: Haber belgesi veya bulunamazsa None.
    """
    doc = get_haberler_collection().find_one({"_id": ObjectId(haber_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _haber_filtresi_olustur(
    tur: Optional[str] = None,
    ilce: Optional[str] = None,
    baslangic: Optional[datetime] = None,
    bitis: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Filtreleme parametrelerini alıp MongoDB için arama sözlüğüne çevirir.
    Kod tekrarını ve karmaşayı önlemek için ayrılmıştır.

    Returns:
        dict: MongoDB find() sorgusu için kullanılacak filtre nesnesi.
    """
    filtre: Dict[str, Any] = {}

    if tur:
        filtre["haber_turu"] = tur

    if ilce:
        # İlçe ismi tam veya kısmi eşleşmeyi umursamayacak şekilde aranır
        filtre["konum_metin"] = {"$regex": ilce, "$options": "i"}

    if baslangic or bitis:
        tarih_filtre: Dict[str, Any] = {}
        if baslangic:
            tarih_filtre["$gte"] = baslangic
        if bitis:
            tarih_filtre["$lte"] = bitis
        filtre["yayin_tarihi"] = tarih_filtre

    return filtre


def haber_listele(
    tur: Optional[str] = None,
    ilce: Optional[str] = None,
    baslangic: Optional[datetime] = None,
    bitis: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Belirli filtrelere göre veritabanındaki haberleri listeler.
    Üretilen sorguyu azalan tarihe göre sıralayarak döndürür.

    Args:
        tur:        Özel haber türü filtresi.
        ilce:       Konum metni için filtre.
        baslangic:  Başlangıç tarihi limiti.
        bitis:      Bitiş tarihi limiti.

    Returns:
        List[dict]: Bulunan ve dönüştürülen haber belgeleri.
    """
    filtre = _haber_filtresi_olustur(tur, ilce, baslangic, bitis)

    cursor = (
        get_haberler_collection()
        .find(filtre)
        .sort("yayin_tarihi", DESCENDING)
    )

    # Bulunan kayıtların ObjectId değerlerini JSON serileştirmeye uygun hale getir
    sonuclar = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        sonuclar.append(doc)
    return sonuclar


def haber_guncelle(haber_id: str, guncelleme: Dict[str, Any]) -> bool:
    """
    _id'si verilen mevcut haberi kısmi olarak günceller ($set işlemi kullanılarak).

    Args:
        haber_id: Haberin MongoDB id stringi.
        guncelleme: Değiştirilecek key-value eşleşmeleri.

    Returns:
        bool: En az 1 alan başarılı bir şekilde güncellendi mi?
    """
    result = get_haberler_collection().update_one(
        {"_id": ObjectId(haber_id)},
        {"$set": guncelleme},
    )
    return result.modified_count > 0


def haber_kaynak_ekle(haber_linki: str, yeni_kaynak: str) -> bool:
    """
    Habere yeni bir kaynak ekler. Embedding veya başlık tespiti sayesinde 
    aynı haber başka sitede çıktığında kullanılır.

    Args:
        haber_linki: Haberin URL'si.
        yeni_kaynak: Eklenecek yeni haber kaynağının ismi.

    Returns:
        bool: Başarılı şekilde eklendi mi?
    """
    # $addToSet kullanarak duplicate kaynak ismi eklemeyi engelliyoruz
    result = get_haberler_collection().update_one(
        {"haber_linki": haber_linki},
        {"$addToSet": {"kaynaklar": yeni_kaynak}},
    )
    return result.modified_count > 0


def haber_sil(haber_id: str) -> bool:
    """
    _id değerine sahip haberi veritabanından kalıcı olarak siler.

    Returns:
        bool: Haberin başarıyla silinip silinmediği.
    """
    result = get_haberler_collection().delete_one({"_id": ObjectId(haber_id)})
    return result.deleted_count > 0


def haber_link_mevcut_mu(haber_linki: str) -> bool:
    """
    Spesifik bir URL ile kaydedilmiş haber var mı kontrol eder.

    Returns:
        bool: Kayıt bulunuyorsa True.
    """
    # Performans için limit(1) kullanılıyor.
    count = get_haberler_collection().count_documents({"haber_linki": haber_linki}, limit=1)
    return count > 0


def tum_embeddingleri_getir() -> List[Dict[str, Any]]:
    """
    Similarity (benzerlik) hesaplamaları için embedding değeri olan 
    tüm kayıtları RAM'e çekmeye yarayan fonksiyon.

    Returns:
        List[dict]: "_id", "baslik", "haber_linki", "embedding" içeren kayıtlar.
    """
    cursor = get_haberler_collection().find(
        {"embedding": {"$ne": None}},
        {"_id": 1, "baslik": 1, "haber_linki": 1, "embedding": 1, "kaynaklar": 1},
    )
    
    sonuclar = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        sonuclar.append(doc)
    return sonuclar


# ── Konum Cache İşlemleri ───────────────────────────────────────────────────

def konum_cache_getir(konum_metin: str) -> Optional[Dict[str, float]]:
    """
    Adres çözmeyi hızlandırmak için Google Geocoding sonucunu önbellekten çeker.

    Args:
        konum_metin: Aranacak doğal dil mahalle/ilçe metni.

    Returns:
        dict: {"lat": float, "lon": float} ya da bulunamazsa None.
    """
    doc = get_konum_cache_collection().find_one({"konum_metin": konum_metin})
    if doc:
        return {"lat": doc["lat"], "lon": doc["lon"]}
    return None


def konum_cache_kaydet(konum_metin: str, lat: float, lon: float) -> bool:
    """
    Yeni çözülmüş bir adresi, ileride tekrar API kullanılmaması için kaydeder.

    Returns:
        bool: Başarıyla cache'e yazıldı mı? (Duplicate ise False döner)
    """
    try:
        get_konum_cache_collection().insert_one({
            "konum_metin": konum_metin,
            "lat": lat,
            "lon": lon,
        })
        return True
    except DuplicateKeyError:
        # Aynı konum zaten cache'lenmişse hata atmasını engelliyoruz
        return False


# ── Analiz Yardımcı İşlemler ────────────────────────────────────────────────

def istatistikler() -> Dict[str, Any]:
    """
    Sistemin durumu hakkında özet sayısal analizleri döndürür.

    Returns:
        dict: Toplam haberler, boş konumları olanlar ve türlerin dağılım tablosu.
    """
    haberler = get_haberler_collection()
    cache_kol = get_konum_cache_collection()
    
    # Haber türlerine göre grup oluşturarak sayma pipeline'ı
    pipeline = [
        {"$group": {"_id": "$haber_turu", "sayi": {"$sum": 1}}},
        {"$sort": {"sayi": -1}},
    ]
    tur_dagilimi = {doc["_id"]: doc["sayi"] for doc in haberler.aggregate(pipeline)}

    return {
        "toplam_haber": haberler.count_documents({}),
        "tur_dagilimi": tur_dagilimi,
        "konumlu_haber": haberler.count_documents({"konum_lat": {"$ne": None}}),
        "konumsuz_haber": haberler.count_documents({"konum_lat": None}),
        "cache_kayit": cache_kol.count_documents({}),
    }


# ── Modül Bağımsız Çalıştırılırsa (Test Akışı) ──────────────────────────────

if __name__ == "__main__":
    print("MongoDB bağlantısı test ediliyor...")
    try:
        istek = get_client()
        istek.admin.command("ping")
        print("✓ MongoDB bağlantısı başarılı!")

        ensure_indexes()
        print("✓ İndeksler oluşturuldu veya doğrulandı.")

        stats = istatistikler()
        print(f"✓ Veritabanı durumu özeti: {stats}")

    except Exception as e:
        print(f"✗ Veritabanıyla iletişimde hata oluştu: {e}")
    finally:
        close_connection()
        print("Test tamamlandı, bağlantı kapatıldı.")
