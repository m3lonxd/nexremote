"""Ajan imleci, Windows arka ucu — Wine içindeki Windows Python'uyla uçtan uca.

    tests/wine/run.sh tests/test_cursor_wine.py

Başsız Xvfb + ayrı WINEPREFIX (bkz. tests/wine/run.sh). Gerçek katmanlı
pencereyi açar: pencere stilleri, konumu, ekrandaki pikselleri, tıklamaların
alttaki pencereye geçmesi (click-through), odak çalmaması ve TCP daemon'ı
(belirteçli) facade üzerinden sınanır.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes as W

if sys.platform != "win32":
    print("⊘ bu test Windows Python'u ister: tests/wine/run.sh tests/test_cursor_wine.py")
    sys.exit(0)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from nexremote import agent_cursor as ac  # noqa: E402
from nexremote import wincursor as wc  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""), flush=True)


u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
u.GetWindowLongW.restype, u.GetWindowLongW.argtypes = W.LONG, [W.HWND, ctypes.c_int]
u.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u.IsWindowVisible.argtypes = [W.HWND]
u.WindowFromPoint.restype, u.WindowFromPoint.argtypes = W.HWND, [W.POINT]
u.GetForegroundWindow.restype = W.HWND
u.SetForegroundWindow.argtypes = [W.HWND]
u.FindWindowW.restype, u.FindWindowW.argtypes = W.HWND, [W.LPCWSTR, W.LPCWSTR]
u.GetDC.restype, u.GetDC.argtypes = W.HDC, [W.HWND]
u.ReleaseDC.argtypes = [W.HWND, W.HDC]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.mouse_event.argtypes = [W.DWORD, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
u.CreateWindowExW.restype = W.HWND
u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
u.DefWindowProcW.restype = wc.LRESULT
u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
u.RegisterClassExW.argtypes = [ctypes.POINTER(wc.WNDCLASSEXW)]
u.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
u.DestroyWindow.argtypes = [W.HWND]
u.UpdateWindow.argtypes = [W.HWND]
g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = W.HDC, [W.HDC]
g.CreateDIBSection.restype = W.HBITMAP
g.CreateDIBSection.argtypes = [W.HDC, ctypes.POINTER(wc.BITMAPINFO), W.UINT,
                               ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
g.SelectObject.restype, g.SelectObject.argtypes = W.HGDIOBJ, [W.HDC, W.HGDIOBJ]
g.BitBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC,
                     ctypes.c_int, ctypes.c_int, W.DWORD]
g.DeleteObject.argtypes = [W.HGDIOBJ]
g.DeleteDC.argtypes = [W.HDC]
k.GetModuleHandleW.restype, k.GetModuleHandleW.argtypes = W.HMODULE, [W.LPCWSTR]

SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
WM_LBUTTONDOWN = 0x0201


def grab(x: int, y: int, w: int, h: int) -> bytes:
    """Ekrandan BGRA (katmanlı pencereler dahil: CAPTUREBLT)."""
    scr = u.GetDC(None)
    mem = g.CreateCompatibleDC(scr)
    bits = ctypes.c_void_p()
    dib = g.CreateDIBSection(mem, ctypes.byref(wc._bmi(w, h)), 0, ctypes.byref(bits), None, 0)
    old = g.SelectObject(mem, dib)
    g.BitBlt(mem, 0, 0, w, h, scr, x, y, SRCCOPY | CAPTUREBLT)
    data = ctypes.string_at(bits, w * h * 4)
    g.SelectObject(mem, old)
    g.DeleteObject(dib)
    g.DeleteDC(mem)
    u.ReleaseDC(None, scr)
    return data


def rect(hwnd) -> tuple[int, int, int, int]:
    r = W.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def pump_for(cur, sec: float) -> None:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        cur.pump()
        time.sleep(0.01)


# ---------------------------------------------------- tıklama tuzağı pencere
clicks: list[tuple[int, int]] = []


def _trap_proc(h, m, w, l):
    if m == WM_LBUTTONDOWN:
        clicks.append((l & 0xFFFF, (l >> 16) & 0xFFFF))
    return u.DefWindowProcW(h, m, w, l)


TRAP_PROC = wc.WNDPROC(_trap_proc)


def make_trap():
    wcx = wc.WNDCLASSEXW()
    wcx.cbSize = ctypes.sizeof(wc.WNDCLASSEXW)
    wcx.lpfnWndProc = TRAP_PROC
    wcx.hInstance = k.GetModuleHandleW(None)
    wcx.lpszClassName = "NexRemoteClickTrap"
    wcx.hbrBackground = 6                                     # COLOR_WINDOW+1
    u.RegisterClassExW(ctypes.byref(wcx))
    hwnd = u.CreateWindowExW(0, "NexRemoteClickTrap", "trap", 0x80000000 | 0x10000000,  # POPUP|VISIBLE
                             200, 150, 600, 400, None, None, wcx.hInstance, None)
    u.ShowWindow(hwnd, 5)
    u.UpdateWindow(hwnd)
    u.SetForegroundWindow(hwnd)
    return hwnd


def main() -> int:
    img = wc.system_arrow()
    check("sistem oku okundu (IDC_ARROW)", img is not None and img.w > 0 and img.h > 0,
          f"{img.w}x{img.h} uç=({img.hot_x},{img.hot_y})" if img else "yok — yerleşik ok")
    if img:
        check("sistem okunun ucu opak değil-boş değil", any(img.argb[3::4]))

    ok, why = ac.available()
    check("available(): Windows'ta hazır", ok, why)
    check("taşıma TCP (Windows)", ac.use_tcp())

    trap = make_trap()
    time.sleep(0.3)
    before = grab(400, 300, 8, 8)

    cur = wc.WinAgentCursor()
    ex = u.GetWindowLongW(cur.hwnd, -20) & 0xFFFFFFFF          # GWL_EXSTYLE
    need = {"LAYERED": wc.WS_EX_LAYERED, "TRANSPARENT": wc.WS_EX_TRANSPARENT,
            "TOPMOST": wc.WS_EX_TOPMOST, "TOOLWINDOW": wc.WS_EX_TOOLWINDOW,
            "NOACTIVATE": wc.WS_EX_NOACTIVATE}
    missing = [n for n, f in need.items() if not ex & f]
    check("pencere stilleri: katmanlı, click-through, en üstte, odaksız", not missing,
          f"eksik: {missing}" if missing else hex(ex))

    t0 = time.monotonic()
    cur.move_to(400, 300)
    took = time.monotonic() - t0
    want = ac.move_duration(((400 - 300) ** 2 + (300 - 300) ** 2) ** 0.5)
    check("hareket zamanında bitti", abs(took - want) < 0.12, f"{took:.3f}s / {want:.3f}s")
    l, t, r, b = rect(cur.hwnd)
    check("ucu hedefte (pencere = hedef − HOT)", (l, t) == (400 - ac.HOT, 300 - ac.HOT)
          and r - l == cur.box, f"{(l, t, r, b)}")
    check("görünür", bool(u.IsWindowVisible(cur.hwnd)))

    pump_for(cur, 0.3)
    after = grab(400, 300, 8, 8)
    changed = sum(1 for i in range(0, len(after), 4) if after[i:i + 3] != before[i:i + 3])
    check("imleç pikselleri ekranda (uç hizalı)", changed >= 6, f"{changed}/64 piksel değişti")
    corner = grab(400 + cur.box - ac.HOT - 3, 300 + cur.box - ac.HOT - 3, 1, 1)
    trap_bg = grab(760, 520, 1, 1)
    check("imleç dışı saydam (pencere köşesinde alttaki pencere görünür)",
          corner[:3] == trap_bg[:3], f"köşe={tuple(corner[:3])} alttaki={tuple(trap_bg[:3])}")

    hit = u.WindowFromPoint(W.POINT(402, 304))
    check("isabet testi imleci atlıyor (WindowFromPoint alttakini bulur)",
          bool(hit) and hit != cur.hwnd, f"hwnd={hit} imleç={cur.hwnd} tuzak={trap}")

    fg = u.GetForegroundWindow()
    clicks.clear()
    u.SetCursorPos(402, 304)
    u.mouse_event(0x0002, 0, 0, 0, 0)                         # sol bas
    u.mouse_event(0x0004, 0, 0, 0, 0)                         # sol bırak
    pump_for(cur, 0.5)
    check("tık imlecin içinden alttaki pencereye geçti", bool(clicks),
          f"tuzak {clicks[:1]} (beklenen ≈ (202, 154))")
    check("odak çalınmadı", u.GetForegroundWindow() == fg and fg != cur.hwnd, f"ön={u.GetForegroundWindow()} önce={fg}")

    shot = os.environ.get("NEXR_CURSOR_SHOT")
    if shot:                                                  # gözle inceleme için 120x120 PPM
        px = grab(360, 260, 120, 120)
        with open(shot, "wb") as f:
            f.write(b"P6 120 120 255\n" + b"".join(px[i + 2::-1][:3] if i else px[2::-1]
                                                  for i in range(0, len(px), 4)))

    t0 = time.monotonic()
    cur.pulse()
    check("tık halkası çizildi, imleç yerinde", rect(cur.hwnd)[:2] == (400 - ac.HOT, 300 - ac.HOT),
          f"{time.monotonic() - t0:.2f}s")

    cur.set_visible(False)
    check("HIDE gizler", not u.IsWindowVisible(cur.hwnd))
    cur.set_visible(True)
    check("SHOW gösterir", bool(u.IsWindowVisible(cur.hwnd)))
    cur.close()
    check("close pencereyi yok eder", cur.hwnd is None)

    # ----------------------------------------------- daemon (TCP + belirteç)
    from nexremote.nexremote import NexRemote
    nr = NexRemote()
    check("facade.pointer_available Windows'ta", nr.pointer_available()[0], nr.pointer_available()[1])
    try:
        ok = nr.cursor(700, 500, click=True)
    except Exception as e:
        ok = False
        print(f"    {type(e).__name__}: {e}")
    check("facade.cursor daemon'ı başlattı ve taşıdı", ok and ac.daemon_running())
    hw = u.FindWindowW(wc.CLASS, None)
    check("daemon penceresi hedefte", bool(hw) and rect(hw)[:2] == (700 - ac.HOT, 500 - ac.HOT),
          str(rect(hw) if hw else None))
    check("cursor_move süreyle", nr.cursor_move(300, 200, duration=0.2)
          and rect(hw)[:2] == (300 - ac.HOT, 200 - ac.HOT))

    import json
    import socket
    with open(ac.endpoint_file()) as f:
        ep = json.load(f)
    s = socket.create_connection(("127.0.0.1", ep["port"]), timeout=2)
    s.sendall(b"yanlis-belirtec\nMOVE 10 10\n")
    s.settimeout(2)
    try:
        got = s.recv(16)
    except OSError:
        got = b""
    s.close()
    check("yanlış belirteç reddedildi", got == b"" and rect(hw)[:2] == (300 - ac.HOT, 200 - ac.HOT),
          repr(got))

    check("cursor_hide", nr.cursor_hide() and not u.IsWindowVisible(hw))
    check("QUIT daemon'ı kapatır", ac.send("QUIT") == "BYE")
    time.sleep(0.5)
    check("daemon kapandı, uç dosyası silindi", not ac.daemon_running()
          and not os.path.exists(ac.endpoint_file()))
    u.DestroyWindow(trap)

    bad = [n for n, ok, _ in results if not ok]
    print(f"WINDOWS İMLEÇ: {len(results) - len(bad)}/{len(results)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
