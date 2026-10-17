# Bağımlılıklar

## Linux

Birincil ortam: KDE Plasma 6 (Wayland). Ölçüldüğü sistem: Debian 13,
KWin 6.3.6, xdg-desktop-portal-kde 6.3.5 (2026-10-16). X11 oturumunda
`python3-xlib` ile XTEST + EWMH kullanılır (izin istemez); Sway ve Hyprland'de
pencereler `swaymsg` / `hyprctl` ile, giriş portal ya da wtype ile.

| Yetenek | Ne kullanılır | Paket (Debian / Arch) | Yoksa |
|---|---|---|---|
| Tüm pencereler, öne getir/kapat, imleç konumu | KWin scripting (D-Bus) | KDE ile gelir | Yalnızca AT-SPI pencereleri |
| Fare + klavye | xdg-desktop-portal RemoteDesktop v2 + ScreenCast | `xdg-desktop-portal-kde`, `pipewire` | `key`/`type`/`click X Y` çalışmaz |
| Latin-1 dışı metin (ğ ş ı, emoji) | pano üzerinden yapıştırma | `wl-clipboard` | Bu karakterler yazılamaz |
| D-Bus erişimi | dbus-python + PyGObject | `python3-dbus python3-gi` / `python-dbus python-gobject` | Yukarıdakilerin hiçbiri |
| Öğe ağacı, isimle tıklama | AT-SPI | `gir1.2-atspi-2.0` / `at-spi2-core` | `tree`/`find`/`click REF` çalışmaz |
| Açık terminaller | Konsole D-Bus, tmux | `konsole`, `tmux` | Yalnızca ilgili tür listelenmez |
| Kendi terminal oturumları | tmux (yoksa PTY) | `tmux` | PTY'ye düşer |
| Ekran görüntüsü | spectacle / grim | `spectacle` veya `grim` | `screenshot` çalışmaz |
| Kırpma / küçültme | Pillow | `python3-pil` / `python-pillow` | Yalnızca tam boy görüntü |
| Görünür ajan imleci | python-xlib + XWayland | `python3-xlib` | İmleç gösterilmez (tıklama yine çalışır) |
| MCP adaptörü | `mcp` ≥ 2 | `pip install mcp` | CLI/HTTP yine çalışır |

## Neden wtype / ydotool değil

- **wtype**, `zwp_virtual_keyboard_v1` ister. KWin bunu sunmaz:
  `Compositor does not support the virtual keyboard protocol`.
- **ydotool / dotool**, `/dev/uinput`'a yazar; varsayılan izin `root:root 0600`.
  Root veya udev kuralı gerektirir.
- **RemoteDesktop portalı** root istemez. Kullanıcı bir kez onaylar
  ("Allow restoring on future sessions" işaretli). Verilen `restore_token`
  `~/.config/nexremote/portal.json` dosyasına yazılır ve sonraki oturumlar
  diyalog açmadan başlar. İzni geri almak için bu dosyayı silmek yeterli.

## Erişilebilirlik ağacı

KDE varsayılan olarak `org.a11y.Status.IsEnabled=false` ile gelir. Bu durumda
Qt uygulamaları (Konsole, Dolphin...) ağaç yayınlamaz. `nexremote setup` bu
ayarı açar; o andan sonra başlatılan uygulamalar ağaç yayınlar. Firefox
tabanlı tarayıcılar ve Electron uygulamaları ağaç yerine ekran görüntüsü ve
koordinat tıklamasıyla kullanılır. Electron için diğer yol, uygulamayı
`--force-renderer-accessibility` ile başlatmaktır.

## Windows

| Yetenek | Ne kullanılır | Kurulum | Yoksa |
|---|---|---|---|
| Pencereler, fare, klavye, ekran görüntüsü | Win32 (ctypes, stdlib) | — | — |
| Öğe ağacı, isimle tıklama | UI Automation | `comtypes` (pip ile otomatik) | `tree`/`find`/`click REF` çalışmaz |
| Açık konsollar | AttachConsole yardımcı süreci | python.exe (paketlenmiş exe'de yok) | `terms` çalışmaz |
| Kırpma / küçültme, MCP | Pillow, mcp | `pip install .[all]` | Yalnızca tam boy görüntü / CLI-HTTP |

Yönetici olarak çalışan pencerelere UIPI nedeniyle yalnızca yönetici olarak
başlatılmış NexRemote giriş gönderebilir.

## macOS (önizleme)

| Yetenek | Ne kullanılır | İzin | Yoksa |
|---|---|---|---|
| Fare, klavye | Quartz CGEvent (ctypes) | Erişilebilirlik | `click`/`key`/`type` çalışmaz |
| Pencereler | CGWindowList + AX | Ekran Kaydı (başlıklar için), Erişilebilirlik (eylemler) | Başlıklar boş gelir |
| Öğe ağacı | Accessibility (AXUIElement) | Erişilebilirlik | `tree`/`find` çalışmaz |
| Ekran görüntüsü | `screencapture` | Ekran Kaydı | `screenshot` çalışmaz |
| Açık terminaller | Terminal.app / iTerm2 (AppleScript), tmux | Otomasyon (ilk kullanımda sorulur) | Yalnızca tmux |

`nexremote setup` izin istemlerini açar. İzinler Sistem Ayarları → Gizlilik ve
Güvenlik altından geri alınır.
