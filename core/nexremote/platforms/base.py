"""Platform sözleşmesi — işletim sistemine bağlı her şey burada soyutlanır.

NexRemote facade'ı (nexremote.py) pencereleri, fareyi/klavyeyi, ekran
görüntüsünü ve masaüstündeki terminalleri YALNIZCA bu arayüz üzerinden
kullanır. Her işletim sistemi/masaüstü bir `Desktop` alt sınıfı sağlar:

    linux.py    KDE (KWin + RemoteDesktop portalı), X11, GNOME, Sway, Hyprland
    windows.py  Win32 + UI Automation + konsol tamponları
    macos.py    Quartz + Accessibility + Terminal.app / iTerm2

KOORDİNAT SÖZLEŞMESİ
    Pencere sınırları, imleç konumu ve tıklama koordinatları aynı uzaydadır:
    işletim sisteminin "ekran koordinatı" (Windows: süreç DPI-farkında, fiziksel
    piksel; macOS: nokta (point); Linux: mantıksal piksel). Çoklu monitörde
    sanal masaüstü negatif başlayabilir.

    Ekran görüntüsü bu uzayda olmayabilir (Retina'da 1 nokta = 2 piksel).
    `screenshot()` bu farkı `Screenshot.scale` (piksel / ekran birimi) ve
    `Screenshot.origin` (görüntünün sol üst köşesinin ekran koordinatı) ile
    bildirir. Ekran = origin + piksel / scale.

HATA SÖZLEŞMESİ
    Desteklenmeyen işlem `Unsupported` fırlatır (mesajı kullanıcıya ne
    kurması/açması gerektiğini söyler). Sessizce False dönmek yok.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..models import Screenshot

WINDOW_ACTIONS = ("activate", "close", "minimize")
BUTTONS = ("left", "right", "middle")


class Unsupported(RuntimeError):
    """Bu platformda/ortamda yapılamayan işlem."""


@dataclass
class WinInfo:
    """Ekrandaki üst düzey pencere (pencere yöneticisinin gördüğü)."""
    id: str                  # platforma özgü, `window_action` bunu alır
    title: str
    app: str                 # uygulama/sınıf adı (ör. org.kde.konsole, firefox, Code.exe)
    pid: Optional[int]
    x: int
    y: int
    w: int
    h: int
    active: bool = False
    minimized: bool = False


@dataclass
class Terminal:
    """Masaüstünde açık bir terminal (sekme / bölme / konsol)."""
    ref: str                 # <tür>:<kimlik>  ör. konsole:7891:2, tmux:s:0.1, wincon:4312
    title: str
    program: str             # ön plandaki süreç (claude, vim, bash, powershell…)
    cwd: str
    kind: str
    pid: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Cap:
    available: bool
    detail: str
    needs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"available": self.available, "detail": self.detail}
        if self.needs:
            d["needs"] = self.needs
        return d


class Desktop(ABC):
    """Bir işletim sistemi/masaüstü oturumu."""

    #: kısa ad: "kde-wayland", "x11", "gnome-wayland", "sway", "hyprland",
    #: "windows", "macos" …
    name: str = "base"

    # ------------------------------------------------------------ yetenekler
    @abstractmethod
    def caps(self) -> dict[str, Cap]:
        """Anahtarlar: windows, input, screenshot, terminals (hepsi zorunlu)."""

    def gui_backend(self):
        """Erişilebilirlik ağacı backend'i (Backend örneği) ya da None.
        Linux: AT-SPI, Windows: UI Automation, macOS: AX."""
        return None

    def setup(self) -> list[tuple[str, bool, str]]:
        """İlk kurulum adımları (izin iste, erişilebilirliği aç…).
        Dönüş: [(adım, başarılı, ayrıntı)]."""
        return []

    # --------------------------------------------------------------- pencere
    @abstractmethod
    def windows(self) -> list[WinInfo]:
        """Görünür/normal pencereler. Pencere listesi alınamıyorsa Unsupported."""

    @abstractmethod
    def window_action(self, wid: str, action: str) -> bool:
        """activate | close | minimize. Pencere yoksa False."""

    def active_window(self) -> Optional[WinInfo]:
        try:
            return next((w for w in self.windows() if w.active), None)
        except Unsupported:
            return None

    # ------------------------------------------------------- fare + klavye
    @abstractmethod
    def pointer_pos(self) -> Optional[tuple[int, int]]:
        """Kullanıcının imleci (bilinmiyorsa None)."""

    @abstractmethod
    def move(self, x: float, y: float) -> None: ...

    @abstractmethod
    def button(self, button: str, pressed: bool) -> None: ...

    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        import time
        self.move(x, y)
        time.sleep(0.03)
        for _ in range(max(1, min(int(count), 3))):
            self.button(button, True)
            time.sleep(0.012)
            self.button(button, False)
            time.sleep(0.05)

    @abstractmethod
    def scroll(self, dy: int, dx: int = 0) -> None:
        """Tekerlek adımı: +dy aşağı, +dx sağ."""

    @abstractmethod
    def key(self, combo: str) -> None:
        """ör. ctrl+shift+t, Return, alt+F4, cmd+space. Ortak adlar:
        ctrl alt shift super/win/cmd/meta; enter tab esc backspace delete
        insert home end pageup pagedown up down left right space f1-f24."""

    @abstractmethod
    def type_text(self, text: str) -> None:
        """Etkin pencereye Unicode metin yaz (Türkçe, emoji, CJK dahil)."""

    # ----------------------------------------------------------- ekran
    @abstractmethod
    def screenshot(self, path: str) -> Screenshot:
        """Tüm (sanal) masaüstünün PNG görüntüsü. scale/origin doldurulur
        (bkz. KOORDİNAT SÖZLEŞMESİ); backend = kullanılan yöntemin adı."""

    # ------------------------------------------- masaüstündeki terminaller
    @abstractmethod
    def terminals(self) -> list[Terminal]: ...

    @abstractmethod
    def terminal_read(self, ref: str, history: int = 0) -> str: ...

    @abstractmethod
    def terminal_send(self, ref: str, text: str = "", enter: bool = False,
                      keys: Optional[list[str]] = None) -> None: ...

    def terminal_history_supported(self, ref: str) -> bool:
        return False

    # ------------------------------------------------------------ çeşitli
    def paste_combo(self) -> str:
        """Etkin pencereye yapıştırma kısayolu."""
        return "ctrl+v"

    def close(self) -> None:
        return None


# ---------------------------------------------------------------- yardımcı
def runtime_dir() -> str:
    """Kullanıcıya özel geçici dizin (soketler, son ekran görüntüsü)."""
    d = os.environ.get("XDG_RUNTIME_DIR")
    if not d:
        import getpass
        import tempfile
        try:
            user = getpass.getuser()
        except Exception:
            user = "user"
        d = os.path.join(tempfile.gettempdir(), f"nexremote-{user}")
    os.makedirs(d, exist_ok=True)
    return d


def config_dir() -> str:
    """Kalıcı ayarlar (izin belirteçleri vb.)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif os.uname().sysname == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, "nexremote")
    os.makedirs(d, exist_ok=True)
    return d


def parse_combo(combo: str) -> tuple[list[str], str]:
    """'Ctrl+Shift+T' → (['ctrl','shift'], 't'). Değiştiriciler normalize edilir
    (control→ctrl, win/cmd/meta→super, option→alt). Ana tuş küçük harfe
    indirilir ama tek karakterli tuşlar olduğu gibi kalır ('+' gibi)."""
    if not combo or not combo.strip():
        raise ValueError("boş tuş kombinasyonu")
    raw = combo.strip()
    # sonda "+" tuşunun kendisi olabilir: "ctrl++"
    if raw.endswith("++"):
        parts, main = raw[:-2].split("+"), "+"
    else:
        *parts, main = raw.split("+")
    mods: list[str] = []
    for p in parts:
        m = p.strip().lower()
        m = {"control": "ctrl", "ctl": "ctrl", "win": "super", "cmd": "super",
             "command": "super", "meta": "super", "logo": "super",
             "option": "alt", "opt": "alt"}.get(m, m)
        if m not in ("ctrl", "alt", "shift", "super", "altgr"):
            raise ValueError(f"bilinmeyen değiştirici: {p!r}")
        if m not in mods:
            mods.append(m)
    main = main.strip()
    if not main:
        raise ValueError(f"ana tuş yok: {combo!r}")
    if len(main) > 1:
        main = main.lower()
        main = {"return": "enter", "escape": "esc", "del": "delete",
                "pgup": "pageup", "prior": "pageup", "pgdn": "pagedown",
                "next": "pagedown", "ins": "insert"}.get(main, main)
    return mods, main


# Terminale ham bayt olarak giden özel tuşlar (terminal_send keys=…)
_TERM_KEYS = {
    "enter": "\r", "return": "\r", "tab": "\t", "esc": "\x1b", "escape": "\x1b",
    "backspace": "\x7f", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C",
    "left": "\x1b[D", "home": "\x1b[H", "end": "\x1b[F", "pgup": "\x1b[5~",
    "pgdn": "\x1b[6~", "pageup": "\x1b[5~", "pagedown": "\x1b[6~",
    "delete": "\x1b[3~", "space": " ",
}


def term_key_bytes(name: str) -> str:
    """'C-c' / 'ctrl+c' / 'enter' / 'up' → terminal baytı."""
    import re
    low = name.strip().lower()
    if low in _TERM_KEYS:
        return _TERM_KEYS[low]
    m = re.fullmatch(r"(?:c-|ctrl\+)([a-z\[\\\]^_])", low)
    if m:
        return chr(ord(m.group(1).upper()) & 0x1f)
    raise ValueError(f"bilinmeyen tuş: {name}")


def term_payload(text: str, enter: bool, keys: Optional[list[str]]) -> str:
    payload = text + "".join(term_key_bytes(k) for k in (keys or []))
    if enter:
        payload += "\r"
    if not payload:
        raise ValueError("gönderilecek metin veya tuş yok")
    return payload


def home_short(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path and path.startswith(home) else path
