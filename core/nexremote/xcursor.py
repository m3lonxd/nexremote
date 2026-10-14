#!/usr/bin/env python3
"""Ajan imleci — saf Xlib, override-redirect pencere.

Neden Xlib: KDE Wayland'de XWayland üzerinden konumlanan TEK güvenilir yol
override-redirect penceredir. WM ona dokunmaz, konum tam kontrolümüzdedir.
Pencere yalnızca İMLEÇ BOYUTUNDA (küçük) ve arkasından tıklanabilir —
kullanıcıyı hiç engellemez.

Soket protokolü: MOVE x y [dur] | CLICK | SHOW | HIDE | PING | QUIT
"""
from __future__ import annotations

import math
import os
import socket
import sys
import threading
import time

SOCK = os.environ.get("NEXREMOTE_CURSOR_SOCK",
                      f"/tmp/nexremote-cursor-{os.getuid()}.sock")
BOX = 72                       # pencere kenarı (px) — işaretçi + nabzı sığsın
COLOR = (0x6e, 0xe7, 0xa8)     # #6ee7a8 (16-bit'e çevrilecek)
PULSE = (0xff, 0xcc, 0x6b)


def _to16(v: int) -> int:
    return v * 257  # 8-bit -> 16-bit


class XAgentCursor:
    def __init__(self, display: str = ":1", screen: int = 0) -> None:
        from Xlib import display as xd, X
        self.X = X
        self.d = xd.Display(display)
        self.scr = self.d.screen(screen)
        self.root = self.scr.root
        self.depth = self.scr.root_depth
        self.vis = self.scr.root_visual
        self.colormap = self.scr.default_colormap
        self.W = self.scr.width_in_pixels
        self.H = self.scr.height_in_pixels
        self.x, self.y = 300, 300
        self._visible = True
        self._win = None
        self._gc = None
        self._make_window()

    def _make_window(self) -> None:
        X = self.X
        w = self.root.create_window(
            self.x, self.y, BOX, BOX, 0, self.depth,
            X.InputOutput, X.CopyFromParent,
            override_redirect=1,
            background_pixel=self.scr.black_pixel,
            event_mask=X.ExposureMask,
        )
        self._win = w
        self._gc = w.create_gc()
        self._apply_shape()      # click-through
        w.map()
        self.d.sync()
        self._draw()

    def _apply_shape(self) -> None:
        """Input bölgesini tamamen boşalt → tüm tıklamalar arkaya geçer.

        Doğru API: shape.rectangles(win, SO.Set, SK.Input, 0,0,0, []) — boş
        dikdörtgen listesi. (X.Window üzerinde metot YOK; modül fonksiyonu.)
        """
        try:
            from Xlib.ext import shape
            shape.rectangles(self._win, shape.SO.Set, shape.SK.Input,
                             0, 0, 0, [])
            self.d.sync()
        except Exception as e:
            print(f"[cursor] shape: {e}", file=sys.stderr)

    # ---------------------------------------------------------------- çizim
    def _fill_circle(self, cx, cy, r, color):
        X = self.X
        gc = self._win.create_gc(
            foreground=self._alloc(color), line_width=1)
        # çemberi satır satır doldur (basit, hızlı)
        for dy in range(-r, r + 1):
            dx = int(math.sqrt(max(r * r - dy * dy, 0)))
            self._win.fill_rectangle(gc, cx - dx, cy + dy, 2 * dx, 1)
        gc.free()

    def _ring(self, cx, cy, r, color, width):
        X = self.X
        gc = self._win.create_gc(
            foreground=self._alloc(color), line_width=width)
        # çemberi noktalarla çiz
        n = max(int(2 * math.pi * r), 24)
        for i in range(n):
            a = 2 * math.pi * i / n
            self._win.fill_rectangle(gc, int(cx + r * math.cos(a)),
                                     int(cy + r * math.sin(a)), 2, 2)
        gc.free()

    def _alloc(self, rgb):
        r, g, b = rgb
        try:
            return self.colormap.alloc_color(_to16(r), _to16(g), _to16(b)).pixel
        except Exception:
            return self.scr.white_pixel

    def _line(self, x0, y0, x1, y1, color, width=2):
        """İki nokta arası kalın çizgi (Bresenham benzeri, kalınlık için kare)."""
        gc = self._win.create_gc(foreground=self._alloc(color))
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            t = i / n
            x = int(x0 + (x1 - x0) * t)
            y = int(y0 + (y1 - y0) * t)
            self._win.fill_rectangle(gc, x - width // 2, y - width // 2, width, width)
        gc.free()

    def _clear(self):
        self._win.fill_rectangle(self._gc, 0, 0, BOX, BOX)
        self.d.sync()

    def _draw(self) -> None:
        """Crosshair (hedef) stili: dört yöne çizgi + merkez çember + nokta."""
        if self._win is None:
            return
        cx = cy = BOX // 2
        self._clear()
        gap = 7          # merkez çember ile çizgiler arası boşluk
        arm = 18         # çizgi uzunluğu
        # dört yön çizgisi (üst, alt, sol, sağ)
        self._line(cx, cy - gap, cx, cy - gap - arm, COLOR, 2)   # üst
        self._line(cx, cy + gap, cx, cy + gap + arm, COLOR, 2)   # alt
        self._line(cx - gap, cy, cx - gap - arm, cy, COLOR, 2)   # sol
        self._line(cx + gap, cy, cx + gap + arm, cy, COLOR, 2)   # sağ
        # merkez çember
        self._ring(cx, cy, 6, COLOR, 2)
        # merkez nokta
        self._fill_circle(cx, cy, 2, COLOR)
        self.d.sync()

    # ---------------------------------------------------------------- hareket
    def move_to(self, x, y, duration=0.4, steps=None):
        sx, sy = self.x, self.y
        steps = steps or max(int(duration * 60), 8)
        for i in range(1, steps + 1):
            f = i / steps
            f = f * f * (3 - 2 * f)
            nx, ny = int(sx + (x - sx) * f), int(sy + (y - sy) * f)
            self.x, self.y = nx, ny
            try:
                self._win.configure(x=nx, y=ny)
            except Exception:
                pass
            self.d.sync()
            time.sleep(max(duration / steps, 0.008))

    def pulse(self):
        cx = cy = BOX // 2
        for i in range(8):
            self._clear()
            r = 14 + i * 3
            self._ring(cx, cy, r, PULSE, 2)
            self._ring(cx, cy, 12, COLOR, 2)
            self._fill_circle(cx, cy, 4, COLOR)
            self.d.sync()
            time.sleep(0.04)
        self._draw()

    def set_visible(self, v):
        self._visible = v
        try:
            (self._win.map if v else self._win.unmap)()
            self.d.sync()
        except Exception:
            pass

    def close(self):
        try:
            self._win.destroy()
            self.d.sync()
        except Exception:
            pass


# -------------------------------------------------------------------- daemon
def serve(cursor: XAgentCursor) -> None:
    if os.path.exists(SOCK):
        try:
            os.unlink(SOCK)
        except OSError:
            pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o600)
    srv.listen(8)
    print(f"[cursor] hazır: {SOCK}", flush=True)

    lock = threading.Lock()

    def handle(conn):
        try:
            for line in conn.makefile("r"):
                p = line.strip().split()
                if not p:
                    continue
                c = p[0].upper()
                with lock:
                    if c == "MOVE":
                        cursor.move_to(float(p[1]), float(p[2]),
                                       float(p[3]) if len(p) > 3 else 0.4)
                    elif c == "CLICK":
                        cursor.pulse()
                    elif c == "SHOW":
                        cursor.set_visible(True)
                    elif c == "HIDE":
                        cursor.set_visible(False)
                    elif c == "PING":
                        conn.sendall(b"PONG\n"); continue
                    elif c == "QUIT":
                        conn.sendall(b"BYE\n")
                        os._exit(0)
                    else:
                        conn.sendall(b"ERR\n"); continue
                conn.sendall(b"OK\n")
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


def daemon_running() -> bool:
    if not os.path.exists(SOCK):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.5); s.connect(SOCK); s.sendall(b"PING\n")
        ok = s.recv(16).strip() == b"PONG"; s.close(); return ok
    except Exception:
        return False


def send(cmd: str, timeout: float = 5.0) -> str:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout); s.connect(SOCK)
    s.sendall((cmd + "\n").encode())
    r = s.recv(64).decode().strip(); s.close(); return r


def start_daemon(display: str = ":1") -> bool:
    if daemon_running():
        return True
    import subprocess
    env = dict(os.environ); env["DISPLAY"] = display
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--display", display],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=env)
    for _ in range(40):
        if daemon_running():
            return True
        time.sleep(0.15)
    return False


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    display = ":1"
    for i, a in enumerate(argv):
        if a == "--display" and i + 1 < len(argv):
            display = argv[i + 1]
    cur = XAgentCursor(display=display)
    serve(cur)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
