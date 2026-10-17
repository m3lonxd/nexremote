"""İşletim sistemi seçimi: `detect()` bu makine için doğru `Desktop`'u döndürür."""
from __future__ import annotations

import sys

from .base import Cap, Desktop, Terminal, Unsupported, WinInfo

__all__ = ["Cap", "Desktop", "Terminal", "Unsupported", "WinInfo", "detect"]


def detect() -> Desktop:
    if sys.platform == "win32":
        from .windows import WindowsDesktop
        return WindowsDesktop()
    if sys.platform == "darwin":
        from .macos import MacDesktop
        return MacDesktop()
    from .linux import LinuxDesktop
    return LinuxDesktop()
