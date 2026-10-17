"""Windows katmanı — Wine içindeki Windows Python'uyla uçtan uca.

    tests/wine/run.sh tests/test_windows_wine.py

Başsız Xvfb + ayrı WINEPREFIX'te koşar (bkz. tests/wine/run.sh); kullanıcının
gerçek ekranına ve ~/.wine'ına dokunmaz. Gerçek pencere (notepad) ve gerçek
konsol (cmd.exe) açar; WindowsDesktop'un pencere, giriş, ekran görüntüsü ve
konsol yollarını sınar. Wine'ın UI Automation'ı denetim ağacını gezemediği için
UIA yalnızca pencere düğümü düzeyinde denetlenir.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib

if sys.platform != "win32":
    print("⊘ bu test Windows Python'u ister: tests/wine/run.sh tests/test_windows_wine.py")
    sys.exit(0)

from nexremote.platforms import detect  # noqa: E402
from nexremote.platforms.base import Unsupported  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(("✓ " if ok else "✗ ") + name + (f" — {detail}" if detail else ""))
    return ok


def wait(pred, timeout: float = 10.0, step: float = 0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def png_pixels_nonuniform(path: str) -> bool:
    """PNG'yi stdlib ile çöz; tek renk değilse True (boş/siyah görüntüyü yakalar)."""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    w = h = 0
    while pos < len(data):
        n, = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    # ilk ve orta satırın filtre baytını atla; farklı bayt varsa tek renk değil
    stride = len(raw) // h
    rows = raw[1:stride] + raw[(h // 2) * stride + 1:(h // 2 + 1) * stride]
    return len(set(rows)) > 4


d = detect()
check("detect() → windows", d.name == "windows", d.name)
caps = {k: c.to_dict() for k, c in d.caps().items()}
check("caps: dört anahtar", set(caps) >= {"windows", "input", "screenshot", "terminals"},
      ", ".join(sorted(caps)))

# ------------------------------------------------------------- pencereler
procs: list[subprocess.Popen] = []
notepad = subprocess.Popen(["notepad.exe"])
procs.append(notepad)
win = wait(lambda: next((w for w in d.windows() if w.pid == notepad.pid), None), 20)
check("notepad penceresi listede", win is not None,
      win and f"{win.title!r} {win.app} {win.w}x{win.h}")

try:
    if win:
        check("pencere geometrisi anlamlı", win.w > 50 and win.h > 50, f"{win.w}x{win.h}")
        check("window_action activate", d.window_action(win.id, "activate"))
        act = wait(lambda: (d.active_window() or None), 5)
        check("etkin pencere notepad", act is not None and act.pid == notepad.pid,
              act and act.title)

        # --------------------------------------------------------- giriş
        cx, cy = win.x + win.w // 2, win.y + win.h // 2
        d.click(cx, cy)
        pos = d.pointer_pos()
        check("click → imleç hedefte", pos is not None and abs(pos[0] - cx) <= 1
              and abs(pos[1] - cy) <= 1, str(pos))
        text = "NexRemote ğüşıöç İ 42"
        d.type_text(text)
        time.sleep(0.5)
        # Notepad'in düzenleme denetimi: WM_GETTEXT ile içerik doğrula
        import ctypes
        u = ctypes.windll.user32
        u.FindWindowExW.restype = ctypes.c_void_p
        u.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p,
                                    ctypes.c_wchar_p]
        u.SendMessageW.restype = ctypes.c_ssize_t
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                   ctypes.c_void_p]
        from nexremote.platforms.windows import parse_hwnd
        hwnd = parse_hwnd(win.id)
        edit = u.FindWindowExW(hwnd, None, "Edit", None)
        buf = ctypes.create_unicode_buffer(512)
        u.SendMessageW(edit, 0x000D, 512, ctypes.cast(buf, ctypes.c_void_p))  # WM_GETTEXT
        check("type_text Unicode (Türkçe) yazdı", buf.value == text, repr(buf.value))

        d.key("ctrl+a")
        d.key("backspace")
        time.sleep(0.3)
        u.SendMessageW(edit, 0x000D, 512, ctypes.cast(buf, ctypes.c_void_p))
        check("key ctrl+a + backspace temizledi", buf.value == "", repr(buf.value))

        try:
            d.scroll(3)
            check("scroll hata vermedi", True)
        except Exception as e:
            check("scroll hata vermedi", False, str(e))

    # ------------------------------------------------------- ekran görüntüsü
    out = os.path.join(tempfile.gettempdir(), "nexr-shot.png")
    shot = d.screenshot(out)
    check("screenshot PNG", os.path.getsize(out) > 1000 and shot.width >= 640,
          f"{shot.width}x{shot.height} scale={shot.scale} {shot.backend}")
    check("screenshot tek renk değil", png_pixels_nonuniform(out))

    # ---------------------------------------------------------- UIA
    g = d.gui_backend()
    if g is None:
        check("UIA backend (comtypes)", False, "comtypes yok")
    else:
        cap = g.capability()
        ws = g.windows()
        check("UIA pencere düğümleri", any("otepad" in (w.title or "") or "otepad" in (w.app or "")
                                           for w in ws) if ws else False,
              f"{len(ws)} pencere; {cap.detail[:80]}")

    # ---------------------------------------------------------- konsol
    CREATE_NEW_CONSOLE = 0x10
    con = subprocess.Popen(["cmd.exe", "/Q", "/K", "title nexr-test"],
                           creationflags=CREATE_NEW_CONSOLE)
    procs.append(con)
    term = wait(lambda: next((t for t in d.terminals() if t.pid == con.pid), None), 20, 0.5)
    check("terminals(): yeni cmd konsolu", term is not None, term and f"{term.ref} {term.title!r}")
    if term:
        d.terminal_send(term.ref, "echo NEXR_%COMPUTERNAME:~0,0%OK_7x6", enter=True)
        seen = wait(lambda: "NEXR_OK_7x6" in d.terminal_read(term.ref), 10, 0.4)
        check("terminal_send + terminal_read (komut çıktısı okundu)", bool(seen))
        d.terminal_send(term.ref, "echo ğüş", enter=True)
        seen = wait(lambda: "ğüş" in d.terminal_read(term.ref), 10, 0.4)
        check("konsola Unicode gönderme", bool(seen))
        check("terminal_history_supported", d.terminal_history_supported(term.ref))
    try:
        d.terminal_read("wincon:999999")
        check("olmayan konsol → hata", False, "hata fırlatmadı")
    except (RuntimeError, Unsupported, ValueError) as e:
        check("olmayan konsol → hata", True, str(e)[:70])
    try:
        d.terminal_read("bozuk-ref")
        check("bozuk ref → ValueError", False)
    except ValueError:
        check("bozuk ref → ValueError", True)

    # ------------------------------------------------------ pencere kapatma
    if win:
        check("window_action close", d.window_action(win.id, "close"))
        gone = wait(lambda: notepad.poll() is not None, 10)
        check("notepad kapandı", gone is not None)
    check("olmayan pencere → False", d.window_action("0x7ffffff0", "activate") is False)
finally:
    for p in procs:
        if p.poll() is None:
            p.kill()

ok = sum(1 for _, o, _ in results if o)
print(f"WINDOWS: {ok}/{len(results)}")
sys.exit(0 if ok == len(results) else 1)
