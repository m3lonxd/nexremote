# NexRemote

Bir AI ajanına masaüstünü ve üzerinde açık terminalleri **görme ve
kullanma** yetkisi verir — Linux (KDE, GNOME, X11, Sway, Hyprland), Windows ve
macOS'ta aynı komutlarla. Ajan kabuk komutu çalıştırabiliyorsa yeter: Claude
Code, Codex, Aider, opencode, kendi betiğin ya da tool-calling'i olmayan bir
yerel model.

```console
$ nexremote observe
Pencereler (6) — * etkin:
  * org.kde.konsole        1920x1080+1080+0     api : claude — Konsole
    firefox                1920x1080+1080+0     Player — Mozilla Firefox
    ...
Açık terminaller (4):
  konsole:7891:1       claude         ~/projects/api        api : claude
  konsole:29769:1      ssh            ~                     deploy@staging

$ nexremote term-read konsole:29769:1          # başka bir terminalin ekranı
$ nexremote term-send konsole:7891:1 "testleri çalıştır" --enter
$ nexremote screenshot --window win:{…} --max-width 1568
$ nexremote click 412 230 --from-shot          # görüntüde gördüğün yere tıkla
$ nexremote type "Merhaba dünya — ğüşıöç"
```

## Ne yapabilir

| | Linux | Windows | macOS |
|---|---|---|---|
| **Ekrandaki her pencere** — başlık, uygulama, konum; öne getir, küçült, kapat | KWin · hyprctl · swaymsg · EWMH | EnumWindows | CGWindowList + AX |
| **Gerçek fare** — tık, sağ/çift tık, kaydırma, sürükleme; imleç yerine döner | XTEST · RemoteDesktop portalı · Sway IPC | SendInput | CGEvent |
| **Gerçek klavye** — kısayollar, her dilde metin | XTEST · portal + pano · wtype | SendInput (Unicode) | CGEvent (Unicode) |
| **Butona adıyla basma** — fare hiç oynamaz | AT-SPI | UI Automation | Accessibility (AX) |
| **Electron/Chromium'u ağacı açık başlatma** — `nexremote launch` | bayrak + ortam | bayrak | bayrak |
| **Açık terminaller** — başka bir ajanın, ssh'ın, build'in ekranını oku; ona yaz | Konsole D-Bus, tmux | konsol tamponu (AttachConsole) | Terminal.app, iTerm2, tmux |
| **Kendi terminali** — komut çalıştır, çıktıyı al | tmux / PTY | cmd.exe | tmux / PTY |
| **Görünür ajan imleci** — ajan nerede çalışıyor gör; senin farene dokunmaz, tıklamayı engellemez | X11/XWayland pencere · imleç teman | katmanlı pencere · sistem oku | NSWindow · sistem oku |
| **Ekran görüntüsü** — pencereye kırp, küçült; görüntü koordinatıyla tıkla | PipeWire yayını · spectacle · grim · Xlib | GDI | screencapture |

### Ref'ler yanlış düğmeye basmaz

`tree` her okumada bir **anlık görüntü** alır ve eyleme açık her öğeye o
okumaya bağlı bir ref verir:

```console
$ nexremote tree --window win:{…} -i
# anlık görüntü @s7k2m9qx — Hesap Makinesi
[frame] Hesap Makinesi
  [button] 7  <click>  ref=@s7k2m9qx:e4
  [button] Temizle  <click>  ref=@s7k2m9qx:e11
$ nexremote click @s7k2m9qx:e4
```

Ref'in arkasında konum değil kimlik kanıtı durur: pencerenin süreci ve
başlığı, öğenin rolü, kararlı adı, adlı ataları. Eylemden hemen önce canlı
ağaç yeniden okunur ve öğe bu kanıtla aranır. Arada bir düğme eklenip liste
kaysa da ref aynı öğeye gider. Kanıtlanamazsa hiçbir şeye basılmaz:
`STALE_REF` (öğe gitti) ya da `AMBIGUOUS_TARGET` (artık birden çok öğe
uyuyor) döner, ajan yeniden `tree` alır. Devre dışı düğme 5 sn'ye kadar
beklenir (`NEXREMOTE_WAIT`), olmazsa `TIMEOUT`. Yoğun uygulamada
`tree --root @s…:e3 -i` yalnız bir bölgeyi açar.

### Hızlı: bir tık bir model turu değil

Her CLI çağrısı eskiden yeni bir Python süreci, yeni D-Bus/AT-SPI bağlantısı ve
pencerenin tüm ağacını yeniden okumaydı; imleç animasyonu da tıkı bekletiyordu.
v0.7'de:

- **Arka plan servisi** (Linux'ta varsayılan): ilk `nexremote` çağrısı servisi
  başlatır, sonrakiler bağlantıları sıcak tutan servise gider — çağrı başına
  süreç maliyeti yarıya iner. Kod değişince servis kendiliğinden yenilenir,
  10 dk boşta kalınca kapanır. `nexremote service status|stop`, kapatmak için
  `NEXREMOTE_SERVICE=0`.
- **Ref hızlı yolu:** tıklamadan önce yalnızca ref'in yolundaki düğüm ve ataları
  okunur; kimlik kanıtı tutarsa ağacın geri kalanı okunmaz (GTK test
  uygulamasında 18 ms → 3 ms). Tutmazsa tam okuma. Her eylemde tam okuma için
  `NEXREMOTE_STRICT=1`.
- **İmleç tıkı bekletmez:** ajan imleci ~80 ms süzülür (`NEXREMOTE_CURSOR_MS`),
  gerçek tık hemen atılır; daemon yoksa arka planda başlar.
- **Daha az model turu:**

```console
$ nexremote do 'click-text "Yeni"' 'fill @s…:e9 "rapor.txt" --enter' 'wait --element Kaydedildi' --after
[1] ✓ click-text "Yeni"
[2] ✓ fill @s…:e9 "rapor.txt" --enter
[3] ✓ wait --element Kaydedildi
# sonrası @s7q… — Belge
+ [button] Kapat  ref=@s7q…:e4
```

`--after` eylemden sonra neyin değiştiğini yazar (yeni öğeler ref'leriyle), ayrı
doğrulama turu gerekmez. `wait` koşulu içeride yoklar. MCP'de `nexremote_batch`,
HTTP'de `POST /batch` aynı işi yapar.

`NEXREMOTE_TRACE=1` her adımın süresini yazar; `nexremote bench` p50/p95 ölçer.

Birden çok ajan aynı makinede çalışıyorsa fare/klavye olayları bir **girdi
kirasıyla** sıraya girer: bir ajanın sürüklemesinin ortasına ötekinin tuşu
karışmaz. Kira yalnız gönderim süresince tutulur.

Bu iki fikir [agent-desktop](https://github.com/vercel-labs/agent-desktop)
(Apache-2.0) projesinden uyarlandı; kod NexRemote için Python'da yeniden
yazıldı.

Root / yönetici gerekmez. Wayland'de fare ve klavye için masaüstü **bir kez**
izin sorar; macOS'ta Erişilebilirlik ve Ekran Kaydı izni bir kez verilir.
Windows ve X11 ayrıca izin istemez.

## Kurulum

**Linux**

```bash
cd nexremote
./install.sh          # ~/bin altına komutları koyar, eksik paketleri listeler
nexremote setup       # erişilebilirliği açar; Wayland izin penceresinde
                      # "Allow restoring…" işaretle → Share
nexremote caps        # neyin çalıştığını gösterir
```

`pip` ile kurmak isterseniz sistem paketlerini (python3-dbus, python3-gi)
görebilmesi için: `pipx install --system-site-packages .`

**Windows** (Python 3.9+)

```powershell
cd nexremote
py -m pip install .[all]   # comtypes (UI Automation) otomatik gelir
nexremote caps
```

**macOS** (Python 3.9+)

```bash
cd nexremote
python3 -m pip install '.[all]'
nexremote setup       # Erişilebilirlik + Ekran Kaydı izin istemlerini açar
nexremote caps
```

## Ajanına bağla

| Ajan | Bağlantı |
|---|---|
| Kabuk çalıştıran her harness | `nexremote skill` çıktısını sistem talimatına ekle |
| MCP istemcisi (Claude Code, Claude Desktop, Cursor) | `claude mcp add nexremote -- ~/bin/nexremote-mcp` — 28 araç (`nexremote_batch` dahil) + canlı kaynaklar (`nexremote://windows`, `nexremote://terminals`, `nexremote://tree/{ref}`), ekran görüntüsü görsel olarak döner |
| HTTP konuşan uzak ajan | `NEXREMOTE_TOKEN=… nexremote-http --port 8788` ya da servis olarak: `deploy/install-service.sh` |
| Tool-calling'i olmayan model | `nexremote-agent --model-cmd "ollama run qwen2.5" --goal "…"` |

### Başka makineden bağlanma

`deploy/install-service.sh` HTTP adaptörünü systemd kullanıcı servisi olarak
kurar: grafik oturumla başlar, onunla kapanır. Token
`~/.config/nexremote/http.token` içinde 600 izniyle üretilir, komut satırında
görünmez. Varsayılan olarak yalnızca `127.0.0.1` dinlenir; uzaktan en güvenli
yol SSH tünelidir:

```bash
deploy/install-service.sh                         # bu makinede 127.0.0.1:8788
ssh -N -L 8788:127.0.0.1:8788 kullanici@masaustu  # uzak ajanın makinesinde
deploy/install-service.sh --host 0.0.0.0 --tls-cert cert.pem --tls-key key.pem   # doğrudan ağa (HTTPS)
deploy/install-service.sh --uninstall
```

Ağa açarken en az 24 karakterlik token zorunludur. Token sabit zamanlı
karşılaştırılır, 4 MB'tan büyük ya da bozuk gövdeler 400 döner. Tarayıcıdan
gelen istekler (`Origin` / çapraz site) reddedilir — bir web sayfası localhost'taki
servise komut gönderemez; token'sız çalışırken `Host` da yerel ad olmalıdır.

Komutların tamamı: `nexremote --help`. Ajan talimatı: [`adapters/skill/nexremote.md`](adapters/skill/nexremote.md).

## Mimari

```
core/nexremote/
  nexremote.py      tek giriş noktası (facade); eylem kapsamı, after/wait/click_text
  snapshot.py       anlık görüntü ref'leri (@sid:eN), hızlı yol + katı yeniden tanıma, fark
  trace.py          NEXREMOTE_TRACE=1 adım süreleri
  lease.py          girdi kirası: makine genelinde tek fare/klavye yazarı
  platforms/        işletim sistemi katmanı (Desktop sözleşmesi, base.py)
    linux.py          KWin / Hyprland / Sway / X11 seçimi
    x11.py wlroots.py X11 (XTEST, EWMH) · Sway, Hyprland IPC
    windows.py        Win32 + SendInput + GDI; _wincon.py konsol yardımcısı
    macos.py          Quartz CGEvent + CGWindowList + Terminal/iTerm2
  kwin.py           pencereler + imleç konumu (KWin betiği → callDBus); servis için kalıcı izleyici
  portal_input.py   fare/klavye daemon'ı (RemoteDesktop + ScreenCast oturumu)
  terminals.py      masaüstündeki Konsole sekmeleri ve tmux bölmeleri
  backends/gui.py   AT-SPI ağacı (Linux), isimle bulma/tıklama
  backends/uia.py   UI Automation ağacı (Windows)
  backends/ax.py    Accessibility ağacı (macOS)
  backends/tui.py   NexRemote'un kendi tmux/PTY oturumları
  agent_cursor.py   görünür ajan imleci: hareket, kare çizimi, daemon (Unix soketi / belirteçli TCP)
  xcursor.py        imleç arka ucu, Linux (X11/XWayland, click-through)
  wincursor.py      imleç arka ucu, Windows (katmanlı pencere, WS_EX_TRANSPARENT)
  maccursor.py      imleç arka ucu, macOS (ctypes objc, ignoresMouseEvents)
adapters/  cli (+ arka plan servisi) · mcp · http · skill
```

## Testler

```bash
tests/run_all.sh
```

Girdi enjeksiyonu yapan testler, `tests/sandbox.sh` ile açılan **izole** bir
masaüstünde koşar: ayrı D-Bus, sanal KWin, ayrı portal ve PipeWire. Gerçek
ekrana, fareye ve izinlere dokunmaz. İzin diyaloğunu da NexRemote'un kendi
erişilebilirlik okuması onaylar.

- X11: başsız `Xvfb` içinde (`tests/x11_sandbox.sh`).
- Sway: başsız gerçek Sway içinde (`tests/sway_sandbox.sh`): Sway IPC faresi,
  wtype ile Türkçe yazı, grim, pencere eylemleri. Hyprland sahte `hyprctl` ile.
- Windows: Wine içinde gerçek Windows Python'u, ayrı prefix ve başsız ekranla
  (`tests/wine/setup.sh` bir kez, sonra `tests/wine/run.sh tests/test_windows_wine.py`):
  gerçek Notepad penceresi ve cmd.exe konsolu açılır.
- Anlık görüntü ref'leri: sahte ağaçla yol kayması, silinen/çoğalan öğe,
  uygulama indeksi kayması, otomatik bekleme ve süreçler arası girdi kirası
  (`tests/test_snapshot.py`).
- macOS: Quartz/AX çağrıları sahte kütüphaneyle sınanır (`tests/test_macos_logic.py`).
- Electron: sandbox'ta gerçek bir Electron penceresi açılır, butonu ağaçtaki
  ref'iyle tıklanır (`NEXR_ELECTRON=/yol/electron`; yoksa test atlanır).
- AT-SPI hızlı yol: Xvfb içinde gerçek bir GTK uygulamasıyla yol kayması,
  kaybolan/ikiz öğe, katı kip, `after`, `wait`, `fill` (`tests/test_atspi_fast.py`).
- Hız: servis, `do`, gecikme bütçesi, tmux düzeltmeleri, ajan döngüsü (`tests/test_speed.py`).

## Sınırlar

- Linux: KDE Plasma 6 Wayland gerçek masaüstünde, X11 Xvfb içinde, Sway 1.10 başsız
  gerçek bileşimcide, Hyprland sahte IPC ile test edildi. GNOME'da pencere listesi yalnızca AT-SPI'ye düşer.
- Windows: Wine 10 üzerinde test edildi; gerçek Windows 10/11'de doğrulanmadı.
  Wine'ın UI Automation'ı denetim ağacını gezemediği için isimle tıklama
  Wine'da sınanamadı. Yönetici olarak çalışan pencerelere yalnızca yönetici
  olarak çalışan NexRemote ulaşır (UIPI).
- macOS: kod yazıldı ve mantığı sahte Quartz ile test edildi, **gerçek bir
  Mac'te henüz doğrulanmadı** — önizleme sürümü.
- Konsole D-Bus yalnızca görünen ekranı verir; geçmiş satırlar için tmux gerekir.
- Electron/Chromium uygulamaları ağacı ancak `--force-renderer-accessibility`
  ile başlatılınca yayınlar; `nexremote launch <program>` bayrağı ekler.
  Çalışan bir uygulamada sonradan açılamaz, uygulamanın yeniden başlatılması gerekir.
  Firefox tabanlı tarayıcılar ağacı kendiliğinden yayınlar. Oyunlar ve canvas
  uygulamaları için ekran görüntüsü + koordinat tıklaması kullanılır.
- v0.7'nin KWin kalıcı pencere izleyicisi, Wayland giriş servisindeki tek
  komutta tık + imleci geri koyma ve servis tarafında kare kırpma gerçek KDE
  masaüstünde henüz denenmedi (sahte servisle sınandı); izleyici kendini tek
  seferlik okumayla doğrular, tutmazsa eski yola döner (`NEXREMOTE_KWIN_WATCH=0`
  ile kapatılır). Arka plan servisi macOS ve Windows'ta varsayılan kapalı
  (`NEXREMOTE_SERVICE=1`).
- Görünür ajan imleci Linux'ta X11/XWayland ister (XWayland'siz saf Wayland'de görünmez, tıklama yine çalışır). Windows arka ucu Wine'da sınandı; macOS arka ucu önizleme, gerçek Mac'te denenmedi.

MIT
