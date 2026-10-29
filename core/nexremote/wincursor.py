"""Ajan imleci, Windows arka ucu — ctypes ile Win32 katmanlı pencere.

    WS_EX_LAYERED      kare UpdateLayeredWindow ile piksel başına alfa ile çizilir
    WS_EX_TRANSPARENT  isabet testi pencereyi atlar: tıklamalar arkadakine gider
    WS_EX_TOPMOST      her pencerenin üstünde
    WS_EX_TOOLWINDOW   görev çubuğunda ve Alt+Tab'de görünmez
    WS_EX_NOACTIVATE   odak çalmaz; kullanıcının yazdığı pencere aktif kalır

İmleç görüntüsü kullanıcının SİSTEM okudur (LoadCursor IDC_ARROW → GetIconInfo
→ GetDIBits); alınamazsa yerleşik oka düşer. Koordinatlar fiziksel piksel:
süreç WindowsDesktop gibi Per-Monitor v2 DPI-farkında yapılır, böylece
`nexremote click X Y` ile imlecin ucu aynı noktaya düşer.

Yalnızca stdlib (ctypes). Tüm pencere çağrıları daemon'ın ana iş
parçacığından yapılır; pump() mesaj kuyruğunu boşaltır.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes as W

from .agent_cursor import HOT, AgentCursorBase, builtin_arrow, compose_frame, frame_box

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x00080000, 0x00000020, 0x00000008
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
EX_STYLE = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0010, 0x0040
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 2, 0, 1
PM_REMOVE = 1
DIB_RGB_COLORS, BI_RGB = 0, 0
IDC_ARROW = 32512
CLASS = "NexRemoteAgentCursor"

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON), ("hCursor", W.HANDLE),
                ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
                ("lpszClassName", W.LPCWSTR), ("hIconSm", W.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG),
                ("biPlanes", W.WORD), ("biBitCount", W.WORD), ("biCompression", W.DWORD),
                ("biSizeImage", W.DWORD), ("biXPelsPerMeter", W.LONG),
                ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", W.DWORD * 3)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", W.LONG), ("bmWidth", W.LONG), ("bmHeight", W.LONG),
                ("bmWidthBytes", W.LONG), ("bmPlanes", W.WORD), ("bmBitsPixel", W.WORD),
                ("bmBits", ctypes.c_void_p)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", W.BOOL), ("xHotspot", W.DWORD), ("yHotspot", W.DWORD),
                ("hbmMask", W.HBITMAP), ("hbmColor", W.HBITMAP)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", W.BYTE), ("BlendFlags", W.BYTE),
                ("SourceConstantAlpha", W.BYTE), ("AlphaFormat", W.BYTE)]


def _api():
    u = ctypes.WinDLL("user32", use_last_error=True)
    g = ctypes.WinDLL("gdi32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    sig = {
        (u, "DefWindowProcW"): (LRESULT, [W.HWND, W.UINT, W.WPARAM, W.LPARAM]),
        (u, "RegisterClassExW"): (W.ATOM, [ctypes.POINTER(WNDCLASSEXW)]),
        (u, "CreateWindowExW"): (W.HWND, [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND,
                                          W.HMENU, W.HINSTANCE, W.LPVOID]),
        (u, "DestroyWindow"): (W.BOOL, [W.HWND]),
        (u, "ShowWindow"): (W.BOOL, [W.HWND, ctypes.c_int]),
        (u, "SetWindowPos"): (W.BOOL, [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, W.UINT]),
        (u, "UpdateLayeredWindow"): (W.BOOL, [W.HWND, W.HDC, ctypes.POINTER(W.POINT),
                                              ctypes.POINTER(W.SIZE), W.HDC,
                                              ctypes.POINTER(W.POINT), W.COLORREF,
                                              ctypes.POINTER(BLENDFUNCTION), W.DWORD]),
        (u, "PeekMessageW"): (W.BOOL, [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]),
        (u, "TranslateMessage"): (W.BOOL, [ctypes.POINTER(W.MSG)]),
        (u, "DispatchMessageW"): (LRESULT, [ctypes.POINTER(W.MSG)]),
        (u, "GetDC"): (W.HDC, [W.HWND]),
        (u, "ReleaseDC"): (ctypes.c_int, [W.HWND, W.HDC]),
        (u, "LoadCursorW"): (W.HANDLE, [W.HINSTANCE, W.LPVOID]),
        (u, "GetIconInfo"): (W.BOOL, [W.HANDLE, ctypes.POINTER(ICONINFO)]),
        (g, "CreateCompatibleDC"): (W.HDC, [W.HDC]),
        (g, "DeleteDC"): (W.BOOL, [W.HDC]),
        (g, "CreateDIBSection"): (W.HBITMAP, [W.HDC, ctypes.POINTER(BITMAPINFO), W.UINT,
                                              ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]),
        (g, "SelectObject"): (W.HGDIOBJ, [W.HDC, W.HGDIOBJ]),
        (g, "DeleteObject"): (W.BOOL, [W.HGDIOBJ]),
        (g, "GetObjectW"): (ctypes.c_int, [W.HANDLE, ctypes.c_int, W.LPVOID]),
        (g, "GetDIBits"): (ctypes.c_int, [W.HDC, W.HBITMAP, W.UINT, W.UINT, W.LPVOID,
                                          ctypes.POINTER(BITMAPINFO), W.UINT]),
        (k, "GetModuleHandleW"): (W.HMODULE, [W.LPCWSTR]),
    }
    for (lib, name), (res, args) in sig.items():
        f = getattr(lib, name)
        f.restype, f.argtypes = res, args
    return u, g, k


def _bmi(w: int, h: int) -> BITMAPINFO:
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth, bi.bmiHeader.biHeight = w, -h      # eksi: üstten aşağı satırlar
    bi.bmiHeader.biPlanes, bi.bmiHeader.biBitCount = 1, 32
    bi.bmiHeader.biCompression = BI_RGB
    return bi


def premultiply(bgra: bytes) -> bytes:
    out = bytearray(bgra)
    for i in range(0, len(out), 4):
        a = out[i + 3]
        if a != 255:
            out[i] = (out[i] * a + 127) // 255
            out[i + 1] = (out[i + 1] * a + 127) // 255
            out[i + 2] = (out[i + 2] * a + 127) // 255
    return bytes(out)


def mask_to_bgra(and_bits: bytes, xor_bits: bytes, color: bytes | None) -> bytes:
    """Alfasız imleç → BGRA (düz alfa). AND maskesi 1 = saydam.

    Renkli imleçte renk XOR bitmap'inden gelir; tek renkli imleçte (color None)
    AND=0,XOR=0 siyah, AND=0,XOR=1 beyaz, AND=1,XOR=1 (ekranı ters çevir) siyah
    çizilir — katmanlı pencere ters çevirme yapamaz, en okunur yaklaşım budur."""
    out = bytearray(len(and_bits))
    for i in range(0, len(and_bits), 4):
        transparent = and_bits[i] != 0
        inv = xor_bits[i] != 0
        if color is not None:
            if transparent and not inv:
                continue
            out[i:i + 3] = color[i:i + 3]
            out[i + 3] = 255
        elif transparent and not inv:
            continue
        elif not transparent and inv:
            out[i:i + 4] = b"\xff\xff\xff\xff"
        else:
            out[i:i + 4] = b"\x00\x00\x00\xff"
    return bytes(out)


def system_arrow():
    """Kullanıcının sistem ok imleci (CursorImage) ya da None."""
    from .cursor_theme import CursorImage
    u, g, _ = _api()
    hcur = u.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))        # paylaşılan; yok edilmez
    info = ICONINFO()
    if not hcur or not u.GetIconInfo(hcur, ctypes.byref(info)):
        return None
    hdc = u.GetDC(None)
    try:
        def bits(hbm, w, h) -> bytes:
            buf = ctypes.create_string_buffer(w * h * 4)
            if g.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(_bmi(w, h)), DIB_RGB_COLORS) != h:
                raise OSError("GetDIBits başarısız")
            return buf.raw

        bm = BITMAP()
        if not g.GetObjectW(info.hbmMask, ctypes.sizeof(BITMAP), ctypes.byref(bm)):
            return None
        w = bm.bmWidth
        if info.hbmColor:
            h = bm.bmHeight
            color = bits(info.hbmColor, w, h)
            if any(color[3::4]):
                argb = color                                     # 32 bit alfalı imleç
            else:
                argb = mask_to_bgra(bits(info.hbmMask, w, h), b"\0" * (w * h * 4), color)
        else:
            h = bm.bmHeight // 2                                 # üst yarı AND, alt yarı XOR
            both = bits(info.hbmMask, w, h * 2)
            argb = mask_to_bgra(both[:w * h * 4], both[w * h * 4:], None)
        return CursorImage(w, h, int(info.xHotspot), int(info.yHotspot),
                           premultiply(argb), "Windows sistem oku (IDC_ARROW)")
    except OSError as e:
        print(f"[cursor] sistem oku alınamadı: {e}", file=sys.stderr)
        return None
    finally:
        u.ReleaseDC(None, hdc)
        for hbm in (info.hbmMask, info.hbmColor):
            if hbm:
                g.DeleteObject(hbm)


class WinAgentCursor(AgentCursorBase):
    name = "Win32"

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("WinAgentCursor yalnızca Windows'ta çalışır")
        try:
            from .platforms.windows import make_dpi_aware
            make_dpi_aware()
        except Exception as e:
            print(f"[cursor] DPI: {e}", file=sys.stderr)
        self.u, self.g, k = _api()
        img = None
        if os.environ.get("NEXREMOTE_CURSOR_THEME", "1") != "0":
            img = system_arrow()
        self.img = img or builtin_arrow()
        self.box = frame_box(self.img)
        self._visible = True
        hinst = k.GetModuleHandleW(None)
        # sınıf yordamı nesneye bağlı tutulur (çöp toplanırsa Windows ölü adresi çağırır)
        self._proc = WNDPROC(lambda h, m, w, l: self.u.DefWindowProcW(h, m, w, l))
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = CLASS
        self.u.RegisterClassExW(ctypes.byref(wc))               # zaten kayıtlıysa sorun değil
        self.hwnd = self.u.CreateWindowExW(EX_STYLE, CLASS, "NexRemote agent cursor", WS_POPUP,
                                           self.x - HOT, self.y - HOT, self.box, self.box,
                                           None, None, hinst, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW başarısız (hata {ctypes.get_last_error()})")
        self._screen = self.u.GetDC(None)
        self._mem = self.g.CreateCompatibleDC(self._screen)
        bitsp = ctypes.c_void_p()
        self._dib = self.g.CreateDIBSection(self._mem, ctypes.byref(_bmi(self.box, self.box)),
                                            DIB_RGB_COLORS, ctypes.byref(bitsp), None, 0)
        if not self._dib:
            raise OSError("CreateDIBSection başarısız")
        self._bits = bitsp.value
        self._old = self.g.SelectObject(self._mem, self._dib)
        self._frames: dict[int, bytes] = {}
        self._draw()
        self.u.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.pump()

    def _frame(self, ring: int) -> bytes:
        f = self._frames.get(ring)
        if f is None:
            f = self._frames[ring] = compose_frame(self.img, self.box, ring)
        return f

    def _draw(self, ring: int = 0) -> None:
        ctypes.memmove(self._bits, self._frame(ring), self.box * self.box * 4)
        dst = W.POINT(self.x - HOT, self.y - HOT)
        size = W.SIZE(self.box, self.box)
        src = W.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        if not self.u.UpdateLayeredWindow(self.hwnd, self._screen, ctypes.byref(dst),
                                          ctypes.byref(size), self._mem, ctypes.byref(src),
                                          0, ctypes.byref(blend), ULW_ALPHA):
            print(f"[cursor] UpdateLayeredWindow hata {ctypes.get_last_error()}", file=sys.stderr)

    def _place(self, x: int, y: int) -> None:
        self.x, self.y = x, y
        flags = SWP_NOSIZE | SWP_NOACTIVATE | (SWP_SHOWWINDOW if self._visible else 0)
        self.u.SetWindowPos(self.hwnd, W.HWND(HWND_TOPMOST), x - HOT, y - HOT, 0, 0, flags)

    def pump(self) -> None:
        msg = W.MSG()
        while self.u.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            self.u.TranslateMessage(ctypes.byref(msg))
            self.u.DispatchMessageW(ctypes.byref(msg))

    def set_visible(self, v: bool) -> None:
        self._visible = bool(v)
        self.u.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE if v else SW_HIDE)
        self.pump()

    def close(self) -> None:
        if not getattr(self, "hwnd", None):
            return
        self.u.DestroyWindow(self.hwnd)
        self.hwnd = None
        self.g.SelectObject(self._mem, self._old)
        self.g.DeleteObject(self._dib)
        self.g.DeleteDC(self._mem)
        self.u.ReleaseDC(None, self._screen)
