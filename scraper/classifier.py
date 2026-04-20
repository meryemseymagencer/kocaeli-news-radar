"""
Haber Türü Sınıflandırma Modülü.
Kocaeli Kentsel Haber İzleme Sistemi — scraper/classifier.py

Haberleri başlık + içerik analizi ile 5 kategoriden birine otomatik sınıflandırır.

Haber Türleri (öncelik sırasıyla):
  1. TRAFİK_KAZASI
  2. YANGIN
  3. ELEKTRİK_KESİNTİSİ
  4. HIRSIZLIK
  5. KÜLTÜREL_ETKİNLİK

Sınıflandırma kuralları:
  - Anahtar kelime eşleşmesi (case-insensitive, Türkçe destekli)
  - Başlık eşleşmelerine belirlenen sabit çarpanla ağırlık verilir
  - Birden fazla tür eşleşirse öncelik sırası uygulanır
  - Hiçbir tür eşleşmezse None döner
"""

import re
from typing import Any, Dict, List, Optional


# ── Sabitler (Constants) ───────────────────────────────────────────────────

# Veritabanına kaydedilecek standart haber türü adları (Enum benzeri yapı)
TRAFIK_KAZASI_SABITI = "TRAFİK_KAZASI"
YANGIN_SABITI = "YANGIN"
ELEKTRIK_KESINTISI_SABITI = "ELEKTRİK_KESİNTİSİ"
HIRSIZLIK_SABITI = "HIRSIZLIK"
KULTUREL_ETKINLIK_SABITI = "KÜLTÜREL_ETKİNLİK"

# Tüm geçerli haber türleri (Liste sırası aynı zamanda öncelik sırasıdır)
HABER_TURLERI_SABITI: List[str] = [
    TRAFIK_KAZASI_SABITI,
    YANGIN_SABITI,
    ELEKTRIK_KESINTISI_SABITI,
    HIRSIZLIK_SABITI,
    KULTUREL_ETKINLIK_SABITI,
]

# Başlıktaki eşleşmelerin skorunu artıran çarpan sabiti
BASLIK_AGIRLIK_SABITI = 3

# Her tür için aranacak anahtar kelime ve kalıplar (RegEx metni olarak)
ANAHTAR_KELIMELER_SABITI: Dict[str, List[str]] = {
    TRAFIK_KAZASI_SABITI: [
        r"trafik\s+kazas[ıi]", r"kaza", r"çarp[ıi]şma", r"çarp[ıi]şt[ıi]", r"trafik",
        r"araç", r"otomobil", r"kamyon", r"yayaya\s+çarpt[ıi]", r"zincirleme",
        r"motosiklet", r"otob[üu]s", r"minib[üu]s", r"t[ıi]r", r"takla\s+att[ıi]",
        r"devrildi", r"yaral[ıi]", r"can\s+verdi", r"hayat[ıi]n[ıi]\s+kaybetti",
        r"feci\s+kaza", r"maddi\s+hasar", r"trafik\s+kazalar[ıi]",
    ],
    YANGIN_SABITI: [
        r"yang[ıi]n", r"alev", r"yan[ıi]yor", r"itfaiye", r"yand[ıi]",
        r"ç[ıi]kt[ıi]\s+yang[ıi]n", r"alevler", r"s[öo]nd[üu]r[üu]ld[üu]",
        r"s[öo]nd[üu]rme", r"k[üu]le\s+d[öo]nd[üu]", r"duman", r"k[öo]m[üu]r\s+oldu",
        r"tutuştu", r"yanarak",
    ],
    ELEKTRIK_KESINTISI_SABITI: [
        r"elektrik\s+kesintisi", r"elektrik\s+kesintileri", r"kesinti",
        r"elektrik\s+verilmeyecek", r"ar[ıi]za", r"enerji\s+kesintisi",
        r"elektrik\s+ar[ıi]zas[ıi]", r"karanlık(?:ta)?\s+kald[ıi]", r"trafo\s+patlad[ıi]",
        r"elektrik(?:ler)?\s+kesildi", r"elektrik(?:ler)?\s+gitti", r"programl[ıi]\s+kesinti",
    ],
    HIRSIZLIK_SABITI: [
        r"h[ıi]rs[ıi]z", r"h[ıi]rs[ıi]zl[ıi]k", r"çald[ıi]", r"gasp", r"soygun",
        r"çal[ıi]nd[ıi]", r"h[ıi]rs[ıi]zl[ıi]k\s+çetesi", r"arakla", r"kapkaç",
        r"doland[ıi]r[ıi]c[ıi]", r"yakaland[ıi]", r"suç\s+[öo]rg[üu]t[üu]",
    ],
    KULTUREL_ETKINLIK_SABITI: [
        r"konser", r"festival", r"sergi", r"etkinlik", r"g[öo]steri", r"tiyatro",
        r"panel", r"fuar", r"toplant[ıi]", r"seminer", r"at[öo]lye", r"m[üu]ze",
        r"k[üu]lt[üu]r", r"sanat", r"[öo]d[üu]l\s+t[öo]reni", r"a[çc][ıi]l[ıi]ş",
        r"dans", r"opera", r"bale", r"sinema",
    ],
}

# Regex kalıpları performansı artırmak için modül yüklendiğinde bir kez derlenir
DERLI_KALIPLAR_SABITI: Dict[str, List[re.Pattern]] = {}

for _tur, _kelimeler in ANAHTAR_KELIMELER_SABITI.items():
    DERLI_KALIPLAR_SABITI[_tur] = [
        re.compile(r"(?<!\w)" + kelime + r"(?!\w)", re.IGNORECASE | re.UNICODE)
        for kelime in _kelimeler
    ]


# ── Sınıflandırma Yardımcı Fonksiyonları ─────────────────────────────────────


def _skor_hesapla(metin: str, tur: str) -> int:
    """
    Hedef metin üzerinde, belirtilen türün önceden derlenmiş regex kalıplarını arar ve eşleşme sayısını skor olarak döner.

    Args:
        metin (str): Aranacak cümle veya paragraf.
        tur (str): Kontrol edilecek haber türü (örn. "YANGIN").

    Returns:
        int: Metin içinde geçen toplam anahtar kelime eşleşmesi skoru.
    """
    skor = 0
    for kalip in DERLI_KALIPLAR_SABITI.get(tur, []):
        eslesmeler = kalip.findall(metin)
        skor += len(eslesmeler)
    return skor


def _tur_skorlari(baslik: str, icerik: str) -> Dict[str, int]:
    """
    Hem başlık hem de içerik alanlarını tarayarak her bir haber türü için genel skoru hesaplar.
    Başlıktaki eşleşmeler, ağırlık sabitiyle (BASLIK_AGIRLIK_SABITI) çarpılır.

    Args:
        baslik (str): Analiz edilecek başlık.
        icerik (str): Analiz edilecek ana haber gövdesi.

    Returns:
        Dict[str, int]: { haber_turu: toplam_skor } şeklinde bir sözlük.
    """
    skorlar: Dict[str, int] = {}

    for tur in HABER_TURLERI_SABITI:
        baslik_skor = _skor_hesapla(baslik, tur) * BASLIK_AGIRLIK_SABITI
        icerik_skor = _skor_hesapla(icerik, tur)
        skorlar[tur] = baslik_skor + icerik_skor

    return skorlar


def _en_yuksek_skorlu_turu_bul(skorlar: Dict[str, int]) -> Optional[str]:
    """
    Hesaplanan skor sözlüğüne bakarak en yüksek puanı alan türü seçer.
    Hiçbir tür eşleşmemişse None döner; eşitlik varsa öncelik sırasını (HABER_TURLERI_SABITI) uygular.

    Args:
        skorlar (Dict[str, int]): Önceden hesaplanmış tür -> skor eşleşmeleri.

    Returns:
        Optional[str]: Kazanan kategori ismi veya bulunamadıysa None.
    """
    # Eğer bütün skorlar 0 ise hiçbir eşleşme olmamıştır
    if all(s == 0 for s in skorlar.values()):
        return None

    # Matematiksel olarak ulaşılan en yüksek tepe skoru buluyoruz
    maks_skor = max(skorlar.values())

    # Sistem önceliğine göre türler listesinde sırayla gezip o skoru tutan ilk türü döndürüyoruz
    for tur in HABER_TURLERI_SABITI:
        if skorlar[tur] == maks_skor:
            return tur

    return None


# ── Ana Sınıflandırma Fonksiyonları ──────────────────────────────────────────


def siniflandir(baslik: str, icerik: str) -> Optional[str]:
    """
    Dışarıya dönük basit sınıflandırma arayüzü. Sadece haberin türünü string olarak döner.

    Birden fazla tür eşleşirse şu mantık uygulanır:
      1. En yüksek skora sahip tür seçilir.
      2. Eşit skor varsa öncelik sırası geçerlidir: (TRAFİK_KAZASI > YANGIN > vb.)

    Args:
        baslik (str): Haber başlığı.
        icerik (str): Özet veya tam içerik.

    Returns:
        Optional[str]: Kategori adı veya sınıflandırılamadıysa None.
    """
    if not baslik and not icerik:
        return None

    baslik = baslik or ""
    icerik = icerik or ""

    skorlar = _tur_skorlari(baslik, icerik)
    return _en_yuksek_skorlu_turu_bul(skorlar)


def siniflandir_detayli(baslik: str, icerik: str) -> Dict[str, Any]:
    """
    Hata ayıklama veya ağırlıklandırma analizleri için çalışır. Sadece türü dönmekle kalmaz, 
    diğer kategorilerin hangi skoru aldığını parçalı olarak gösterir.
    (30 Satır kuralına uygun şekilde parçalanarak optimize edildi).

    Args:
        baslik (str): Haber başlığı.
        icerik (str): Haber içeriği.

    Returns:
        Dict[str, Any]: Detaylı inceleme ve puan durumları sözlüğü.
    """
    baslik = baslik or ""
    icerik = icerik or ""

    # Yalnızca başlık aramalarından dönen skorları tutan sözlük
    baslik_skorlari = {tur: _skor_hesapla(baslik, tur) for tur in HABER_TURLERI_SABITI}
    
    # Yalnızca içerik aramalarından dönen skorları tutan sözlük
    icerik_skorlari = {tur: _skor_hesapla(icerik, tur) for tur in HABER_TURLERI_SABITI}

    # Başlıkların önem derecesini (ağırlığını) uygulayıp her türün son total skorunu hesaplıyoruz
    toplam_skorlar = {
        tur: (baslik_skorlari[tur] * BASLIK_AGIRLIK_SABITI) + icerik_skorlari[tur]
        for tur in HABER_TURLERI_SABITI
    }

    # Asıl kazanan türü belirliyoruz
    haber_turu = _en_yuksek_skorlu_turu_bul(toplam_skorlar)

    return {
        "haber_turu": haber_turu,
        "skorlar": toplam_skorlar,
        "baslik_skorlari": baslik_skorlari,
        "icerik_skorlari": icerik_skorlari,
    }


def toplu_siniflandir(haberler: List[Dict], baslik_alani: str = "baslik", icerik_alani: str = "icerik") -> List[Dict]:
    """
    Bir haber veri seti (liste) üzerinde dolaşarak toplu sınıflandırma işlemi yapar ve veriyi günceller.

    Args:
        haberler (List[Dict]): Sözlüklerden oluşan liste (MongoDB formatı)
        baslik_alani (str): Sözlük içerisinde başlığın tutulduğu anahtar isim.
        icerik_alani (str): Sözlük içerisinde içeriğin tutulduğu anahtar isim.

    Returns:
        List[Dict]: Sınıflandırılmış haber listesi (Yerinde mutasyon ile güncellenmiş nesne döner).
    """
    for haber in haberler:
        baslik = haber.get(baslik_alani, "")
        icerik = haber.get(icerik_alani, "")
        haber["haber_turu"] = siniflandir(baslik, icerik)

    return haberler


# ── Test Akışı (Sadece modül bağımsız çalıştırıldığında) ───────────────────

if __name__ == "__main__":
    # Konsol üzerinde çalışan basit test örnekleri
    test_haberleri = [
        {
            "baslik": "İzmit'te feci trafik kazası: 3 yaralı",
            "icerik": (
                "Kocaeli'nin İzmit ilçesinde meydana gelen zincirleme trafik kazasında "
                "3 kişi yaralandı. Ankara Caddesi üzerinde iki otomobil ve bir kamyon "
                "çarpıştı. Yaralılar hastaneye kaldırıldı."
            ),
        },
        {
            "baslik": "Gebze'de apartmanda yangın çıktı",
            "icerik": (
                "Gebze'de bir apartmanın 3. katında çıkan yangın itfaiye ekiplerince "
                "söndürüldü. Yangında dumandan etkilenen 2 kişi hastaneye kaldırıldı."
            ),
        },
    ]

    print("=" * 70)
    print("HABER SINIFLANDIRMA MODÜLÜ TESTİ")
    print("=" * 70)

    for i, haber in enumerate(test_haberleri, 1):
        detay = siniflandir_detayli(haber["baslik"], haber["icerik"])
        print(f"\n{'─' * 70}")
        print(f"Haber {i}: {haber['baslik']}")
        print(f"  Tür    : {detay['haber_turu'] or '(Sınıflandırmasız)'}")
        skor_parts = [f"{t}={s}" for t, s in detay["skorlar"].items() if s > 0]
        print(f"  Skorlar: {', '.join(skor_parts)}")
