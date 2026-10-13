# NexRemote — landing site

Statik site (framework yok). Vercel'de `nexremote.vercel.app` olarak yayınlanır.

## Yapı
```
site/
├── index.html          tek sayfa
├── vercel.json         header + redirect kuralları
├── assets/
│   ├── style.css       tasarım sistemi (dark default, light theme)
│   ├── ui.js           sekmeler, scroll-reveal, kod kopyala, sticky nav
│   ├── hero.js         hero terminal animasyonu (canlı oturum oynatımı)
│   └── arch.js         animasyonlu SVG mimari diyagramı
├── download/           üretilen indirme paketleri (nexremote.zip / .tar.gz)
└── make_download.sh    paketi yeniden üretir
```

## İndirme paketini güncelle
Proje kodu değiştiğinde:
```bash
bash site/make_download.sh      # ~/nexremote kökünden çalıştır
```

## Yerel önizleme
```bash
cd site && python3 -m http.server 8899     # http://localhost:8899
```

## Deploy
```bash
cd site && vercel --prod
```

## Tasarım ilkeleri (anti-slop)
Bu site bilinçli olarak AI-şablonundan kaçınır:
- Mor/pembe mesh gradyan YOK — mühendislik ızgarası dokusu var.
- "3 kart + sahte istatistik + yorumlar" YOK — ürünün kendisi (terminal, ağaç, diyagram) gösteriliyor.
- Inter-for-everything YOK — başlıklar Inter Tight, kod/teknik metin IBM Plex Mono.
- Her animasyon bir işe yarıyor: daktilo gerçek çıktıyı gösterir, diyagram akışı anlatır. Konfeti/glow/parçacık YOK.
- `prefers-reduced-motion` her animasyonda dikkate alınır.
