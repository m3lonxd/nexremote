# Sürüm notları

Kullanıcıya dönük kısa notlar. Ayrıntılı geliştirme raporu ve test sonuçları
`PROGRESS.md`'de; İngilizcesi `CHANGELOG.en.md`.

Site (`site/src/build.py`) "Sürüm notları" bölümünü bu iki dosyadan üretir. İki
dosyada sürümler ve tarihler aynı sırada olmalı, en üstteki sürüm
`pyproject.toml`'daki sürüm olmalı; tutmazsa site derlenmez. Biçim:
`## v<sürüm> — <başlık> (<YYYY-AA-GG>)`, isteğe bağlı bir giriş paragrafı,
ardından `- ` ile başlayan maddeler (devam satırları iki boşlukla girintili).

## v0.7.0 — Hız (2026-11-10)

Tıklar arasında saniyelerce bekleme bitti: ref ile yapılan bir eylem artık
milisaniyeler sürüyor.

- **Arka plan servisi.** İlk `nexremote` çağrısı bir servis başlatır; sonraki
  çağrılar onun açık bağlantılarını kullanır (Xvfb'de CLI çağrısı 120 → 55-70 ms).
  Kod değişince kendini yeniler, boşta kapanır. `NEXREMOTE_SERVICE=0` kapatır;
  macOS ve Windows'ta varsayılan kapalı.
- **Ref hızlı yolu.** Tıktan önce pencerenin tamamı değil, yalnızca öğeye giden
  yol okunur ve kimliği doğrulanır (GTK'da 18 → 3 ms). Bir şey kaydıysa tam okuma
  öğeyi kimliğiyle bulur. `NEXREMOTE_STRICT=1` her eylemde tam okur.
- **Daha az model turu.** `do` birden çok adımı tek çağrıda çalıştırır ve ilk
  hatada durur; `--after` neyin değiştiğini yeni ref'leriyle söyler; ayrıca
  `wait`, `click-text`, `fill`, `sleep`. MCP'de `nexremote_batch`,
  `nexremote_click_text`, `nexremote_fill`, `nexremote_wait`, `nexremote_after`;
  HTTP'de `/batch`, `/click_text`, `/fill`, `/wait`.
- **Tık imleci beklemez.** Ajan imlecine nereye gideceği söylenir, tık hemen
  atılır; imleç çalışmıyorsa tıkı bekletmeden arka planda başlar.
- **Ölçüm.** `NEXREMOTE_TRACE=1` adım adım süreleri yazar, `nexremote bench`
  p50/p95 verir.
- **Düzeltmeler.** `win:` ref'i aynı uygulamanın başka penceresini seçebiliyordu;
  tmux oturum adı önekle eşleşiyordu (`build` → `build2`); `-` ile başlayan metin
  ve `&` ya da yorumla biten komutlar `run`'ı bozuyordu; `run` çıkış kodunu
  gizliyordu; HTTP adaptörü tarayıcıdan localhost üzerinden sürülebiliyordu ve
  boş token dosyasıyla token'sız açılıyordu; X11'de `key A` shift'siz gidiyordu.
- Hızlar Xvfb'de ölçüldü; gerçek KDE masaüstünde henüz ölçülmedi.

## v0.6.0 — Linux'ta daha hızlı (2026-11-09)

- **Ekran görüntüsü canlı yayından.** Wayland'de kare, giriş servisinin zaten
  tuttuğu PipeWire yayınından kopyalanır: 1,5 sn yerine yaklaşık 0,2 sn.
- **Tıklar imleç animasyonunu beklemez.**

## v0.5.0 — Ajan imleci her yerde (2026-10-29)

- **Görünür ajan imleci** artık Linux, Windows ve macOS'ta. Farenize dokunmaz:
  tıklarınız içinden geçer, odak kaymaz.
- **Her adaptörden:** MCP `nexremote_cursor`, HTTP `/cursor` ve `/cursor_hide`,
  CLI `cursor`, `point` ve `cursor-hide`.
- Windows imleci Wine'da sınandı; macOS imleci henüz gerçek bir Mac'te
  çalıştırılmadı.

## v0.4.0 — Günlük kullanım (2026-10-29)

- **Electron ve Chromium isimle:** `nexremote launch <program>` uygulamayı
  erişilebilirlik ağacı açık başlatır ve pencere ref'ini döndürür. Ağaç boşsa
  `tree` nedenini ve ne yapılacağını yazar.
- **Sade ağaç:** gizli öğeler atlanır, isimsiz kaplar katlanır, eylemli
  satırlar `ref=` taşır (Zen tarayıcıda 550 → 21 satır).
- **MCP kaynakları:** `nexremote://windows`, `nexremote://terminals`,
  `nexremote://tree/{ref}`.
- **HTTP adaptörü:** token dosyası, TLS, gövde sınırı; ağa açılırken en az 24
  karakterlik token zorunlu. `deploy/install-service.sh` systemd kullanıcı
  servisi kurar.
- **Sway gerçek bileşimcide:** fare Sway IPC ile, tekerlek wlroots sanal
  faresiyle, ekran görüntüsü grim ile.

## v0.3.0 — Çok platform (2026-10-17)

- **Tek komut seti, beş masaüstü:** KDE Plasma 6 Wayland, X11, Sway/Hyprland,
  Windows ve macOS (önizleme) aynı sözleşmenin arkasında.
- **pip paketi:** `pip install .[all]` dört komutu Windows ve macOS'ta da kurar.
- **İlk gerçek masaüstü denemesi** (KDE Plasma 6, üç monitör) sandbox'ın
  yakalayamadığı üç hatayı buldu: ilk ekran dışına tıklanamıyordu, ajan imleci
  siyah kare içinde çiziliyordu, imleç hedefin 36 px yanında görünüyordu.
  Üçü de düzeltildi.
- **Ajan imleci** kullanıcının imleç temasından çiziliyor ve 120 fps hareket
  ediyor.

## v0.1 — İlk sürüm (2026-10-13)

- **Dört giriş:** 15 komutlu CLI (`--json` ile), 13 araçlı MCP sunucusu, HTTP
  API ve tool calling'i olmayan modeller için metin döngüsü; ajanlar için skill
  talimatı.
- **GUI:** AT-SPI ile pencereleri görme ve kontrollere isimle basma.
- **Terminal:** çağrılar arasında yaşayan tmux/PTY oturumları.
