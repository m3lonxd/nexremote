"""Giriş enjeksiyonu — Wayland (ve X11 fallback) uyumlu.

Harici kütüphane YOK; yalnızca sistem araçları (wtype / ydotool / dotool / xdotool).
Araç yoksa zarifçe False döner, import patlamaz.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

__all__ = [
    "available_tools", "type_text", "key", "click_at",
    "move_mouse", "key_supported",
]

# wtype tuş adları ↔ ortak isimler
_KEY_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
    "super": "logo", "meta": "logo", "cmd": "logo", "win": "logo",
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "backspace": "BackSpace",
    "delete": "Delete", "del": "Delete", "up": "Up", "down": "Down",
    "left": "Left", "right": "Right", "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5", "f6": "F6",
    "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}


def available_tools() -> dict[str, bool]:
    """Sistemde hangi giriş aracı var?"""
    return {t: shutil.which(t) is not None
            for t in ("wtype", "ydotool", "dotool", "xdotool")}


def key_supported() -> bool:
    t = available_tools()
    return any(t.values())


def _run(cmd: list[str], timeout: float = 5.0) -> bool:
    try:
        r = subprocess.run(cmd, timeout=timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False


def type_text(text: str) -> bool:
    """Aktif pencereye metin yaz (Wayland)."""
    if not text:
        return False
    t = available_tools()
    if t.get("wtype"):
        return _run(["wtype", "--", text])
    if t.get("ydotool"):
        return _run(["ydotool", "type", "--", text])
    if t.get("dotool"):
        try:
            p = subprocess.run(["dotool"], input=f"type {text}\n".encode(),
                               timeout=5.0)
            return p.returncode == 0
        except Exception:
            return False
    if t.get("xdotool"):  # yalnızca xwayland/X11 uygulamaları
        return _run(["xdotool", "type", "--", text])
    return False


def _parse_combo(combo: str) -> tuple[list[str], str]:
    """'ctrl+shift+t' -> (['ctrl','shift'], 't')"""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return [], ""
    return parts[:-1], parts[-1]


def key(combo: str) -> bool:
    """Tuş kombinasyonu gönder. Örnek: 'ctrl+c', 'alt+F4', 'Return'."""
    mods, main = _parse_combo(combo)
    if not main:
        return False
    t = available_tools()

    if t.get("wtype"):
        cmd = ["wtype"]
        for m in mods:
            cmd += ["-M", _KEY_ALIASES.get(m, m)]
        cmd += ["-k", _KEY_ALIASES.get(main, main)]
        for m in reversed(mods):
            cmd += ["-m", _KEY_ALIASES.get(m, m)]
        return _run(cmd)

    if t.get("ydotool"):
        seq = "+".join([_KEY_ALIASES.get(m, m) for m in mods] + [_KEY_ALIASES.get(main, main)])
        return _run(["ydotool", "key", seq])

    if t.get("xdotool"):
        seq = "+".join([m for m in mods] + [main])
        return _run(["xdotool", "key", seq])

    return False


def move_mouse(x: int, y: int) -> bool:
    t = available_tools()
    if t.get("ydotool"):
        return _run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)])
    if t.get("xdotool"):
        return _run(["xdotool", "mousemove", str(x), str(y)])
    return False


def click_at(x: int, y: int, button: int = 1) -> bool:
    """Fiziksel fare tıklaması (koordinat). AT-SPI yoksa son çare."""
    t = available_tools()
    if not move_mouse(x, y):
        return False
    time.sleep(0.05)
    if t.get("ydotool"):
        # ydotool click: 0x40=sol bas, 0x80=sol bırak (basit: 'click 1')
        return _run(["ydotool", "click", str(button)])
    if t.get("xdotool"):
        return _run(["xdotool", "click", str(button)])
    return False
