"""Linux masaüstü — oturuma göre en doğrudan kanalı seçer.

    pencereler   KWin betiği (KDE) → hyprctl (Hyprland) → swaymsg (Sway) → EWMH (X11)
    giriş        XTEST (X11, izinsiz) → RemoteDesktop portalı (Wayland: KDE, GNOME,
                 wlroots) → wtype/ydotool/xdotool
    ekran        spectacle / grim / gnome-screenshot / scrot → Xlib (X11)
    terminaller  Konsole D-Bus + tmux

Her kanal ayrı bir sağlayıcıdır (kwin.py, wlroots.py, x11.py, portal_input.py);
facade yalnızca bu sınıfı görür.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

from ..models import Screenshot
from ._png import png_size
from .base import Cap, Desktop, Terminal, Unsupported, WinInfo


def session_kind() -> str:
    """'kde-wayland', 'gnome-wayland', 'sway', 'hyprland', 'wayland', 'x11' ya da 'tty'."""
    desk = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    stype = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or stype == "wayland"
    if wayland:
        if "kde" in desk:
            return "kde-wayland"
        if "gnome" in desk:
            return "gnome-wayland"
        if os.environ.get("SWAYSOCK") or "sway" in desk:
            return "sway"
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or "hyprland" in desk:
            return "hyprland"
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "tty"


class LinuxDesktop(Desktop):
    def __init__(self) -> None:
        self.name = session_kind()
        self._x11 = None
        self._wm: Optional[str] = None
        self._input: Optional[str] = None

    # ------------------------------------------------------- kanal seçimi
    def _x11_ok(self) -> bool:
        if self.name != "x11":
            return False  # XWayland yalnızca X uygulamalarını görür; yanıltıcı olur
        from . import x11
        return x11.available()[0]

    def _x(self):
        if self._x11 is None:
            from .x11 import X11
            self._x11 = X11()
        return self._x11

    def _wm_kind(self) -> str:
        """Pencere listesini veren kanal: kwin | hyprland | sway | x11 | ''."""
        if self._wm is None:
            from .. import kwin
            from .wlroots import Hyprland, Sway
            if kwin.available():
                self._wm = "kwin"
            elif self.name == "hyprland" and Hyprland.available():
                self._wm = "hyprland"
            elif self.name == "sway" and Sway.available():
                self._wm = "sway"
            elif self._x11_ok():
                self._wm = "x11"
            else:
                self._wm = ""
        return self._wm

    def _wm_provider(self):
        from .wlroots import Hyprland, Sway
        return {"hyprland": Hyprland, "sway": Sway}[self._wm_kind()]()

    def _input_kind(self) -> str:
        """Giriş kanalı: x11 | portal | tools | ''."""
        if self._input is None:
            from .. import input as inp
            from .. import portal_input
            if self._x11_ok():
                self._input = "x11"
            elif portal_input.available()[0]:
                self._input = "portal"
            elif any(inp.available_tools().values()):
                self._input = "tools"
            else:
                self._input = ""
        return self._input

    # ------------------------------------------------------------ yetenekler
    def caps(self) -> dict[str, Cap]:
        return {
            "windows": self._windows_cap(),
            "input": self._input_cap(),
            "screenshot": self._shot_cap(),
            "terminals": self._terminals_cap(),
        }

    def _windows_cap(self) -> Cap:
        kind = self._wm_kind()
        if kind:
            label = {"kwin": "KWin", "hyprland": "Hyprland IPC", "sway": "Sway IPC",
                     "x11": "X11 EWMH"}[kind]
            return Cap(True, f"{label}: ekrandaki tüm pencereler")
        needs = {"hyprland": ["hyprctl"], "sway": ["swaymsg"], "x11": ["python-xlib"]}
        return Cap(False, f"{self.name}: pencere listesi alınamıyor — yalnızca "
                          "erişilebilirlik ağacı yayınlayan pencereler",
                   needs.get(self.name, []))

    def _input_cap(self) -> Cap:
        from .. import input as inp
        from .. import portal_input
        kind = self._input_kind()
        if kind == "x11":
            from . import x11
            return Cap(True, x11.available()[1])
        if kind == "portal":
            return Cap(True, portal_input.available()[1])
        if kind == "tools":
            tools = [t for t, ok_ in inp.available_tools().items() if ok_]
            mouse = any(t in tools for t in ("ydotool", "xdotool"))
            return Cap(True, f"klavye: {', '.join(tools)}"
                       + ("" if mouse else " (fare için RemoteDesktop portalı ya da ydotool gerekir)"))
        return Cap(False, portal_input.available()[1],
                   ["xdg-desktop-portal (RemoteDesktop)", "python3-dbus", "python3-gi"]
                   if self.name != "x11" else ["python-xlib"])

    def _shot_cap(self) -> Cap:
        tool = next((t for _, t in self._shot_tools("x") if shutil.which(t)), None)
        if tool:
            return Cap(True, tool)
        if self._x11_ok():
            return Cap(True, "xlib (kök pencere)")
        return Cap(False, "ekran görüntüsü aracı yok", ["spectacle | grim | gnome-screenshot | scrot"])

    def _terminals_cap(self) -> Cap:
        try:
            n = len(self.terminals())
        except Exception as e:
            return Cap(False, str(e))
        return Cap(True, f"{n} açık terminal (Konsole D-Bus + tmux)")

    def gui_backend(self):
        from ..backends.gui import GuiBackend
        return GuiBackend()

    def setup(self) -> list[tuple[str, bool, str]]:
        from .. import portal_input
        steps = []
        try:
            r = subprocess.run(["busctl", "--user", "set-property", "org.a11y.Bus",
                                "/org/a11y/bus", "org.a11y.Status", "IsEnabled", "b", "true"],
                               capture_output=True)
            steps.append(("erişilebilirlik ağacı açıldı (yeni açılan uygulamalar için)",
                          r.returncode == 0, r.stderr.decode().strip()))
        except OSError as e:
            steps.append(("erişilebilirlik ağacı", False, f"busctl yok: {e}"))
        kind = self._input_kind()
        if kind == "x11":
            steps.append(("fare+klavye (XTEST, izin gerekmez)", True, ""))
        elif kind == "portal":
            if not portal_input.running():
                print("Ekranda 'Uzaktan kontrol' izni açılacak: 'Allow restoring on future "
                      "sessions' kutusunu işaretleyip Share'e basın (bir kez).", flush=True)
            try:
                portal_input.ensure()
                steps.append(("fare+klavye servisi çalışıyor", True, portal_input.available()[1]))
            except Exception as e:
                steps.append(("fare+klavye servisi", False, str(e)))
        else:
            steps.append(("fare+klavye", kind == "tools", self._input_cap().detail))
        return steps

    # --------------------------------------------------------------- pencere
    def windows(self) -> list[WinInfo]:
        kind = self._wm_kind()
        if kind == "kwin":
            from .. import kwin
            return [WinInfo(id=w["id"], title=w["title"], app=w["app"], pid=w["pid"],
                            x=w["x"], y=w["y"], w=w["w"], h=w["h"],
                            active=bool(w["active"]), minimized=bool(w.get("minimized")))
                    for w in kwin.windows()]
        if kind == "x11":
            return self._x().windows()
        if kind:
            return self._wm_provider().windows()
        raise Unsupported(self._windows_cap().detail)

    def window_action(self, wid: str, action: str) -> bool:
        kind = self._wm_kind()
        if kind == "kwin":
            from .. import kwin
            return kwin.act(wid, action)
        if kind == "x11":
            return self._x().act(wid, action)
        if kind:
            return self._wm_provider().act(wid, action)
        raise Unsupported(self._windows_cap().detail)

    # ------------------------------------------------------- fare + klavye
    def _mouse(self) -> str:
        kind = self._input_kind()
        if kind in ("x11", "portal"):
            return kind
        from .. import input as inp
        tools = inp.available_tools()
        if tools.get("ydotool") or tools.get("xdotool"):
            return "tools"
        raise Unsupported("fare için RemoteDesktop portalı (xdg-desktop-portal), "
                          "X11 oturumu ya da ydotool gerekli")

    def pointer_pos(self) -> Optional[tuple[int, int]]:
        kind = self._wm_kind()
        try:
            if kind == "kwin":
                from .. import kwin
                return kwin.cursor_pos()
            if kind == "x11":
                return self._x().pointer_pos()
            if kind in ("hyprland", "sway"):
                return self._wm_provider().pointer_pos()
        except Exception:
            return None  # konum isteğe bağlı bilgi (imleci geri koymak için)
        return None

    def move(self, x: float, y: float) -> None:
        m = self._mouse()
        if m == "x11":
            self._x().move(x, y)
        elif m == "portal":
            from .. import portal_input
            portal_input.call(f"MOVE {x:.1f} {y:.1f}")
        else:
            from .. import input as inp
            if not inp.move_mouse(int(x), int(y)):
                raise RuntimeError("fare taşınamadı (ydotool/xdotool hata verdi)")

    def button(self, button: str, pressed: bool) -> None:
        m = self._mouse()
        if m == "x11":
            self._x().button(button, pressed)
        elif m == "portal":
            from .. import portal_input
            portal_input.call(f"{'DOWN' if pressed else 'UP'} {button}")
        else:
            raise Unsupported("ydotool/xdotool ile ayrı bas/bırak desteklenmiyor — "
                              "sürükleme için RemoteDesktop portalı gerekli")

    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        m = self._mouse()
        if m == "portal":
            # daemon tek çağrıda taşır+tıklar: olaylar arası zamanlama orada ayarlı
            from .. import portal_input
            portal_input.call(f"CLICK {int(x)} {int(y)} {button} {int(count)}")
        elif m == "x11":
            super().click(x, y, button, count)
        else:
            from .. import input as inp
            btn = {"left": 1, "middle": 2, "right": 3}[button]
            for _ in range(max(1, min(int(count), 3))):
                if not inp.click_at(int(x), int(y), btn):
                    raise RuntimeError("tıklanamadı (ydotool/xdotool hata verdi)")

    def scroll(self, dy: int, dx: int = 0) -> None:
        m = self._mouse()
        if m == "x11":
            self._x().scroll(dy, dx)
        elif m == "portal":
            from .. import portal_input
            portal_input.call(f"SCROLL {int(dy)} {int(dx)}")
        else:
            raise Unsupported("kaydırma için RemoteDesktop portalı ya da X11 oturumu gerekli")

    def key(self, combo: str) -> None:
        kind = self._input_kind()
        if kind == "x11":
            self._x().key(combo)
        elif kind == "portal":
            from .. import portal_input
            portal_input.call("KEY " + combo)
        else:
            from .. import input as inp
            if not inp.key(combo):
                raise Unsupported("klavye için RemoteDesktop portalı ya da wtype/ydotool/xdotool gerekli")

    def type_text(self, text: str) -> None:
        kind = self._input_kind()
        if kind == "x11":
            self._x().type_text(text)
        elif kind == "portal":
            from .. import portal_input
            portal_input.type_text(text, paste_combo=self.paste_combo())
        else:
            from .. import input as inp
            if not inp.type_text(text):
                raise Unsupported("yazmak için RemoteDesktop portalı ya da wtype/ydotool/xdotool gerekli")

    def close(self) -> None:
        if self._x11 is not None:
            self._x11.close()

    _TERMINAL_APPS = ("konsole", "kitty", "alacritty", "foot", "wezterm", "terminal",
                      "xterm", "ghostty", "yakuake", "tilix", "terminator")

    def paste_combo(self) -> str:
        """Terminaller ctrl+v'yi yapıştırma olarak değil ^V olarak alır."""
        try:
            w = self.active_window()
        except Exception:
            w = None
        app = (w.app if w else "").lower()
        return "ctrl+shift+v" if any(t in app for t in self._TERMINAL_APPS) else "ctrl+v"

    # ----------------------------------------------------------- ekran
    @staticmethod
    def _shot_tools(path: str) -> list[tuple[list[str], str]]:
        return [
            (["spectacle", "-b", "-n", "-o", path], "spectacle"),   # KDE
            (["grim", path], "grim"),                               # wlroots
            (["gnome-screenshot", "-f", path], "gnome-screenshot"),
            (["scrot", "-o", path], "scrot"),                       # X11
        ]

    def screenshot(self, path: str) -> Screenshot:
        path = os.path.abspath(path)
        errors = []
        for cmd, tool in self._shot_tools(path):
            if not shutil.which(tool):
                continue
            try:
                if os.path.exists(path):
                    os.remove(path)
                subprocess.run(cmd, timeout=15, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, check=True)
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    w, h = png_size(path)
                    return Screenshot(path=path, width=w, height=h, backend=tool,
                                      display=os.environ.get("WAYLAND_DISPLAY")
                                      or os.environ.get("DISPLAY"))
            except (OSError, subprocess.SubprocessError) as e:
                errors.append(f"{tool}: {e}")
        if self._x11_ok():
            return self._x().screenshot(path)
        raise Unsupported("ekran görüntüsü alınamadı"
                          + (f" ({'; '.join(errors)})" if errors
                             else " — spectacle, grim, gnome-screenshot ya da scrot gerekli"))

    # ------------------------------------------- masaüstündeki terminaller
    def terminals(self) -> list[Terminal]:
        from .. import terminals
        return terminals.list_terminals()

    def terminal_read(self, ref: str, history: int = 0) -> str:
        from .. import terminals
        return terminals.read(ref, history)

    def terminal_send(self, ref: str, text: str = "", enter: bool = False,
                      keys: Optional[list[str]] = None) -> None:
        from .. import terminals
        terminals.send(ref, text, enter=enter, keys=keys)

    def terminal_history_supported(self, ref: str) -> bool:
        from .. import terminals
        return terminals.history_supported(ref)
