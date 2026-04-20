let map;
let mapMarkers = [];
let allNewsData = [];

const cardBadgeClassMap = {
    'TRAFİK_KAZASI': 'cb-kaza',
    'YANGIN': 'cb-yangin',
    'ELEKTRİK_KESİNTİSİ': 'cb-elektrik',
    'HIRSIZLIK': 'cb-hirsizlik',
    'KÜLTÜREL_ETKİNLİK': 'cb-kulturel'
};

const cardBorderClassMap = {
    'TRAFİK_KAZASI': 'card-kaza',
    'YANGIN': 'card-yangin',
    'ELEKTRİK_KESİNTİSİ': 'card-elektrik',
    'HIRSIZLIK': 'card-hirsizlik',
    'KÜLTÜREL_ETKİNLİK': 'card-kulturel'
};

const turDisplayNames = {
    'TRAFİK_KAZASI': 'Trafik Kazası',
    'YANGIN': 'Yangın',
    'ELEKTRİK_KESİNTİSİ': 'Elektrik Kesintisi',
    'HIRSIZLIK': 'Hırsızlık',
    'KÜLTÜREL_ETKİNLİK': 'Etkinlikler'
};

const markerIcons = {
    'TRAFİK_KAZASI': 'http://maps.google.com/mapfiles/ms/icons/red-dot.png',
    'YANGIN': 'http://maps.google.com/mapfiles/ms/icons/orange-dot.png',
    'ELEKTRİK_KESİNTİSİ': 'http://maps.google.com/mapfiles/ms/icons/yellow-dot.png',
    'HIRSIZLIK': 'http://maps.google.com/mapfiles/ms/icons/blue-dot.png',
    'KÜLTÜREL_ETKİNLİK': 'http://maps.google.com/mapfiles/ms/icons/green-dot.png'
};

const activeFilters = new Set(['TRAFİK_KAZASI', 'YANGIN', 'ELEKTRİK_KESİNTİSİ', 'HIRSIZLIK', 'KÜLTÜREL_ETKİNLİK']);

// ── Toast Bildirimi ──
function showToast(message) {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3500);
}

// ── Harita ──
function initMap() {
    map = new google.maps.Map(document.getElementById('map'), {
        center: { lat: 40.7654, lng: 29.9408 },
        zoom: 11,
        mapTypeId: 'roadmap',
        mapTypeControl: false,
        streetViewControl: false,
        styles: []
    });
    fetchHaberler();
}

async function fetchHaberler() {
    try {
        const response = await fetch('http://localhost:8000/api/haberler');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        allNewsData = await response.json();
        applyFilters();
    } catch (error) {
        console.error('Haberler çekilirken hata:', error);
        document.getElementById('news-list').innerHTML =
            '<div class="loading" style="color:#ef4444;">Bağlantı hatası</div>';
    }
}

function applyFilters() {
    const districtFilter = document.getElementById('district-filter').value.toLowerCase();
    const startDate = document.getElementById('date-start').value;
    const endDate = document.getElementById('date-end').value;

    const filtered = allNewsData.filter(news => {
        if (!news.haber_turu) return false;
        if (!activeFilters.has(news.haber_turu)) return false;
        if (districtFilter && (!news.konum_metin || !news.konum_metin.toLowerCase().includes(districtFilter))) return false;
        if (startDate && new Date(news.yayin_tarihi) < new Date(startDate)) return false;
        if (endDate && new Date(news.yayin_tarihi) > new Date(endDate + 'T23:59:59')) return false;
        return true;
    });

    renderNewsCards(filtered);
    updateMapMarkers(filtered);
}
function renderNewsCards(newsArray) {
    const listElement = document.getElementById('news-list');
    document.getElementById('news-count').innerText = newsArray.length;
    listElement.innerHTML = '';

    if (newsArray.length === 0) {
        listElement.innerHTML = '<div class="loading">Kayıt bulunamadı.</div>';
        return;
    }

    newsArray.forEach(news => {
        const d = new Date(news.yayin_tarihi);
        const dateString = d.toLocaleDateString('tr-TR') + ' ' +
            d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });

        const badgeClass = cardBadgeClassMap[news.haber_turu] || '';
        const borderClass = cardBorderClassMap[news.haber_turu] || '';
        const turName = turDisplayNames[news.haber_turu] || news.haber_turu;

        const card = document.createElement('div');
        card.className = `news-card ${borderClass}`;

        // --- DEĞİŞİKLİK YAPILAN KISIM BURASI ---
        card.innerHTML = `
            <div class="news-card-header">
                <span class="card-badge ${badgeClass}">${turName}</span>
                <span class="date">${dateString}</span>
            </div>
            <h4>${news.baslik}</h4>
            <div class="news-card-footer">
                <span>📍 ${news.konum_metin || 'Bilinmiyor'}</span>
                <span class="source-tag">${news.kaynaklar && news.kaynaklar.length > 1 ? 'Çoklu Kaynak' : news.site_adi}</span>
            </div>
            <a href="${news.haber_linki}" target="_blank" class="sidebar-btn">Habere Git</a>
        `;
        // ----------------------------------------

        card.addEventListener('click', () => {
            if (news.konum_lat && news.konum_lon) {
                map.panTo({ lat: parseFloat(news.konum_lat), lng: parseFloat(news.konum_lon) });
                map.setZoom(15);
            }
        });

        listElement.appendChild(card);
    });
}

let activeInfoWindow = null;

function updateMapMarkers(newsArray) {
    mapMarkers.forEach(m => m.setMap(null));
    mapMarkers = [];

    newsArray.forEach(news => {
        if (!news.konum_lat || !news.konum_lon) return;

        const position = { lat: parseFloat(news.konum_lat), lng: parseFloat(news.konum_lon) };
        const iconUrl = markerIcons[news.haber_turu] || null;

        const marker = new google.maps.Marker({
            position,
            map,
            icon: iconUrl,
            title: news.baslik
        });

        const kaynaklarText = news.kaynaklar && news.kaynaklar.length > 0
            ? news.kaynaklar.join(' | ')
            : news.site_adi;

        const infoWindow = new google.maps.InfoWindow({
            content: `
                <div class="iw-content">
                    <div class="iw-title">${news.baslik}</div>
                    <div class="iw-meta">
                        <p><strong>Tarih:</strong> ${new Date(news.yayin_tarihi).toLocaleString('tr-TR')}</p>
                        <p><strong>Kaynak:</strong> ${kaynaklarText}</p>
                    </div>
                    <a href="${news.haber_linki}" class="iw-link" target="_blank">Habere Git</a>
                </div>
            `
        });

        marker.addListener('click', () => {
            if (activeInfoWindow) activeInfoWindow.close();
            infoWindow.open(map, marker);
            activeInfoWindow = infoWindow;
        });

        mapMarkers.push(marker);
    });
}

// ── Badge Toggle ──
document.querySelectorAll('.filter-badge').forEach(badge => {
    badge.addEventListener('click', () => {
        const val = badge.getAttribute('data-val');
        if (activeFilters.has(val)) {
            activeFilters.delete(val);
            badge.classList.remove('active');
        } else {
            activeFilters.add(val);
            badge.classList.add('active');
        }
        applyFilters();
    });
});

document.getElementById('district-filter').addEventListener('change', applyFilters);
document.getElementById('date-start').addEventListener('change', applyFilters);
document.getElementById('date-end').addEventListener('change', applyFilters);

// ── Scrape Butonu ──
document.getElementById('scrape-btn').addEventListener('click', async () => {
    const btn = document.getElementById('scrape-btn');
    btn.disabled = true;
    btn.innerText = 'Güncelleniyor...';

    try {
        const response = await fetch('http://localhost:8000/api/haberler/scrape', { method: 'POST' });
        if (response.ok) {
            showToast('✓ Ağ taraması başlatıldı. Birkaç dakika sonra yenileyin.');
        } else {
            showToast('⚠ Tarama başlatılamadı.');
        }
    } catch (e) {
        console.error(e);
        showToast('⚠ Sunucu bağlantı hatası.');
    } finally {
        setTimeout(() => {
            btn.disabled = false;
            btn.innerText = 'Verileri Güncelle';
        }, 3000);
    }
});