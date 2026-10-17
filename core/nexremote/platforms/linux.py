"""Linux masaüstü — oturuma göre en doğrudan kanalı seçer.

    pencereler   KWin betiği (KDE) → AT-SPI (yalnızca erişilebilirlik yayınlayanlar)
    giriş        RemoteDesktop portalı (Wayland, root'suz) → wtype/ydotool/xdotool
    ekran        spectacle / grim / gnome-screenshot / scrot
    terminaller  Konsole D-Bus + tmux

Her kanal ayrı bir sağlayıcıdır; ortam değiştikçe (X11, GNOME, Sway, Hyprland)
yeni sağlayıcı eklenir, facade değişmez.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
from typing import Optional

from ..models import Screenshot
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

    # ------------------------------------------------------------ yetenekler
    def caps(self) -> dict[str, Cap]:
        return {
            "windows": self._windows_cap(),
            "input": self._input_cap(),
            "screenshot": self._shot_cap(),
            "terminals": self._terminals_cap(),
        }

    def _windows_cap(self) -> Cap:
        from .. import kwin
        if kwin.available():
            return Cap(True, "KWin: ekrandaki tüm pencereler")
        return Cap(False, "KWin yok — yalnızca AT-SPI pencereleri")

    def _input_cap(self) -> Cap:
        from .. import portal_input
        ok, msg = portal_input.available()
        if ok:
            return Cap(True, msg)
        from .. import input as inp
        tools = [t for t, ok_ in inp.available_tools().items() if ok_]
        if tools:
            return Cap(True, f"klavye: {', '.join(tools)} (fare tıklaması için portal gerekir)")
        return Cap(False, msg, ["xdg-desktop-portal (RemoteDesktop)", "python3-dbus", "python3-gi"])

    def _shot_cap(self) -> Cap:
        tool = next((t for _, t in self._shot_tools("x") if shutil.which(t)), None)
        if tool:
            return Cap(True, tool)
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
        ok, msg = portal_input.available()
        if ok:
            if not portal_input.running():
                print("Ekranda 'Uzaktan kontrol' izni açılacak: 'Allow restoring on future "
                      "sessions' kutusunu işaretleyip Share'e basın (bir kez).", flush=True)
            try:
                portal_input.ensure()
                steps.append(("fare+klavye servisi çalışıyor", True, msg))
            except Exception as e:
                steps.append(("fare+klavye servisi", False, str(e)))
        else:
            steps.append(("fare+klavye", False, msg))
        return steps

    # --------------------------------------------------------------- pencere
    def windows(self) -> list[WinInfo]:
        from .. import kwin
        if not kwin.available():
            raise Unsupported("pencere yöneticisinden liste alınamıyor (KWin yok)")
        return [WinInfo(id=w["id"], title=w["title"], app=w["app"], pid=w["pid"],
                        x=w["x"], y=w["y"], w=w["w"], h=w["h"],
                        active=bool(w["active"]), minimized=bool(w.get("minimized")))
                for w in kwin.windows()]

    def window_action(self, wid: str, action: str) -> bool:
        from .. import kwin
        if not kwin.available():
            raise Unsupported("pencere eylemleri KWin ister")
        return kwin.act(wid, action)

    # ------------------------------------------------------- fare + klavye
    @staticmethod
    def _portal():
        from .. import portal_input
        ok, msg = portal_input.available()
        if not ok:
            raise Unsupported(f"fare için RemoteDesktop portalı gerekli: {msg}")
        return portal_input

    def pointer_pos(self) -> Optional[tuple[int, int]]:
        from .. import kwin
        try:
            return kwin.cursor_pos() if kwin.available() else None
        except Exception:
            return None

    def move(self, x: float, y: float) -> None:
        self._portal().call(f"MOVE {x:.1f} {y:.1f}")

    def button(self, button: str, pressed: bool) -> None:
        self._portal().call(f"{'DOWN' if pressed else 'UP'} {button}")

    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        # daemon tek çağrıda taşır+tıklar: olaylar arası zamanlama orada ayarlı
        self._portal().call(f"CLICK {int(x)} {int(y)} {button} {int(count)}")

    def scroll(self, dy: int, dx: int = 0) -> None:
        self._portal().call(f"SCROLL {int(dy)} {int(dx)}")

    def key(self, combo: str) -> None:
        from .. import portal_input
        if portal_input.available()[0]:
            portal_input.call("KEY " + combo)
            return
        from .. import input as inp
        if not inp.key(combo):
            raise Unsupported("klavye için RemoteDesktop portalı ya da wtype/ydotool/xdotool gerekli")

    def type_text(self, text: str) -> None:
        from .. import portal_input
        if portal_input.available()[0]:
            portal_input.type_text(text, paste_combo=self.paste_combo())
            return
        from .. import input as inp
        if not inp.type_text(text):
            raise Unsupported("yazmak için RemoteDesktop portalı ya da wtype/ydotool/xdotool gerekli")

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
                    w, h = _png_size(path)
                    return Screenshot(path=path, width=w, height=h, backend=tool,
                                      display=os.environ.get("WAYLAND_DISPLAY")
                                      or os.environ.get("DISPLAY"))
            except (OSError, subprocess.SubprocessError) as e:
                errors.append(f"{tool}: {e}")
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


def _png_size(path: str) -> tuple[int, int]:
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return (0, 0)
