# NexRemote Bağımlılık Matrisi (DEPENDENCIES.md)

Tarih: 2026-10-13 | Sistem: Debian GNU/Linux 13 (trixie) / KDE Wayland (`WAYLAND_DISPLAY=wayland-0`)

Format: **bileşen | sistemde var mı | kurulum komutu**

---

## GUI — AT-SPI erişilebilirlik ağacı (pencere/öğe görme + kontrol)

| Bileşen | Sistemde var mı | Kurulum komutu |
|---|---|---|
| python3-gi (`gi` modülü) | **VAR** (3.50.0-4+b1) | Debian: `sudo apt install python3-gi` / Arch: `sudo pacman -S python-gobject` |
| gir1.2-atspi-2.0 (`Atspi` 2.0) | **VAR** (2.56.2-1+deb13u2) — `import gi; gi.require_version('Atspi','2.0'); from gi.repository import Atspi` → `ok` | Debian: `sudo apt install gir1.2-atspi-2.0` / Arch: `sudo pacman -S at-spi2-core` |
| Erişilebilirlik oturumu (AT-SPI bus) | **VAR** (GUI backend `capability()` → "AT-SPI erişilebilirlik ağacı aktif") | Ortam değişkeni gerekmez; masaüstü otomatik başlatır. Hizmet vermiyorsa: `systemctl --user restart at-spi-dbus-bus` veya `export NO_AT_BRIDGE=0` |

## Giriş enjeksiyonu (tuş yazma, tıklama)

| Bileşen | Sistemde var mı | Kurulum komutu |
|---|---|---|
| wtype (Wayland) | **VAR** (0.4-3 kurulu) | Debian: `sudo apt install wtype` / Arch: `sudo pacman -S wtype` |
| ydotool (Wayland, /dev/uinput) | **YOK** — Debian trixie resmi deposunda paket YOK (`apt-cache policy ydotool` boş) | Debian: upstream derleme (`git clone https://github.com/ReimuNotMoe/ydotool && cmake && make && sudo make install`) veya PPA/flatpak; Arch: `sudo pacman -S ydotool` (AUR) |
| dotool | **YOK** — Debian deposunda YOK | Arch/AUR: `yay -S dotool` ; Debian: kaynaktan `go install github.com/cjbassi/dotool` (go gerekir) |
| xdotool (X11/XWayland fallback) | YOK (depo mevcut, kurulu değil) | Debian: `sudo apt install xdotool` / Arch: `sudo pacman -S xdotool` |
| `/dev/uinput` (ydotool için) | VAR (cihaz mevcut, `crw------- root root`) | `sudo usermod -aG input $USER` (yeni oturum gerekir) + `sudo chmod 660 /dev/uinput` veya udev kuralı |
| `input` grubu üyeliği | **YOK** (omarchy `input` grubunda değil; gerekmez — wtype kullanılıyor) | `sudo usermod -aG input omarchy` (yalnızca ydotool/dotool seçilecekse gerekli) |

Not: Wayland'de **wtype** doğru araçtır (klavye/mouse, AT-SPI'dan bağımsız). ydotool alternatifi `/dev/uinput` ister; sistemde şu an ikisine de gerek yok, wtype yeterli.

## TUI — terminal oturumları (tmux)

| Bileşen | Sistemde var mı | Kurulum komutu |
|---|---|---|
| tmux | **VAR** (`tmux 3.5a`) | Debian: `sudo apt install tmux` / Arch: `sudo pacman -S tmux` |
| pty desteği (Python `pty`) | **VAR** (standart kütüphane; TUI `capability()` → "mod=tmux; tmux=var; pty=var; kalıcı oturum=evet") | yok (stdlib) |

## MCP — Model Context Protocol sunucu adaptörü

| Bileşen | Sistemde var mı | Kurulum komutu |
|---|---|---|
| `mcp` Python paketi | **VAR** (`2.2.0`) | `pip install mcp` |
| `MCPServer` API (`mcp.server.mcpserver`) | **VAR** (mcp 2.x'te FastMCP → MCPServer yeniden adlandırıldı; `mcp.server.fastmcp` artık yok, import edilirse hata mesajı yönlendirir) | `pip install 'mcp>=2'` (v1 isteniyorsa `pip install 'mcp<2'` + FastMCP) |
| Python ≥ 3.10 (mcp 2.x) | **VAR** (3.13.x) | `sudo apt install python3` |

## Ekran görüntüsü (screenshot — yalnızca kullanıcı için; model görsel ayırt edemez)

| Bileşen | Sistemde var mı | Kurulum komutu |
|---|---|---|
| spectacle (KDE) | **VAR** | Debian: `sudo apt install spectacle` / Arch: `sudo pacman -S spectacle` |
| grim (wlroots) | **VAR** | `sudo apt install grim` / `sudo pacman -S grim` |
| gnome-screenshot | YOK | `sudo apt install gnome-screenshot` |
| scrot (X11) | YOK | `sudo apt install scrot` |

---

## Özet tablo (tek bakışta)

| Alan | Bileşen | Durum | Kurulum |
|---|---|---|---|
| GUI | python3-gi | VAR | `sudo apt install python3-gi` |
| GUI | gir1.2-atspi-2.0 | VAR | `sudo apt install gir1.2-atspi-2.0` |
| Giriş | wtype | VAR | `sudo apt install wtype` |
| Giriş | ydotool | YOK | Debian'ta depo paketi yok — kaynaktan derle / Arch: `sudo pacman -S ydotool` |
| Giriş | dotool | YOK | Arch/AUR: `yay -S dotool` |
| Giriş | /dev/uinput + input grubu | cihaz VAR, üyelik YOK | `sudo usermod -aG input $USER` (yalnızca ydotool/dotool için) |
| TUI | tmux | VAR | `sudo apt install tmux` |
| MCP | mcp (2.2.0) | VAR | `pip install mcp` |
| Ekran | spectacle / grim | VAR | `sudo apt install spectacle grim` |
