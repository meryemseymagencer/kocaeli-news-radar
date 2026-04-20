"""
REST API Ana Modülü (FastAPI).
Kocaeli Kentsel Haber İzleme Sistemi — api/main.py

Frontend (Vue/Vanilla JS) tarafının veritabanı ile haberleşmasını sağlar.
Asenkron Motor yapısını kullanarak MongoDB'den verileri çeker ve sunar.
"""

import os
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any

from bson import ObjectId
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

# Ortam değişkenlerini sisteme dahil et
load_dotenv()


# ── Sabitler (Constants) ─────────────────────────────────────────────────────

VARSAYILAN_VERITABANI_ADI_SABITI = "kocaeli_haberler"
VARSAYILAN_MONGO_URI_SABITI = f"mongodb://localhost:27017/{VARSAYILAN_VERITABANI_ADI_SABITI}"

# Bir istekte frontend'e dönebilecek maksimum haber kartı sayısı
MAX_HABER_LIMITI_SABITI = 200

# JavaScript'in "Z" formatındaki ISO stringlerini Python uyumlu Parse etmek için ek
UTC_ZAMAN_DILIMI_SABITI = "+00:00"


# ── Uygulama ve Veritabanı Kurulumu ──────────────────────────────────────────

app = FastAPI(title="Kocaeli Kentsel Haber İzleme Sistemi API")

# Frontend'in API'ye erişebilmesi için uygulanan standart CORS politikası
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB Bağlantısı (Motor ile Asenkron)
MONGO_URI = os.getenv("MONGODB_URI", VARSAYILAN_MONGO_URI_SABITI)
client = AsyncIOMotorClient(MONGO_URI)
db = client.get_default_database(VARSAYILAN_VERITABANI_ADI_SABITI)


# ── Yardımcı Araçlar (Helpers) ───────────────────────────────────────────────


def serialize_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    MongoDB'den gelen kompleks BSON formatındaki '_id' (ObjectId) alanını,
    JSON tarafından okunabilecek string (metin) formatına dönüştürür.

    Args:
        doc (Dict): MongoDB'den çekilen raw veri.

    Returns:
        Dict: JSON serialize edilebilir sözlük (veya Null).
    """
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _haberler_sorgusu_olustur(
    tur: Optional[str] = None,
    ilce: Optional[str] = None,
    baslangic: Optional[str] = None,
    bitis: Optional[str] = None
) -> Dict[str, Any]:
    """
    Kullanıcının REST Endpoint'e gönderdiği GET parametrelerini anlayıp 
    MongoDB'ye doğrudan entegre olabilen Query sözlüğünü kurgular.
    (>30 satır kuralından ötürü controller mantığından soyutlanmıştır)

    Args:
        tur (str): "YANGIN", "TRAFİK_KAZASI" vb.
        ilce (str): "İzmit", "Gebze" kelimeleri.
        baslangic (str): ISO Format Başlangıç Tarihi.
        bitis (str): ISO Format Bitiş Tarihi.

    Returns:
        Dict: Veritabanında `.find(query)` komutuna atanacak filtreler.
    """
    query: Dict[str, Any] = {}
    
    if tur:
        query["haber_turu"] = tur
        
    if ilce:
        # Harf duyarsız (Case Insensitive - "i") regex araması yapar
        query["konum_metin"] = {"$regex": ilce, "$options": "i"}
        
    date_query = {}
    if baslangic:
        try:
            date_query["$gte"] = datetime.fromisoformat(
                baslangic.replace("Z", UTC_ZAMAN_DILIMI_SABITI)
            )
        except ValueError:
            pass

    if bitis:
        try:
            date_query["$lte"] = datetime.fromisoformat(
                bitis.replace("Z", UTC_ZAMAN_DILIMI_SABITI)
            )
        except ValueError:
            pass
            
    if date_query:
        query["yayin_tarihi"] = date_query
        
    return query


# ── REST Endpoints (Router) ──────────────────────────────────────────────────


@app.get("/api/haberler")
async def get_haberler(
    tur: Optional[str] = None,
    ilce: Optional[str] = None,
    baslangic: Optional[str] = None,
    bitis: Optional[str] = None
):
    """
    Tüm haberleri, belirlenen filtrelere (tür, ilçe, tarih vb) uygun şekilde asenkron getirir.

    Returns:
        List[Dict]: Sınırlayıcı(limit) ve sıraya sokulmuş haber kartları listesi. Ağ trafiğini azaltmak 
        için devasa yer kaplayan NLP Embedding array'i ve tam metin içeriği çıkartılmıştır.
    """
    query = _haberler_sorgusu_olustur(tur, ilce, baslangic, bitis)

    # 0 değeri ilgili alanların (embedding ve icerik) indirilmesini engeller
    cursor = db.haberler.find(query, {"embedding": 0, "icerik": 0})\
                        .sort("yayin_tarihi", -1)\
                        .limit(MAX_HABER_LIMITI_SABITI)
                        
    docs = await cursor.to_list(length=MAX_HABER_LIMITI_SABITI)
    
    return [serialize_doc(doc) for doc in docs]


@app.get("/api/haberler/{id}")
async def get_haber(id: str):
    """
    Özel bir MongoDB _id değeriyle bir haberi tüm detayları ile (Örn: InfoWindow Modalı) tam yükler.

    Args:
        id (str): Belge ObjectId kimliği.

    Raises:
        HTTPException: ID formatı bozuksa veya kayıt bulunamadıysa.

    Returns:
        Dict: Tekil haber sözlüğü.
    """
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Geçersiz haber ObjectID formatı tespit edildi.")
        
    doc = await db.haberler.find_one({"_id": ObjectId(id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Haber veritabanında bulunamadı.")
        
    return serialize_doc(doc)


def _arka_plan_scraper_isletici() -> None:
    """
    Kullanıcının başlattığı /scrape emrini Python Subprocess (alt süreç) vasıtası ile
    ana server thread'inden (event loop) tamamen bağımsız çalıştıran işçi (worker).
    """
    try:
        # api/main.py dizinini terk edip bir üst dizindeki 'scraper/main.py' hedefini bul
        isletim_dizini = os.path.dirname(os.path.dirname(__file__))
        scraper_yolu = os.path.join(isletim_dizini, "scraper", "main.py")
        
        subprocess.run(["python", scraper_yolu], check=True)
        print("Sistem Scraper görevi başarıyla icra edildi.")
    except Exception as e:
        print(f"Scraper Subprocess'e gönderilirken çakılma yaşandı: {e}")


@app.post("/api/haberler/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks):
    """
    Manuel web kazıma (scraping) döngüsünü tetikleyen POST rotasıdır.

    Bot kazımaları dakikalar sürebileceği için kullanıcıya anında (HTTP 200) dönüp 
    işlemi FastAPI BackgroundTasks mimarisiyle arkada icra etmeye devam eder.

    Returns:
        Dict: Başarı durumunu belirten minik log.
    """
    background_tasks.add_task(_arka_plan_scraper_isletici)
    return {
        "status": "success", 
        "message": "Scraping botu arka planda asenkron olarak koşmaya başladı."
    }
