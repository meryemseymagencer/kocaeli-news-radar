import requests

data = requests.get("http://localhost:8000/api/haberler").json()
print("Toplam haber:", len(data))
print()

no_loc = 0
has_loc = 0
for h in data:
    lat = h.get("konum_lat")
    lon = h.get("konum_lon")
    konum = str(h.get("konum_metin") or "YOK")
    baslik = str(h.get("baslik") or "?")[:60]
    
    if lat and lon:
        has_loc += 1
        status = "KONUM VAR"
    else:
        no_loc += 1
        status = "KONUM YOK"
    
    print(status, "|", "lat=", lat, "lon=", lon, "|", konum[:40], "|", baslik)

print()
print("Konumu olan:", has_loc, "Konumu olmayan:", no_loc)
