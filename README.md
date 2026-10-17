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
| **Gerçek fare** — tık, sağ/çift tık, kaydırma, sürükleme; imleç yerine döner | XTEST · RemoteDesktop portalı | SendInput | CGEvent |
| **Gerçek klavye** — kısayollar, her dilde metin | XTEST · portal + pano | SendInput (Unicode) | CGEvent (Unicode) |
| **Butona adıyla basma** — fare hiç oynamaz | AT-SPI | UI Automation | Accessibility (AX) |
| **Açık terminaller** — başka bir ajanın, ssh'ın, build'in ekranını oku; ona yaz | Konsole D-Bus, tmux | konsol tamponu (AttachConsole) | Terminal.app, iTerm2, tmux |
| **Kendi terminali** — komut çalıştır, çıktıyı al | tmux / PTY | cmd.exe | tmux / PTY |
| **Ekran görüntüsü** — pencereye kırp, küçült; görüntü koordinatıyla tıkla | spectacle · grim · Xlib | GDI | screencapture |

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
| MCP istemcisi (Claude Code, Claude Desktop, Cursor) | `claude mcp add nexremote -- ~/bin/nexremote-mcp` — 21 araç, ekran görüntüsü görsel olarak döner |
| HTTP konuşan uzak ajan | `nexremote-http --port 8788 --token GİZLİ` |
| Tool-calling'i olmayan model | `nexremote-agent --model-cmd "ollama run qwen2.5" --goal "…"` |

Komutların tamamı: `nexremote --help`. Ajan talimatı: [`adapters/skill/nexremote.md`](adapters/skill/nexremote.md).

## Mimari

```
core/nexremote/
  nexremote.py      tek giriş noktası (facade); adaptörler yalnızca bunu bilir
  platforms/        işletim sistemi katmanı (Desktop sözleşmesi, base.py)
    linux.py          KWin / Hyprland / Sway / X11 seçimi
    x11.py wlroots.py X11 (XTEST, EWMH) · Sway, Hyprland IPC
    windows.py        Win32 + SendInput + GDI; _wincon.py konsol yardımcısı
    macos.py          Quartz CGEvent + CGWindowList + Terminal/iTerm2
  kwin.py           pencereler + imleç konumu (KWin betiği → callDBus ile yanıt)
  portal_input.py   fare/klavye daemon'ı (RemoteDesktop + ScreenCast oturumu)
  terminals.py      masaüstündeki Konsole sekmeleri ve tmux bölmeleri
  backends/gui.py   AT-SPI ağacı (Linux), isimle bulma/tıklama
  backends/uia.py   UI Automation ağacı (Windows)
  backends/ax.py    Accessibility ağacı (macOS)
  backends/tui.py   NexRemote'un kendi tmux/PTY oturumları
  xcursor.py        görünür ajan imleci (click-through, kullanıcıyı engellemez)
adapters/  cli · mcp · http · skill
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
- Sway / Hyprland: sahte `swaymsg` / `hyprctl` IPC ile.
- Windows: Wine içinde gerçek Windows Python'u, ayrı prefix ve başsız ekranla
  (`tests/wine/setup.sh` bir kez, sonra `tests/wine/run.sh tests/test_windows_wine.py`):
  gerçek Notepad penceresi ve cmd.exe konsolu açılır.
- macOS: Quartz/AX çağrıları sahte kütüphaneyle sınanır (`tests/test_macos_logic.py`).

## Sınırlar

- Linux: KDE Plasma 6 Wayland gerçek masaüstünde, X11 Xvfb içinde, Sway/Hyprland sahte IPC
  ile test edildi. GNOME'da pencere listesi yalnızca AT-SPI'ye düşer.
- Windows: Wine 10 üzerinde test edildi; gerçek Windows 10/11'de doğrulanmadı.
  Wine'ın UI Automation'ı denetim ağacını gezemediği için isimle tıklama
  Wine'da sınanamadı. Yönetici olarak çalışan pencerelere yalnızca yönetici
  olarak çalışan NexRemote ulaşır (UIPI).
- macOS: kod yazıldı ve mantığı sahte Quartz ile test edildi, **gerçek bir
  Mac'te henüz doğrulanmadı** — önizleme sürümü.
- Konsole D-Bus yalnızca görünen ekranı verir; geçmiş satırlar için tmux gerekir.
- Tarayıcılar ve Electron uygulamaları çoğunlukla ağaç yayınlamaz; bunlarda
  ekran görüntüsü + koordinat tıklaması kullanılır.
- Görünür ajan imleci şimdilik yalnızca Linux'ta.

MIT
