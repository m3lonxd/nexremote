# NexRemote — Geliştirme Raporu

## v0.3.0 — çok platform (2026-10-17)

Facade artık işletim sisteminden bağımsız; her şey `platforms.Desktop`
sözleşmesinden geçer (`core/nexremote/platforms/base.py`).

| Platform | Katman | Doğrulama |
|---|---|---|
| KDE Plasma 6 Wayland | KWin + RemoteDesktop portalı + Konsole | gerçek masaüstü (salt okuma) + izole sandbox: giriş 13/13, CLI 21/21 |
| X11 | XTEST, EWMH, Xlib ekran | Xvfb sandbox 22/22 |
| Sway / Hyprland | swaymsg / hyprctl IPC | sahte IPC 15/15 |
| Windows | Win32 EnumWindows, SendInput, GDI, UI Automation, AttachConsole | Wine 10 + gerçek Windows Python: 22/22 (Notepad + cmd.exe) |
| macOS (önizleme) | Quartz CGEvent, CGWindowList, AX, screencapture, Terminal/iTerm2 | sahte Quartz ile 94/94 — gerçek Mac'te çalıştırılmadı |

- `pyproject.toml`: `pip install .[all]` ile dört giriş komutu (Windows/macOS kurulum yolu).
- Windows TUI: pipe modu (cmd.exe); CLI ajan döngüsü kabuk kullanmadan çalışır.
- Testler: `tests/run_all.sh` (Linux + macOS mantık), `tests/wine/run.sh tests/test_windows_wine.py`.

**Gerçek masaüstü denemesi (2026-10-17, KDE Plasma 6, 3 monitör):** kcalc'ta
düğmelere adıyla basıldı (7×8=56), ekran görüntüsünden koordinatla tık + klavye
(12*12=144). Sandbox'ın yakalayamadığı üç hata bulundu ve düzeltildi:
1. KDE portalı yayın konumu göndermiyor → ilk ekran dışına tıklanamıyordu
   (KWin çıkışlarından + tek seferlik ölçümle konumlanıyor).
2. Ajan imleci siyah kare içinde çiziliyordu (Shape yalnızca girdi içindi).
3. İmleç hedefin 36 px sağ altında görünüyordu (hotspot hizası).
Ajan imleci artık kullanıcının imleç temasından (SVG; yoksa Xcursor) çiziliyor,
120 fps minimum-jerk hareketle gidiyor.

**Doğrulanmayanlar:** gerçek Windows 10/11 donanımı; Wine'da UIA ağaç gezintisi
olmadığından Windows'ta isimle tıklama; macOS'un tamamı gerçek donanımda;
GNOME. Ajan imleci yalnızca Linux'ta.

---

## v0.1 — ilk sürüm (2026-10-13)

**Hedef:** Her harness'a, her modele kurulup çalışabilen; GUI + terminal (TUI)
ortamlarını görebilen ve kontrol edebilen, proxy'lenmiş CLI'lar dahil her
ortamda çalışan AI ajanı köprüsü.

## Durum: ✅ TAMAMLANDI — 5/5 kabul testi geçti

## Ne yapıldı

### Çekirdek (core/nexremote/)
- `models.py` — ortak dil: Element/Window/Screenshot/CommandResult, Role/Action
  normalizasyonu (backend-bağımsız).
- `base.py` — Backend ABC (GUI + TUI polimorfizmi).
- `nexremote.py` — NexRemote facade; **eksik backend tüm sistemi çökertmez**
  (kısmi ortam desteği).
- `input.py` — Wayland giriş enjeksiyonu (wtype/ydotool/dotool/xdotool).
- `backends/gui.py` — AT-SPI tabanlı görme + isimle tıklama.
- `backends/tui.py` — tmux (kalıcı oturum + capture-pane) / PTY fallback.

### Adaptörler (adapters/)
| Adaptör | Ne sunar | Doğrulama |
|---|---|---|
| `cli/nexremote_cli.py` | 15 komut + `--json`, `caps/observe/tree/find/click/...` | ✅ kcalc toggle |
| `mcp/server.py` | 13 MCP aracı (mcp 2.2.0 MCPServer) | ✅ bağımsız handshake |
| `cli/agent_loop.py` | metin-protokol döngüsü (tool-calling'siz modeller) | ✅ sahte model |
| `http/server.py` | stdlib HTTP API, Bearer token | ✅ gui+tui |
| `skill/nexremote.md` | LLM skill talimatı | — |

### Altyapı
- `install.sh` — wrapper'ları kurar + bağımlılık kontrolü.
- `docs/HARNESSES.md` — 11 harness için kurulum matrisi.
- `docs/DEPENDENCIES.md` — sistem durumu + kurulum komutları.
- `tests/test_smoke.py` — 9/9 geçti.
- `tests/test_acceptance.py` — 5/5 geçti.

## Kanıtlanan yetenekler

1. **GUI görme + tıklama (AT-SPI):** kcalc üzerinde radio düğmesi tıklandı,
   `Deg=True→Rad=True` durum değişimi doğrulandı. Piksel tahmini yok — isimle.
2. **Kalıcı TUI oturumları:** `run` → **ayrı çağrıda** `screen` → ekran okundu.
   (Bu kritik bir bug'dı: `close()` tmux'u öldürüyordu, düzeltildi.)
3. **İnteraktif TUI ajanı:** Python REPL başlatıldı, ayrı çağrılarda
   `print(6*7)` gönderildi, `42` ekrandan okundu.
4. **Metin protokolü:** tool-calling'i olmayan model `nexremote` satırları
   yazdı, wrapper çalıştırdı, `DONE` alındı.
5. **MCP:** 13 araç, JSON-RPC handshake + `tools/call` bağımsız doğrulandı.
6. **HTTP:** `curl` ile health/caps/windows/run çalıştı.
7. **Görünür ajan imleci (crosshair):** `core/nexremote/xcursor.py` imleç stili
   "yön oku"ndan **crosshair (hedef)** stilinine çevrildi — dört yöne kalın
   çizgi + merkez halka + merkez nokta (`_line` metodu eklendi). İzole
   `Xvfb :98` sandbox'ında piksel sayımıyla doğrulandı: merkez nokta + 4 kol
   renkli (244 renkli piksel), boşluk/köşeler siyah. Kullanıcının gerçek
   ekranına (`:0`/`:1`) dokunulmadı.

## Kısıtlar (dürüst)
- Electron uygulamaları (Discord/VSCode/Slack) iç butonlarını AT-SPI'ye
  sunmak için `--force-renderer-accessibility` ister.
- Wayland yazma için `wtype` gerekir (kuruldu ✓).
- Saf Qt bazı uygulamalar (kate) AT-SPI köprüsü istiyor; kcalc tam doğrulandı.
- Ekran görüntüsü piksel getirir; görsel yorum görsel modele aittir.

## Sonraki adımlar (öneri)
- Electron erişilebilirlik bayrağını otomatik enjekte eden bir sarmalayıcı.
- Koordinat-tabanlı tıklama için `ydotool` (Debian'da depo dışı; derleme gerek).
- Uzak sunucu dağıtımı (`deploy/`) — HTTP adaptörü + systemd unit.
- MCP kaynakları (resources) ile pencere ağacını canlı sunma.
