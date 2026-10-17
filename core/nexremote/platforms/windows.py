"""Windows masaüstü — yalnızca stdlib (ctypes); pywin32 gerekmez.

    pencereler   EnumWindows + DWM (gizlenmiş/cloaked pencereler elenir)
    giriş        SendInput (fare: sanal masaüstüne normalize mutlak konum;
                 klavye: sanal tuş + KEYEVENTF_UNICODE)
    ekran        GDI BitBlt — tüm sanal masaüstü, fiziksel piksel
    terminaller  konsol tamponları (AttachConsole, ayrı yardımcı süreçte: _wincon.py)
    öğe ağacı    UI Automation (backends/uia.py, `comtypes` kuruluysa)

KOORDİNAT: süreç başlarken Per-Monitor (v2) DPI-farkında yapılır; böylece
pencere dikdörtgenleri, imleç, SendInput ve ekran görüntüsü aynı fiziksel
piksel uzayındadır (Screenshot.scale = 1.0). Sanal masaüstü negatif
başlayabilir (sol/üst monitör) — görüntünün origin'i SM_X/YVIRTUALSCREEN'dir.

Modül Linux'ta da içe aktarılabilir (birim testleri): ctypes.windll'a yalnızca
`_Win32()` kurulurken, yani WindowsDesktop örneklenirken dokunulur.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from ..models import Screenshot
from ._png import bgrx_to_rgb, write_png
from .base import (BUTTONS, WINDOW_ACTIONS, Cap, Desktop, Terminal, Unsupported, WinInfo,
                   parse_combo, term_payload)

# Sabit genişlikli Win32 tipleri (wintypes Linux'ta farklı boyutlu — bkz. birim testleri)
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
LONG = ctypes.c_int32
BOOL = ctypes.c_int32
UINT = ctypes.c_uint32
HANDLE = ctypes.c_void_p          # HWND, HDC, HBITMAP, HGDIOBJ … hepsi işaretçi genişliğinde
ULONG_PTR = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
HRESULT = ctypes.c_int32

# ---------------------------------------------------------------- sabitler
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
_BUTTON_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
WHEEL_DELTA = 120
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
SM_SWAPBUTTON = 23
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
SW_MINIMIZE, SW_RESTORE = 6, 9
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x2
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
DIB_RGB_COLORS, BI_RGB = 0, 0
DETACHED_PROCESS, CREATE_NO_WINDOW = 0x00000008, 0x08000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Sanal tuş kodları
VK_LSHIFT, VK_LCONTROL, VK_LMENU, VK_RMENU, VK_LWIN = 0xA0, 0xA2, 0xA4, 0xA5, 0x5B
VK_RETURN, VK_TAB = 0x0D, 0x09
_MOD_VK = {"ctrl": VK_LCONTROL, "alt": VK_LMENU, "shift": VK_LSHIFT, "super": VK_LWIN,
           "altgr": VK_RMENU}
_NAMED_VK = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "space": 0x20,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91, "pause": 0x13,
    "printscreen": 0x2C, "print": 0x2C, "menu": 0x5D, "apps": 0x5D,
    "ctrl": VK_LCONTROL, "alt": VK_LMENU, "shift": VK_LSHIFT, "super": VK_LWIN,
    "altgr": VK_RMENU, "rctrl": 0xA3, "rshift": 0xA1, "rsuper": 0x5C,
    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD,
    "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1,
}
_NAMED_VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})       # F1=0x70 … F24=0x87
_NAMED_CHAR = {"plus": "+", "minus": "-", "comma": ",", "period": ".", "slash": "/",
               "backslash": "\\", "semicolon": ";", "equal": "=", "grave": "`",
               "apostrophe": "'", "bracketleft": "[", "bracketright": "]"}
# Genişletilmiş (E0 önekli) tuşlar: bayrak olmadan ok tuşları sayısal tuş takımına düşer
_EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
                0xA3, VK_RMENU, VK_LWIN, 0x5C, 0x5D, 0x90, 0x2C, 0x6F,
                0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB3}

# Konsol programı olarak tanınan süreçler (terminals())
SHELL_EXES = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe", "sh.exe", "zsh.exe", "fish.exe",
    "nu.exe", "wsl.exe", "ssh.exe", "python.exe", "python3.exe", "py.exe", "node.exe",
    "claude.exe", "codex.exe", "gemini.exe", "opencode.exe", "aider.exe", "deno.exe",
    "bun.exe", "ipython.exe", "irb.exe", "lua.exe", "telnet.exe", "ftp.exe",
    "nvim.exe", "vim.exe", "less.exe", "htop.exe", "btop.exe", "far.exe",
})
_SHELLS = ("cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe", "sh.exe", "zsh.exe",
           "fish.exe", "nu.exe", "wsl.exe")
_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_wincon.py")


# ------------------------------------------------------------ yapılar
class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", DWORD), ("biWidth", LONG), ("biHeight", LONG), ("biPlanes", WORD),
                ("biBitCount", WORD), ("biCompression", DWORD), ("biSizeImage", DWORD),
                ("biXPelsPerMeter", LONG), ("biYPelsPerMeter", LONG), ("biClrUsed", DWORD),
                ("biClrImportant", DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 1)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", DWORD), ("cntUsage", DWORD), ("th32ProcessID", DWORD),
                ("th32DefaultHeapID", ULONG_PTR), ("th32ModuleID", DWORD),
                ("cntThreads", DWORD), ("th32ParentProcessID", DWORD),
                ("pcPriClassBase", LONG), ("dwFlags", DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


# ------------------------------------------------------- saf yardımcılar
KeyEvent = tuple[int, bool, bool]       # (vk, genişletilmiş, bırakma)


def combo_events(combo: str, vkscan: Optional[Callable[[str], int]] = None) -> list[KeyEvent]:
    """'ctrl+shift+t' → [(vk, extended, up), …]: değiştiriciler basılır, ana tuş
    basılıp bırakılır, değiştiriciler ters sırada bırakılır.

    `vkscan(ch)` VkKeyScanExW sonucu döndürür (düşük bayt vk, yüksek bayt
    shift=1/ctrl=2/alt=4; -1 = bu düzende yok). Harf ve rakamlar düzenden
    bağımsız eşlenir; diğer tek karakterler (noktalama, ğ, ş…) vkscan ister."""
    mods, main = parse_combo(combo)
    vk = _NAMED_VK.get(main)
    if vk is None and main in _NAMED_CHAR:
        main = _NAMED_CHAR[main]
    if vk is None:
        if len(main) != 1:
            raise ValueError(f"bilinmeyen tuş: {main!r}")
        if "a" <= main <= "z" or "0" <= main <= "9":
            vk = ord(main.upper())
        elif "A" <= main <= "Z":
            vk = ord(main)
            if "shift" not in mods:
                mods.append("shift")
        else:
            if vkscan is None:
                raise ValueError(f"'{main}' için klavye düzeni gerekli")
            r = vkscan(main)
            if r == -1 or (r & 0xFF) == 0xFF:
                raise ValueError(f"'{main}' bu klavye düzeninde tek tuşla yazılamıyor")
            vk, shift_state = r & 0xFF, (r >> 8) & 0xFF
            for bit, m in ((1, "shift"), (2, "ctrl"), (4, "alt")):
                if shift_state & bit and m not in mods:
                    mods.append(m)
    seq = [_MOD_VK[m] for m in mods if _MOD_VK[m] != vk]
    out: list[KeyEvent] = [(m, m in _EXTENDED_VK, False) for m in seq]
    out += [(vk, vk in _EXTENDED_VK, False), (vk, vk in _EXTENDED_VK, True)]
    out += [(m, m in _EXTENDED_VK, True) for m in reversed(seq)]
    return out


def text_units(text: str) -> list[tuple[str, int]]:
    """Metni SendInput birimlerine böl: ('vk', VK_RETURN|VK_TAB) ya da ('unit', utf16).
    \\r\\n, \\r ve \\n tek Enter olur; BMP dışı karakterler (emoji) vekil çiftine
    ayrılır — her UTF-16 birimi ayrı basma/bırakma çifti olarak gönderilir."""
    out: list[tuple[str, int]] = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        u = raw[i] | (raw[i + 1] << 8)
        if u == 0x0A:
            out.append(("vk", VK_RETURN))
        elif u == 0x09:
            out.append(("vk", VK_TAB))
        else:
            out.append(("unit", u))
    return out


def normalize_abs(v: int, origin: int, size: int) -> int:
    """Ekran koordinatı → MOUSEEVENTF_VIRTUALDESK mutlak değeri (0..65535).
    Windows geri dönüşümü piksel = floor(n * size / 65536) ile, Wine ise
    MulDiv(n, size, 65535) (yuvarlama) ile yapar. n = tavan(v * 65536 / size)
    her iki formülde de aynı pikseli verir (v < 32767 için)."""
    if size <= 0:
        raise ValueError("sanal ekran boyutu sıfır")
    return max(0, min(65535, -(-(v - origin) * 65536 // size)))


def parse_hwnd(wid: str) -> int:
    s = (wid or "").strip()
    if s.startswith("win:"):
        s = s[4:]
    try:
        h = int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        raise ValueError(f"geçersiz pencere kimliği: {wid!r} (ör. 0x1a2b)") from None
    if h <= 0:
        raise ValueError(f"geçersiz pencere kimliği: {wid!r}")
    return h


def parse_wincon(ref: str) -> int:
    if not ref.startswith("wincon:"):
        raise ValueError(f"geçersiz terminal ref: {ref!r} (wincon:PID)")
    try:
        pid = int(ref[len("wincon:"):])
    except ValueError:
        raise ValueError(f"geçersiz terminal ref: {ref!r} (wincon:PID)") from None
    if pid <= 0:
        raise ValueError(f"geçersiz terminal ref: {ref!r}")
    return pid


def console_summary(console: dict, procs: dict[int, tuple[int, str]]) -> tuple[int, str]:
    """Bir konsolun (yardımcının döndürdüğü üye listesi) kök kabuğunu ve ön
    plandaki programını seç. procs: pid → (ebeveyn pid, exe adı küçük harf).
    Kök: ebeveyni konsolda olmayan üye (tercihen kabuk). Program: kökten en
    derindeki torun — kabuktan başlatılan claude/vim/python…"""
    members = [p for p in console.get("members", []) if p in procs] or [console["pid"]]
    mset = set(members)
    roots = [p for p in members if procs.get(p, (0, ""))[0] not in mset]
    roots.sort(key=lambda p: (procs.get(p, (0, ""))[1] not in _SHELLS, p))
    root = roots[0] if roots else members[0]

    def depth(p: int) -> int:
        d, seen = 0, set()
        while p != root and p in mset and p not in seen:
            seen.add(p)
            p = procs.get(p, (0, ""))[0]
            d += 1
        return d if p == root else -1

    best = max(members, key=lambda p: (depth(p), p))
    exe = procs.get(best, (0, ""))[1] or procs.get(root, (0, ""))[1]
    program = exe[:-4] if exe.endswith(".exe") else exe
    return root, program


# ------------------------------------------------------------ Win32 erişimi
_dpi_lock = threading.Lock()
_dpi_mode = ""


def make_dpi_aware() -> str:
    """Süreci Per-Monitor v2 DPI-farkında yap (bir kez). Dönüş: etkin kip."""
    global _dpi_mode
    with _dpi_lock:
        if _dpi_mode:
            return _dpi_mode
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        try:
            f = user32.SetProcessDpiAwarenessContext
            f.restype, f.argtypes = BOOL, [HANDLE]
            if f(ctypes.c_void_p(-4)):                # PER_MONITOR_AWARE_V2
                _dpi_mode = "per-monitor-v2"
                return _dpi_mode
            if ctypes.get_last_error() == 5:          # ERROR_ACCESS_DENIED: zaten ayarlı
                _dpi_mode = "önceden ayarlı (manifest/başka kod)"
                return _dpi_mode
        except AttributeError:
            pass                                      # Windows 10 1703 öncesi
        try:
            shcore = ctypes.WinDLL("shcore", use_last_error=True)
            f = shcore.SetProcessDpiAwareness
            f.restype, f.argtypes = HRESULT, [ctypes.c_int]
            hr = f(2)                                 # PROCESS_PER_MONITOR_DPI_AWARE
            if hr == 0:
                _dpi_mode = "per-monitor"
                return _dpi_mode
            if hr == -2147024891:                     # E_ACCESSDENIED: zaten ayarlı
                _dpi_mode = "önceden ayarlı (manifest/başka kod)"
                return _dpi_mode
        except (OSError, AttributeError):
            pass                                      # Windows 8.1 öncesi
        f = user32.SetProcessDPIAware
        f.restype, f.argtypes = BOOL, []
        _dpi_mode = "system" if f() else "yok (ölçekli koordinatlar olabilir)"
        return _dpi_mode


class _Win32:
    """Kullanılan her Win32 fonksiyonu, argtypes/restype tanımlı olarak."""

    def __init__(self) -> None:
        P = ctypes.POINTER
        self.user32 = u = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.gdi32 = g = ctypes.WinDLL("gdi32", use_last_error=True)
        try:
            self.dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        except OSError:
            self.dwmapi = None                        # DWM yok (çok eski / sunucu çekirdeği)

        def fn(dll, name, res, *args):
            f = getattr(dll, name)
            f.restype, f.argtypes = res, list(args)
            setattr(self, name, f)

        self.WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HANDLE, LPARAM)
        fn(u, "EnumWindows", BOOL, self.WNDENUMPROC, LPARAM)
        fn(u, "EnumChildWindows", BOOL, HANDLE, self.WNDENUMPROC, LPARAM)
        fn(u, "IsWindow", BOOL, HANDLE)
        fn(u, "IsWindowVisible", BOOL, HANDLE)
        fn(u, "IsIconic", BOOL, HANDLE)
        fn(u, "GetWindowTextLengthW", ctypes.c_int, HANDLE)
        fn(u, "GetWindowTextW", ctypes.c_int, HANDLE, ctypes.c_wchar_p, ctypes.c_int)
        fn(u, "GetClassNameW", ctypes.c_int, HANDLE, ctypes.c_wchar_p, ctypes.c_int)
        fn(u, "GetWindowThreadProcessId", DWORD, HANDLE, P(DWORD))
        fn(u, "GetWindowRect", BOOL, HANDLE, P(RECT))
        fn(u, "GetForegroundWindow", HANDLE)
        fn(u, "SetForegroundWindow", BOOL, HANDLE)
        fn(u, "BringWindowToTop", BOOL, HANDLE)
        fn(u, "ShowWindow", BOOL, HANDLE, ctypes.c_int)
        fn(u, "ShowWindowAsync", BOOL, HANDLE, ctypes.c_int)
        fn(u, "PostMessageW", BOOL, HANDLE, UINT, WPARAM, LPARAM)
        fn(u, "AttachThreadInput", BOOL, DWORD, DWORD, BOOL)
        if hasattr(u, "GetWindowLongPtrW"):
            fn(u, "GetWindowLongPtrW", LPARAM, HANDLE, ctypes.c_int)
        else:                                         # 32 bit Python
            fn(u, "GetWindowLongW", LONG, HANDLE, ctypes.c_int)
            self.GetWindowLongPtrW = self.GetWindowLongW
        fn(u, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
        fn(u, "GetCursorPos", BOOL, P(POINT))
        fn(u, "SetCursorPos", BOOL, ctypes.c_int, ctypes.c_int)
        fn(u, "SendInput", UINT, UINT, P(INPUT), ctypes.c_int)
        fn(u, "MapVirtualKeyW", UINT, UINT, UINT)
        fn(u, "VkKeyScanExW", ctypes.c_short, ctypes.c_wchar, HANDLE)
        fn(u, "GetKeyboardLayout", HANDLE, DWORD)
        fn(u, "GetDC", HANDLE, HANDLE)
        fn(u, "ReleaseDC", ctypes.c_int, HANDLE, HANDLE)
        fn(g, "CreateCompatibleDC", HANDLE, HANDLE)
        fn(g, "CreateDIBSection", HANDLE, HANDLE, P(BITMAPINFO), UINT, P(ctypes.c_void_p),
           HANDLE, DWORD)
        fn(g, "SelectObject", HANDLE, HANDLE, HANDLE)
        fn(g, "BitBlt", BOOL, HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
           HANDLE, ctypes.c_int, ctypes.c_int, DWORD)
        fn(g, "DeleteObject", BOOL, HANDLE)
        fn(g, "DeleteDC", BOOL, HANDLE)
        fn(g, "GdiFlush", BOOL)
        fn(k, "GetCurrentThreadId", DWORD)
        fn(k, "OpenProcess", HANDLE, DWORD, BOOL, DWORD)
        fn(k, "CloseHandle", BOOL, HANDLE)
        fn(k, "QueryFullProcessImageNameW", BOOL, HANDLE, DWORD, ctypes.c_wchar_p, P(DWORD))
        fn(k, "CreateToolhelp32Snapshot", HANDLE, DWORD, DWORD)
        fn(k, "Process32FirstW", BOOL, HANDLE, P(PROCESSENTRY32W))
        fn(k, "Process32NextW", BOOL, HANDLE, P(PROCESSENTRY32W))
        if self.dwmapi is not None:
            fn(self.dwmapi, "DwmGetWindowAttribute", HRESULT, HANDLE, DWORD, ctypes.c_void_p,
               DWORD)


def _winerr(what: str) -> OSError:
    code = ctypes.get_last_error()
    return ctypes.WinError(code, f"{what} başarısız (Win32 hata {code})")


class WindowsDesktop(Desktop):
    name = "windows"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise Unsupported("WindowsDesktop yalnızca Windows'ta çalışır")
        self.dpi_mode = make_dpi_aware()
        self.w = _Win32()
        self._exe_cache: dict[int, str] = {}

    # ------------------------------------------------------------ yetenekler
    def caps(self) -> dict[str, Cap]:
        try:
            n = len(self.terminals())
            term = Cap(True, f"{n} açık konsol (AttachConsole; Windows Terminal/ConPTY dahil)")
        except Unsupported as e:
            term = Cap(False, str(e), ["python.exe (konsol yardımcısı için)"])
        except OSError as e:
            term = Cap(False, f"konsollar listelenemedi: {e}")
        return {
            "windows": Cap(True, f"Win32 EnumWindows (DPI: {self.dpi_mode}, fiziksel piksel)"),
            "input": Cap(True, "SendInput fare + klavye (Unicode); yönetici olarak çalışan "
                               "pencerelere UIPI nedeniyle yalnızca yönetici NexRemote ulaşır"),
            "screenshot": Cap(True, "GDI BitBlt — tüm sanal masaüstü"),
            "terminals": term,
        }

    def gui_backend(self):
        try:
            import comtypes  # noqa: F401
        except ImportError:
            return None
        from ..backends.uia import UiaBackend
        return UiaBackend(self)

    def setup(self) -> list[tuple[str, bool, str]]:
        steps = [("DPI farkındalığı", self.dpi_mode.startswith("per-monitor")
                  or self.dpi_mode.startswith("önceden"), self.dpi_mode),
                 ("izin", True, "Windows'ta fare/klavye/ekran için ayrıca izin gerekmez")]
        try:
            import comtypes  # noqa: F401
            from ..backends.uia import UiaBackend
            cap = UiaBackend(self).capability()
            steps.append(("UI Automation öğe ağacı", cap.available, cap.detail))
        except ImportError:
            steps.append(("UI Automation öğe ağacı", False,
                          "`pip install comtypes` ile isimle bul/tıkla açılır"))
        ok = os.path.isfile(_HELPER) and not getattr(sys, "frozen", False)
        steps.append(("konsol yardımcısı", ok, _HELPER if ok else
                      "konsol okuma/yazma python.exe ile çalışan NexRemote ister"))
        return steps

    # --------------------------------------------------------------- pencere
    def _exe_name(self, pid: int) -> str:
        if pid in self._exe_cache:
            return self._exe_cache[pid]
        name = ""
        h = self.w.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = DWORD(1024)
                if self.w.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    name = buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
            finally:
                self.w.CloseHandle(h)
        # korumalı/sistem süreçlerinde açılamaz — ad boş kalır (yalnızca bilgi alanı)
        self._exe_cache[pid] = name
        return name

    def _text(self, hwnd: int) -> str:
        n = self.w.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 2)
        self.w.GetWindowTextW(hwnd, buf, n + 2)
        return buf.value

    def _class(self, hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        self.w.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _pid(self, hwnd: int) -> int:
        pid = DWORD()
        self.w.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _cloaked(self, hwnd: int) -> bool:
        if self.w.dwmapi is None:
            return False
        v = DWORD()
        hr = self.w.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), 4)
        return hr == 0 and v.value != 0          # hata = DWM bilmiyor → gizli sayma

    def _rect(self, hwnd: int) -> RECT:
        r = RECT()
        if self.w.dwmapi is not None and self.w.DwmGetWindowAttribute(
                hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) == 0 \
                and r.right > r.left:
            return r                              # görünür çerçeve (gölge payı hariç)
        if not self.w.GetWindowRect(hwnd, ctypes.byref(r)):
            raise _winerr("GetWindowRect")
        return r

    def _uwp_host(self, hwnd: int, pid: int) -> tuple[int, str]:
        """ApplicationFrameHost penceresinin içindeki asıl UWP uygulaması."""
        found: list[int] = []

        def cb(h, _):
            p = self._pid(h)
            if p != pid:
                found.append(p)
                return False
            return True

        self.w.EnumChildWindows(hwnd, self.w.WNDENUMPROC(cb), 0)
        if found:
            return found[0], self._exe_name(found[0])
        return pid, self._exe_name(pid)

    def windows(self) -> list[WinInfo]:
        hwnds: list[int] = []

        def cb(h, _):
            hwnds.append(h)
            return True

        if not self.w.EnumWindows(self.w.WNDENUMPROC(cb), 0):
            raise _winerr("EnumWindows")
        fg = self.w.GetForegroundWindow() or 0
        self._exe_cache.clear()
        out: list[WinInfo] = []
        for h in hwnds:
            if not h or not self.w.IsWindowVisible(h):
                continue
            if self.w.GetWindowLongPtrW(h, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                continue
            title = self._text(h)
            if not title or self._class(h) in ("Progman", "WorkerW"):
                continue
            if self._cloaked(h):
                continue
            try:
                r = self._rect(h)
            except OSError:
                continue                          # pencere bu arada kapandı
            pid = self._pid(h)
            app = self._exe_name(pid)
            if app.lower() == "applicationframehost.exe":
                pid, app = self._uwp_host(h, pid)
            out.append(WinInfo(id=f"0x{h:x}", title=title, app=app, pid=pid or None,
                               x=r.left, y=r.top, w=r.right - r.left, h=r.bottom - r.top,
                               active=h == fg, minimized=bool(self.w.IsIconic(h))))
        return out

    def window_action(self, wid: str, action: str) -> bool:
        if action not in WINDOW_ACTIONS:
            raise ValueError(f"bilinmeyen pencere eylemi: {action} ({', '.join(WINDOW_ACTIONS)})")
        h = parse_hwnd(wid)
        if not self.w.IsWindow(h):
            return False
        if action == "close":
            if not self.w.PostMessageW(h, WM_CLOSE, 0, 0):
                raise _winerr("PostMessageW(WM_CLOSE)")
            return True
        if action == "minimize":
            self.w.ShowWindowAsync(h, SW_MINIMIZE)   # dönüş değeri önceki görünürlük
            return True
        return self._activate(h)

    def _activate(self, h: int) -> bool:
        """Ön plan kilidini aşarak etkinleştir. Windows yalnızca son girdiyi
        alan sürecin ön planı değiştirmesine izin verir; sırasıyla denenir:
        ön plan iş parçacığına girdi bağlama, boş SendInput, Alt dokunuşu."""
        w = self.w
        if w.IsIconic(h):
            w.ShowWindow(h, SW_RESTORE)
        me = w.GetCurrentThreadId()

        def attempt() -> bool:
            fg = w.GetForegroundWindow()
            fg_tid = w.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(fg_tid and fg_tid != me and w.AttachThreadInput(me, fg_tid, True))
            try:
                w.SetForegroundWindow(h)
                w.BringWindowToTop(h)
            finally:
                if attached:
                    w.AttachThreadInput(me, fg_tid, False)
            return self._wait_foreground(h)

        if attempt():
            return True
        self._send([self._mouse_input(0, 0, 0)])          # "son girdi bizden" sayılsın
        if attempt():
            return True
        self._send([self._key_input(VK_LMENU, False, False)])
        try:
            ok = attempt()
        finally:
            self._send([self._key_input(VK_LMENU, False, True)])
        if ok:
            return True
        if not w.IsWindow(h):
            return False
        raise RuntimeError("pencere öne getirilemedi (Windows ön plan kilidi); "
                           "pencere başka bir masaüstünde ya da yönetici olarak çalışıyor olabilir")

    def _wait_foreground(self, h: int, timeout: float = 0.5) -> bool:
        end = time.monotonic() + timeout
        while True:
            if (self.w.GetForegroundWindow() or 0) == h:
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.02)

    # ------------------------------------------------------- fare + klavye
    def _virtual_screen(self) -> tuple[int, int, int, int]:
        m = self.w.GetSystemMetrics
        return (m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN),
                m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN))

    @staticmethod
    def _mouse_input(dx: int, dy: int, flags: int, data: int = 0) -> INPUT:
        i = INPUT(type=INPUT_MOUSE)
        i.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        return i

    def _key_input(self, vk: int, extended: bool, up: bool) -> INPUT:
        i = INPUT(type=INPUT_KEYBOARD)
        flags = (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if up else 0)
        i.u.ki = KEYBDINPUT(vk, self.w.MapVirtualKeyW(vk, 0) & 0xFFFF, flags, 0, 0)
        return i

    @staticmethod
    def _unicode_input(unit: int, up: bool) -> INPUT:
        i = INPUT(type=INPUT_KEYBOARD)
        i.u.ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
        return i

    def _send(self, inputs: list[INPUT]) -> None:
        if not inputs:
            return
        arr = (INPUT * len(inputs))(*inputs)
        n = self.w.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if n != len(inputs):
            raise _winerr(f"SendInput ({n}/{len(inputs)} olay)")

    def pointer_pos(self) -> Optional[tuple[int, int]]:
        p = POINT()
        if not self.w.GetCursorPos(ctypes.byref(p)):
            return None                  # güvenli masaüstü (UAC/kilit ekranı): bilinmiyor
        return (p.x, p.y)

    def move(self, x: float, y: float) -> None:
        xi, yi = int(round(x)), int(round(y))
        vx, vy, vw, vh = self._virtual_screen()
        if not (vx <= xi < vx + vw and vy <= yi < vy + vh):
            raise ValueError(f"({xi}, {yi}) sanal ekranın dışında "
                             f"({vx}, {vy}, {vw}x{vh})")
        self._send([self._mouse_input(normalize_abs(xi, vx, vw), normalize_abs(yi, vy, vh),
                                      MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                                      | MOUSEEVENTF_VIRTUALDESK)])
        # Olay eşzamansız işlenir; yerine oturmasını bekle. Ölçek yuvarlaması
        # (farklı sürücü/Wine) bir piksel kaydırdıysa kesin konuma oturt.
        end = time.monotonic() + 0.1
        while self.pointer_pos() != (xi, yi) and time.monotonic() < end:
            time.sleep(0.005)
        if self.pointer_pos() != (xi, yi) and not self.w.SetCursorPos(xi, yi):
            raise _winerr("SetCursorPos")

    def button(self, button: str, pressed: bool) -> None:
        if button not in BUTTONS:
            raise ValueError(f"bilinmeyen fare düğmesi: {button} ({', '.join(BUTTONS)})")
        if button in ("left", "right") and self.w.GetSystemMetrics(SM_SWAPBUTTON):
            button = "right" if button == "left" else "left"   # solak ayarı: fiziksel düğme
        down, up = _BUTTON_FLAGS[button]
        self._send([self._mouse_input(0, 0, down if pressed else up)])

    def scroll(self, dy: int, dx: int = 0) -> None:
        # WHEEL: pozitif = ileri (yukarı) → +dy aşağı için eksi; HWHEEL: pozitif = sağ
        for _ in range(abs(int(dy))):
            self._send([self._mouse_input(0, 0, MOUSEEVENTF_WHEEL,
                                          -WHEEL_DELTA if dy > 0 else WHEEL_DELTA)])
            time.sleep(0.01)
        for _ in range(abs(int(dx))):
            self._send([self._mouse_input(0, 0, MOUSEEVENTF_HWHEEL,
                                          WHEEL_DELTA if dx > 0 else -WHEEL_DELTA)])
            time.sleep(0.01)

    def _vkscan(self, ch: str) -> int:
        fg = self.w.GetForegroundWindow()
        tid = self.w.GetWindowThreadProcessId(fg, None) if fg else 0
        return int(self.w.VkKeyScanExW(ch, self.w.GetKeyboardLayout(tid)))

    def key(self, combo: str) -> None:
        events = combo_events(combo, self._vkscan)
        # tek SendInput: kullanıcının girdisi araya giremez
        self._send([self._key_input(vk, ext, up) for vk, ext, up in events])

    def type_text(self, text: str) -> None:
        if not text:
            return
        inputs: list[INPUT] = []
        for kind, code in text_units(text):
            if kind == "vk":
                inputs += [self._key_input(code, False, False), self._key_input(code, False, True)]
            else:
                inputs += [self._unicode_input(code, False), self._unicode_input(code, True)]
        # parça parça: hedefin ileti kuyruğu taşmasın (Electron/eski Win32 denetimleri)
        for i in range(0, len(inputs), 64):
            self._send(inputs[i:i + 64])
            time.sleep(0.005)

    def paste_combo(self) -> str:
        """mintty (Git Bash) ctrl+v'yi yapıştırma saymaz; shift+insert her yerde
        çalışır. Klasik conhost 'Ctrl tuşu kısayolları' kapalıysa da ctrl+v
        çalışmaz — Windows 10+ varsayılanı açık, Windows Terminal da ctrl+v alır."""
        w = self.active_window()
        if w and w.app.lower() in ("mintty.exe",):
            return "shift+insert"
        return "ctrl+v"

    # ----------------------------------------------------------- ekran
    def screenshot(self, path: str) -> Screenshot:
        path = os.path.abspath(path)
        vx, vy, vw, vh = self._virtual_screen()
        if vw <= 0 or vh <= 0:
            raise Unsupported("sanal ekran boyutu alınamadı (masaüstü oturumu yok mu?)")
        w = self.w
        screen = w.GetDC(None)
        if not screen:
            raise _winerr("GetDC")
        mem = bmp = old = None
        try:
            mem = w.CreateCompatibleDC(screen)
            if not mem:
                raise _winerr("CreateCompatibleDC")
            bmi = BITMAPINFO()
            h = bmi.bmiHeader
            h.biSize, h.biWidth, h.biHeight = ctypes.sizeof(BITMAPINFOHEADER), vw, -vh  # üstten alta
            h.biPlanes, h.biBitCount, h.biCompression = 1, 32, BI_RGB
            bits = ctypes.c_void_p()
            bmp = w.CreateDIBSection(screen, ctypes.byref(bmi), DIB_RGB_COLORS,
                                     ctypes.byref(bits), None, 0)
            if not bmp or not bits.value:
                raise _winerr("CreateDIBSection")
            old = w.SelectObject(mem, bmp)
            if not w.BitBlt(mem, 0, 0, vw, vh, screen, vx, vy, SRCCOPY | CAPTUREBLT):
                raise _winerr("BitBlt")
            w.GdiFlush()
            data = ctypes.string_at(bits.value, vw * vh * 4)
        finally:
            if mem:
                if old:
                    w.SelectObject(mem, old)
                w.DeleteDC(mem)
            if bmp:
                w.DeleteObject(bmp)
            w.ReleaseDC(None, screen)
        write_png(path, vw, vh, bgrx_to_rgb(data))
        return Screenshot(path=path, width=vw, height=vh, backend="gdi", scale=1.0,
                          origin=(vx, vy), display="windows")

    # ------------------------------------------- masaüstündeki terminaller
    def _processes(self) -> dict[int, tuple[int, str]]:
        snap = self.w.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            raise _winerr("CreateToolhelp32Snapshot")
        procs: dict[int, tuple[int, str]] = {}
        try:
            e = PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = self.w.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                procs[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile.lower())
                ok = self.w.Process32NextW(snap, ctypes.byref(e))
        finally:
            self.w.CloseHandle(snap)
        return procs

    def _helper(self, req: dict, timeout: float = 15.0) -> dict:
        if getattr(sys, "frozen", False) or not sys.executable:
            raise Unsupported("konsol erişimi python.exe ile çalışan NexRemote ister "
                              "(paketlenmiş/frozen sürümde yardımcı süreç başlatılamaz)")
        if not os.path.isfile(_HELPER):
            raise Unsupported(f"konsol yardımcısı bulunamadı: {_HELPER}")
        try:
            r = subprocess.run([sys.executable, "-I", "-X", "utf8", _HELPER],
                               input=json.dumps(req).encode("utf-8"), capture_output=True,
                               timeout=timeout, creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"konsol yardımcısı {timeout:.0f} sn içinde yanıt vermedi") from None
        try:
            res = json.loads(r.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            err = r.stderr.decode("utf-8", "replace").strip()[-500:]
            raise RuntimeError(f"konsol yardımcısı çöktü (çıkış {r.returncode}): {err}") from None
        if not res.get("ok"):
            code = res.get("winerror")
            if code in (5, 6, 31, 87):
                # 87: süreç yok/konsolu yok, 5: erişim yok (yönetici konsolu), 6/31: kapandı
                raise RuntimeError(f"konsola bağlanılamadı: {res.get('error')} — süreç kapanmış, "
                                   "konsolsuz ya da yönetici olarak çalışıyor olabilir")
            raise RuntimeError(f"konsol işlemi başarısız: {res.get('error')}")
        return res

    def terminals(self) -> list[Terminal]:
        procs = self._processes()
        me = os.getpid()
        cands = [p for p, (_, exe) in procs.items() if exe in SHELL_EXES and p != me]
        # kabuklar önce: konsolun kökü genellikle kabuktur
        cands.sort(key=lambda p: (procs[p][1] not in _SHELLS, p))
        if not cands:
            return []
        res = self._helper({"op": "list", "pids": cands})
        out: list[Terminal] = []
        for c in res.get("consoles", []):
            root, program = console_summary(c, procs)
            out.append(Terminal(ref=f"wincon:{root}", title=c.get("title", ""),
                                program=program, cwd="", kind="wincon", pid=root))
        # cwd boş: başka sürecin çalışma dizini yalnızca PEB'i (ProcessParameters →
        # CurrentDirectory) ReadProcessMemory ile okuyarak alınabilir — belgelenmemiş
        # yapı ve WOW64/korumalı süreç ayrımları gerektirdiği için okunmuyor.
        return out

    def terminal_read(self, ref: str, history: int = 0) -> str:
        pid = parse_wincon(ref)
        return self._helper({"op": "read", "pid": pid, "history": max(0, int(history))})["text"]

    def terminal_send(self, ref: str, text: str = "", enter: bool = False,
                      keys: Optional[list[str]] = None) -> None:
        pid = parse_wincon(ref)
        payload = term_payload(text, enter, keys)
        self._helper({"op": "send", "pid": pid, "payload": payload})

    def terminal_history_supported(self, ref: str) -> bool:
        return ref.startswith("wincon:")
