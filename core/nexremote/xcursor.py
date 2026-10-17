#!/usr/bin/env python3
"""Ajan imleci — saf Xlib, override-redirect pencere.

Neden Xlib: KDE Wayland'de XWayland üzerinden konumlanan TEK güvenilir yol
override-redirect penceredir. WM ona dokunmaz, konum tam kontrolümüzdedir.
Pencere yalnızca İMLEÇ BOYUTUNDA (küçük) ve arkasından tıklanabilir —
kullanıcıyı hiç engellemez.

Soket protokolü: MOVE x y [sn — yoksa mesafeye göre] | CLICK | SHOW | HIDE | PING | QUIT
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
BOX = 56                       # pencere kenarı (px) — ok + tık halkası sığsın
HOT = 16                       # okun ucunun pencere içindeki yeri (x = y); hedef nokta
COLOR = (0x6e, 0xe7, 0xa8)     # dolgu #6ee7a8 — kullanıcının imleciyle karışmasın
EDGE = (0x10, 0x14, 0x12)      # kenar çizgisi
PULSE = (0x6e, 0xe7, 0xa8)

# Standart ok imleci (ucu 0,0'da; birim = piksel). Breeze/Adwaita oranları.
ARROW = [(0, 0), (0, 22), (5.2, 17), (9.2, 26.5), (13.6, 24.6), (9.6, 15.8), (16.5, 15.8)]


FPS = 120                      # hareket kare hızı


def move_duration(dist: float) -> float:
    """Fitts benzeri: kısa mesafe hızlı, uzun mesafe biraz daha uzun (sn)."""
    return min(0.7, 0.14 + 0.08 * math.log2(1 + dist / 24))


def min_jerk(t: float) -> float:
    """Minimum-jerk konum profili: 10t³ − 15t⁴ + 6t⁵ (uçlarda hız ve ivme 0)."""
    t = max(0.0, min(1.0, t))
    return t * t * t * (10 + t * (-15 + 6 * t))


def arc_control(sx: float, sy: float, x: float, y: float) -> tuple[float, float]:
    """Kavis için ikinci derece Bezier kontrol noktası: orta noktadan, yola dik,
    mesafenin %8'i kadar (en çok 48 px). Yön hep aynı tarafa: el bileği gibi."""
    dx, dy = x - sx, y - sy
    dist = math.hypot(dx, dy) or 1.0
    off = min(48.0, dist * 0.08)
    nx, ny = -dy / dist, dx / dist
    if ny > 0:                  # kavis yukarı doğru bükülsün
        nx, ny = -nx, -ny
    return (sx + x) / 2 + nx * off, (sy + y) / 2 + ny * off


def bezier(sx, sy, cx, cy, x, y, s: float) -> tuple[float, float]:
    u = 1 - s
    return (u * u * sx + 2 * u * s * cx + s * s * x,
            u * u * sy + 2 * u * s * cy + s * s * y)


def _inside(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    """Çift-tek kuralı ile nokta çokgen içinde mi."""
    hit = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def arrow_spans(poly=ARROW) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    """Oku satır aralıklarına çevir: (kenar, dolgu) — her öğe (x, y, genişlik).

    Kenar = çokgenin içinde olup 1 px komşusu dışarıda kalan pikseller."""
    w = int(max(x for x, _ in poly)) + 3
    h = int(max(y for _, y in poly)) + 3
    inside = [[_inside(x + 0.5, y + 0.5, poly) for x in range(w)] for y in range(h)]

    def at(x, y):
        return 0 <= y < h and 0 <= x < w and inside[y][x]

    edge = [[False] * w for _ in range(h)]
    fill = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if at(x, y):
                ring = all(at(x + dx, y + dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
                (fill if ring else edge)[y][x] = True

    def spans(grid):
        out = []
        for y, row in enumerate(grid):
            x = 0
            while x < w:
                if row[x]:
                    x0 = x
                    while x < w and row[x]:
                        x += 1
                    out.append((x0, y, x - x0))
                else:
                    x += 1
        return out
    return spans(edge), spans(fill)


def theme_cursor():
    """Kullanıcının imleç temasından ok (cursor_theme.load); olmazsa None."""
    try:
        try:
            from . import cursor_theme  # type: ignore[attr-defined]
        except ImportError:  # daemon dosya yoluyla çalışır (paket bağlamı yok)
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from nexremote import cursor_theme
        return cursor_theme.load()
    except Exception as e:
        print(f"[cursor] tema imleci yüklenemedi: {e}", file=sys.stderr)
        return None


def compose_frame(img, box: int, ring: int = 0) -> bytes:
    """box×box BGRA (ön-çarpılmış) kare: uçta halka + tema imleci (ucu HOT,HOT)."""
    import cairo
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, box, box)
    ctx = cairo.Context(surf)
    if ring:
        r, g, b = (c / 255 for c in PULSE)
        fade = max(0.0, 1.0 - (ring - 5) / 12)
        ctx.set_source_rgba(r, g, b, 0.85 * fade)
        ctx.set_line_width(2.0)
        ctx.arc(HOT + 0.5, HOT + 0.5, ring, 0, 2 * math.pi)
        ctx.stroke()
    src = cairo.ImageSurface.create_for_data(bytearray(img.argb), cairo.FORMAT_ARGB32,
                                             img.w, img.h, img.w * 4)
    ctx.set_source_surface(src, HOT - img.hot_x, HOT - img.hot_y)
    ctx.paint()
    surf.flush()
    stride = surf.get_stride()
    data = bytes(surf.get_data())
    if stride != box * 4:
        data = b"".join(data[r * stride:r * stride + box * 4] for r in range(box))
    return data


def alpha_spans(frame: bytes, box: int, thr: int) -> list[tuple[int, int, int, int]]:
    """Alfası thr ve üstü olan pikselleri satır aralıklarına çevir (Shape için)."""
    out = []
    for y in range(box):
        row = frame[y * box * 4 + 3:(y + 1) * box * 4:4]
        x = 0
        while x < box:
            if row[x] >= thr:
                x0 = x
                while x < box and row[x] >= thr:
                    x += 1
                out.append((x0, y, x - x0, 1))
            else:
                x += 1
    return out


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
        self._painted: list[tuple[int, int, int, int, int]] = []  # bu karede çizilenler
        self._spans = arrow_spans()
        # Kullanıcının imleç temasından ok (SVG → Xcursor); yoksa yerleşik ok.
        # Tema imleci 32 bit ARGB pencereyle yumuşak kenarlı çizilir.
        self.theme = theme_cursor() if os.environ.get("NEXREMOTE_CURSOR_THEME", "1") != "0" else None
        self.box = BOX
        self._argb_visual = None
        if self.theme is not None:
            self._argb_visual = self._find_argb_visual()
            if self._argb_visual is None:
                self.theme = None
            else:
                self.box = max(BOX, HOT - self.theme.hot_x + self.theme.w + 2,
                               HOT - self.theme.hot_y + self.theme.h + 2)
        self._make_window()

    def _find_argb_visual(self):
        """32 bit TrueColor görsel (alfa kanallı pencere için) ya da None."""
        X = self.X
        for depth in self.scr.allowed_depths:
            if depth.depth != 32:
                continue
            for v in depth.visuals:
                if v.visual_class == X.TrueColor:
                    return v.visual_id
        return None

    def _composited(self) -> bool:
        """Bileşikleyici var mı (_NET_WM_CM_S<n> seçiminin sahibi)? Yoksa X
        alfayı yok sayar; o zaman yarı saydam kenarlar şekil maskesiyle kesilir."""
        try:
            atom = self.d.intern_atom(f"_NET_WM_CM_S{self.d.get_default_screen()}")
            return self.d.get_selection_owner(atom) != self.X.NONE
        except Exception:
            return False

    def _make_window(self) -> None:
        X = self.X
        if self._argb_visual is not None:
            cmap = self.root.create_colormap(self._argb_visual, X.AllocNone)
            w = self.root.create_window(
                self.x - HOT, self.y - HOT, self.box, self.box, 0, 32,
                X.InputOutput, self._argb_visual,
                override_redirect=1, colormap=cmap,
                background_pixel=0, border_pixel=0,
                event_mask=X.ExposureMask,
            )
        else:
            w = self.root.create_window(
                self.x - HOT, self.y - HOT, BOX, BOX, 0, self.depth,
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
        px = self._alloc(color)
        # çemberi satır satır doldur (basit, hızlı)
        for dy in range(-r, r + 1):
            dx = int(math.sqrt(max(r * r - dy * dy, 0)))
            self._rect(px, cx - dx, cy + dy, 2 * dx, 1)

    def _ring(self, cx, cy, r, color, width):
        px = self._alloc(color)
        # çemberi noktalarla çiz
        n = max(int(2 * math.pi * r), 24)
        for i in range(n):
            a = 2 * math.pi * i / n
            self._rect(px, int(cx + r * math.cos(a)), int(cy + r * math.sin(a)), 2, 2)

    def _alloc(self, rgb):
        r, g, b = rgb
        try:
            return self.colormap.alloc_color(_to16(r), _to16(g), _to16(b)).pixel
        except Exception:
            return self.scr.white_pixel

    def _line(self, x0, y0, x1, y1, color, width=2):
        """İki nokta arası kalın çizgi (Bresenham benzeri, kalınlık için kare)."""
        px = self._alloc(color)
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            t = i / n
            x = int(x0 + (x1 - x0) * t)
            y = int(y0 + (y1 - y0) * t)
            self._rect(px, x - width // 2, y - width // 2, width, width)

    def _rect(self, px: int, x, y, w, h):
        """px = renk pikseli. Dikdörtgeni bu kareye ekle; çizim `_commit_shape`'te yapılır."""
        if w <= 0 or h <= 0:
            return
        self._painted.append((px, x, y, w, h))

    def _clear(self):
        self._painted = []

    def _commit_shape(self) -> None:
        """Önce görünen biçimi (Bounding) çizilecek piksellere kırp, sonra çiz.

        Pencerenin zemini siyahtır ve XWayland override-redirect pencerede
        saydamlık garanti değildir; Shape Bounding bölgesi ise geri kalan
        pikselleri hiçbir X sunucusunda göstermez. Sıra önemli: biçim değişince
        XWayland pencere içeriğini atar, bu yüzden çizim biçimden SONRA yapılır."""
        rects = [r[1:] for r in self._painted] or [(0, 0, 1, 1)]
        try:
            from Xlib.ext import shape
            shape.rectangles(self._win, shape.SO.Set, shape.SK.Bounding, 0, 0, 0, rects)
        except Exception as e:
            print(f"[cursor] shape: {e}", file=sys.stderr)
        gcs: dict[int, object] = {}
        for pixel, x, y, w, h in self._painted:
            gc = gcs.get(pixel)
            if gc is None:
                gc = gcs[pixel] = self._win.create_gc(foreground=pixel)
            self._win.fill_rectangle(gc, x, y, w, h)
        for gc in gcs.values():
            gc.free()
        self.d.sync()

    def _draw(self, ring: int = 0) -> None:
        """Ok imleci; ucu (HOT, HOT) noktasında. ring > 0 ise uçta tık halkası."""
        if self._win is None:
            return
        if self.theme is not None:
            self._draw_theme(ring)
            return
        self._clear()
        if ring:
            self._ring(HOT, HOT, ring, PULSE, 2)
        edge, fill = self._spans
        e, f = self._alloc(EDGE), self._alloc(COLOR)
        for x, y, w in edge:
            self._rect(e, HOT + x, HOT + y, w, 1)
        for x, y, w in fill:
            self._rect(f, HOT + x, HOT + y, w, 1)
        self._commit_shape()

    def _draw_theme(self, ring: int) -> None:
        """Tema imleci + (varsa) kenarı yumuşak tık halkası, tek ARGB görüntü."""
        X = self.X
        frame = compose_frame(self.theme, self.box, ring)
        thr = 1 if self._composited() else 128
        rects = alpha_spans(frame, self.box, thr) or [(0, 0, 1, 1)]
        try:
            from Xlib.ext import shape
            shape.rectangles(self._win, shape.SO.Set, shape.SK.Bounding, 0, 0, 0, rects)
        except Exception as e:
            print(f"[cursor] shape: {e}", file=sys.stderr)
        self._win.put_image(self._gc, 0, 0, self.box, self.box, X.ZPixmap, 32, 0, frame)
        self.d.sync()

    # ---------------------------------------------------------------- hareket
    def move_to(self, x, y, duration=None):
        """Hedefe insan eli gibi git: minimum-jerk hız profili (yumuşak kalkış ve
        duruş), hafif kavisli yol, süre mesafeye göre (Fitts). Zamana bağlıdır:
        kare çizimi gecikse de hareket hızlanıp yavaşlamaz, tam zamanında biter."""
        sx, sy = self.x, self.y
        x, y = int(round(x)), int(round(y))
        dist = math.hypot(x - sx, y - sy)
        if dist < 1:
            return
        if not duration or duration <= 0:
            duration = move_duration(dist)
        cx, cy = arc_control(sx, sy, x, y)
        t0 = time.monotonic()
        last = (sx, sy)
        frame = 1.0 / FPS
        n = 1
        while True:
            t = (time.monotonic() - t0) / duration
            if t >= 1.0:
                break
            px, py = bezier(sx, sy, cx, cy, x, y, min_jerk(t))
            pos = (int(round(px)), int(round(py)))
            if pos != last:
                self._place(*pos)
                last = pos
            # bir sonraki karenin mutlak zamanına uyu (gecikme birikmez)
            wait = t0 + n * frame - time.monotonic()
            n += 1
            if wait > 0:
                time.sleep(wait)
        self._place(x, y)
        self.d.sync()

    def _place(self, x: int, y: int) -> None:
        self.x, self.y = x, y
        try:
            self._win.configure(x=x - HOT, y=y - HOT)
        except Exception:
            return
        self.d.flush()          # sync değil: kare başına gidiş-dönüş beklemesi yok

    def pulse(self):
        """Tık geri bildirimi: uçtan genişleyen halka."""
        for r in (5, 7, 9, 11, 13, 15):
            self._draw(ring=r)
            time.sleep(0.035)
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
                                       float(p[3]) if len(p) > 3 else None)
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
