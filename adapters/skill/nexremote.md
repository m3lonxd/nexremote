---
name: nexremote
description: >
  Masaüstünü ve terminal oturumlarını görüp kontrol et — GUI uygulamalarını
  (buton/menü/alan) erişilebilirlik ağacı üzerinden oku ve isimle tıkla, ekran
  görüntüsü al, terminal komutu çalıştır ve TUI ekranını oku. Bir AI ajanı bir
  GUI'yi veya uzak terminali sürmesi gerektiğinde, "ekranda ne var" veya
  "şu butona bas" dendiğinde, ya da bir CLI/TUI ajanının çıktısını gözlemlemek
  gerektiğinde kullan. Kabuk komutu çalıştırabilen her ortamda çalışır.
---

# NexRemote — masaüstü + terminal kontrolü

Kabuk komutu çalıştırarak bu makinenin **grafik masaüstünü** ve **terminal
oturumlarını** görebilir ve kontrol edebilirsin. MCP, tool-calling veya özel
SDK GEREKMEZ — sadece `nexremote` komutları.

Makine-okunur çıktı için her komuta `--json` ekle.

## Gözlem (önce bunu yap)
```bash
nexremote caps                     # ne yapabildiğini öğren
nexremote observe                  # tam durum: pencereler + oturumlar + ağaç
nexremote windows                  # açık pencereler (ref, uygulama, başlık)
nexremote tree --window REF        # pencerenin erişilebilirlik ağacı (metin)
nexremote find "Kaydet"            # öğe adıyla ara → ref döner
nexremote screenshot /tmp/s.png    # piksel görüntüsü (görsel model gerekir)
```

## GUI eylemleri
```bash
nexremote click REF                # öğeye tıkla (ref: tree/find çıktısından)
nexremote set-text REF "metin"     # metin alanına yaz
nexremote key "ctrl+s"             # tuş kombinasyonu
nexremote type "metin"             # aktif pencereye yaz
```

## Terminal (TUI) eylemleri
```bash
nexremote run "ls -la"             # komut çalıştır, çıktıyı al
nexremote screen                   # aktif oturumun ekranını oku
nexremote screen --session ad      # belirli oturumu oku
nexremote keys "C-c" --session ad  # terminale tuş gönder
nexremote sessions                 # oturumları listele
```

## Tipik döngü
1. `nexremote caps` → yetenekleri öğren
2. `nexremote observe` → mevcut durumu gör
3. `nexremote find X` → hedefin ref'ini al
4. `nexremote click REF` (veya `set-text`/`run`) → eylemi yap
5. `nexremote observe` / `nexremote screen` → sonucu doğrula

## Kurulum doğrulaması
```bash
nexremote caps
nexremote windows
nexremote run "echo nexremote-ok"
```

## Notlar
- **Electron** uygulamaları (Discord, VS Code, Slack) iç butonlarını AT-SPI'ye
  sunmak için `--force-renderer-accessibility` bayrağı ister. Örn:
  `code --force-renderer-accessibility` veya
  `ELECTRON_ENABLE_ACCESSIBILITY=1 discord`
- Öğe adıyla bulunamazsa önce `tree` ile ağaca bak; koordinata tıklamak yerine
  `find` kullanmak çok daha güvenilirdir.
- Başsız sunucuda (GUI yok) yalnızca `run`/`screen`/`keys` çalışır.
- `key`/`type` Wayland'de `wtype` (veya `ydotool`) gerektirir; yoksa zarifçe
  başarısız olur.
- `--force-renderer-accessibility` uyarısı yalnız Electron içindir; native
  KDE/GNOME/Qt/GTK uygulamaları zaten tam ağaç verir.
