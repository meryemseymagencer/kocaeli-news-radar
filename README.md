# 📍 Kocaeli Kentsel Haber İzleme Sistemi

Bu proje, Kocaeli yerel haberlerini 5 farklı kaynaktan otomatik olarak çeken, temizleyen, sınıflandıran ve Google Maps üzerinde görselleştiren web scraping tabanlı bir izleme sistemidir. Proje, doğal dil işleme (NLP) teknikleri ile metinlerden konum verisi çıkarır ve makine öğrenmesi modelleriyle haberleri kategorize eder.

## 🚀 Özellikler

* **Otomatik Veri Çekme:** Belirlenen 5 farklı yerel haber sitesinden son 3 günlük haberlerin düzenli olarak toplanması.
* **Akıllı Çift Kayıt (Duplicate) Engelleme:** Sadece URL kontrolü değil, `sentence-transformers` (`paraphrase-multilingual-MiniLM-L12-v2`) modeli kullanılarak haber içeriklerinin embedding benzerliğinin (Cosine Similarity %90 ve üzeri) ölçülmesi. Aynı olay farklı sitelerde haberleştirilmişse sistem bunu tek bir kayıt olarak tutar ve kaynakları birleştirir.
* **Otomatik Kategori Sınıflandırması:** Haberlerin içerik analizine göre "Trafik Kazası", "Yangın", "Elektrik Kesintisi", "Hırsızlık" veya "Kültürel Etkinlikler" olarak öncelik sırasına göre etiketlenmesi.
* **NLP ile Konum Çıkarımı:** `spaCy` kullanılarak metin içerisinden Varlık İsmi Tanıma (NER) yöntemiyle sokak, mahalle ve ilçe gibi lokasyon verilerinin çıkarılması.
* **Geocoding ve Veritabanı Önbellekleme (Caching):** Google Geocoding API ile adreslerin koordinatlara dönüştürülmesi. API maliyetlerini düşürmek ve hızı artırmak için sık kullanılan konumların MongoDB üzerinde önbelleğe alınması (cache).
* **Dinamik Harita Gösterimi:** Haber türüne göre özel renklendirilmiş işaretçilerle olayların Google Maps üzerinde gerçek zamanlı ve filtrelenebilir şekilde gösterilmesi.

## 🛠️ Mimari ve Veri Akışı

Sistem şu boru hattı (pipeline) üzerinden çalışır:
`[Scraper] → [Temizleme] → [Sınıflandırma] → [Konum Çıkarımı] → [Geocoding] → [MongoDB] → [REST API] → [Frontend + Harita]`

## 💻 Teknoloji Yığını (Tech Stack)

* **Veri Kazıma & İşleme (Python):** `requests`, `BeautifulSoup4`, `Selenium`, `spaCy`, `sentence-transformers`.
* **Backend:** REST API mimarisi (FastAPI / Express.js).
* **Veritabanı:** MongoDB (Geospatial sorgular ve metin indeksleme için).
* **Frontend:** Vanilla JS, Google Maps JS API.

## ⚙️ Kurulum ve Çalıştırma

1. Depoyu bilgisayarınıza klonlayın:
```bash
git clone [https://github.com/kullanici-adin/kocaeli-news-scraper.git](https://github.com/kullanici-adin/kocaeli-news-scraper.git)
cd kocaeli-news-scraper

2. Gerekli Python kütüphanelerini yükleyin:

pip install -r requirements.txt

3. Çevresel değişkenleri (.env) ayarlayın:
Proje dizininde bir .env dosyası oluşturun ve aşağıdaki bilgileri kendi API anahtarlarınızla doldurun:
GOOGLE_MAPS_API_KEY=sizin_api_anahtariniz
MONGODB_URI=mongodb://localhost:27017/kocaeli_haberler
PORT=3000

4.Veritabanını ayağa kaldırın ve sistemi başlatın:
python scraper/main.py

📁 Proje Yapısı
├── scraper/
│   ├── main.py          # Ana scraping orkestratörü
│   ├── sources/         # Haber kaynaklarına özel scraper modülleri
│   ├── cleaner.py       # HTML temizleme ve normalizasyon
│   ├── classifier.py    # Kategori etiketleme
│   ├── location_extractor.py # NLP konum çıkarımı
│   └── geocoder.py      # Koordinat dönüştürme ve cache yönetimi
├── api/
│   └── main.py          # REST API Endpoints
├── frontend/
│   ├── index.html
│   ├── app.js           # Harita ve filtreleme mantığı
│   └── style.css
└── db/
    └── mongo.py         # MongoDB bağlantı ve şemaları
