#!/usr/bin/env python3
"""Ajan imleci — platformdan bağımsız çekirdek.

Ajanın KENDİ görünür imleci: küçük, her şeyin üstünde, tıklamaları arkaya
geçiren (click-through) bir pencere. Kullanıcının faresine dokunmaz.

    arka uç      Linux  → xcursor.XAgentCursor   (X11/XWayland override-redirect)
                 Windows→ wincursor.WinAgentCursor (katmanlı WS_EX_TRANSPARENT pencere)
                 macOS  → maccursor.MacAgentCursor (NSWindow, ignoresMouseEvents)

Hepsi aynı hareketi (minimum-jerk + kavis, 120 FPS) ve aynı daemon'ı kullanır;
yalnızca pencereyi yerleştirme ve çizme kısmı platforma özeldir.

Daemon protokolü (satır başına bir komut, yanıt bir satır):
    MOVE x y [sn — yoksa mesafeye göre] | CLICK | SHOW | HIDE | PING | QUIT

Taşıma: POSIX'te kullanıcıya özel Unix soketi (0600). Windows'ta 127.0.0.1
üzerinde rastgele bir TCP portu; port ve 32 baytlık belirteç kullanıcının
geçici dizinindeki bir dosyada durur ve her bağlantının ilk satırı belirteç
olmalıdır (aynı makinedeki başka kullanıcılar imleci süremez).

Pencere işlemleri hep daemon'ın ANA iş parçacığında yapılır (Win32 ve AppKit
pencerenin sahibi olan iş parçacığını ister); bağlantılar iş kuyruğuna yazar.
"""
from __future__ import annotations

import hmac
import json
import math
import os
import queue
import secrets
import socket
import sys
import threading
import time

BOX = 56                       # pencere kenarı (px) — ok + tık halkası sığsın
HOT = 16                       # okun ucunun pencere içindeki yeri (x = y); hedef nokta
COLOR = (0x6e, 0xe7, 0xa8)     # yerleşik ok dolgusu #6ee7a8
EDGE = (0x10, 0x14, 0x12)      # yerleşik ok kenarı
PULSE = (0x6e, 0xe7, 0xa8)     # tık halkası
RINGS = (5, 7, 9, 11, 13, 15)  # tık halkasının kareleri (yarıçap, px)

# Standart ok imleci (ucu 0,0'da; birim = piksel). Breeze/Adwaita oranları.
ARROW = [(0, 0), (0, 22), (5.2, 17), (9.2, 26.5), (13.6, 24.6), (9.6, 15.8), (16.5, 15.8)]

FPS = 120                      # hareket kare hızı


# ------------------------------------------------------------------ hareket
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


# ------------------------------------------------------------------- çizim
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


def builtin_arrow():
    """Yerleşik ok (tema/sistem imleci alınamazsa): CursorImage, ucu (0,0)."""
    from .cursor_theme import CursorImage
    edge, fill = arrow_spans()
    w = int(max(x for x, _ in ARROW)) + 3
    h = int(max(y for _, y in ARROW)) + 3
    buf = bytearray(w * h * 4)
    for spans, (r, g, b) in ((edge, EDGE), (fill, COLOR)):
        for x0, y, n in spans:
            for x in range(x0, x0 + n):
                i = (y * w + x) * 4
                buf[i:i + 4] = bytes((b, g, r, 255))
    return CursorImage(w, h, 0, 0, bytes(buf), "yerleşik ok")


def theme_cursor():
    """Kullanıcının imleç temasından ok (cursor_theme.load); olmazsa None."""
    try:
        from . import cursor_theme
        return cursor_theme.load()
    except Exception as e:
        print(f"[cursor] tema imleci yüklenemedi: {e}", file=sys.stderr)
        return None


def frame_box(img) -> int:
    """İmleç ve halka sığacak kare pencere kenarı."""
    return max(BOX, HOT - img.hot_x + img.w + 2, HOT - img.hot_y + img.h + 2)


def compose_frame(img, box: int, ring: int = 0) -> bytes:
    """box×box BGRA (ön-çarpılmış) kare: uçta halka + imleç (ucu HOT,HOT).

    Saf Python — cairo gerekmez; Windows ve macOS'ta da aynı kare çıkar.
    Halka 2 px kalınlığında, kenarları yumuşatılmış; imleç halkanın üstüne
    "over" kuralıyla bindirilir."""
    out = bytearray(box * box * 4)
    if ring:
        r, g, b = PULSE
        fade = max(0.0, 1.0 - (ring - 5) / 12)
        peak = 0.85 * fade
        c = HOT + 0.5
        lo, hi = max(0, int(c - ring - 3)), min(box, int(c + ring + 3))
        for py in range(lo, hi):
            for px in range(lo, hi):
                d = abs(math.hypot(px + 0.5 - c, py + 0.5 - c) - ring)
                cov = max(0.0, min(1.0, 1.5 - d))     # 2 px çizgi: |d|≤0.5 tam, 1.5'te sıfır
                if cov <= 0:
                    continue
                a = peak * cov
                i = (py * box + px) * 4
                out[i:i + 4] = bytes((int(b * a + 0.5), int(g * a + 0.5), int(r * a + 0.5),
                                      int(255 * a + 0.5)))
    ox, oy = HOT - img.hot_x, HOT - img.hot_y
    src = img.argb
    for y in range(img.h):
        ty = oy + y
        if not 0 <= ty < box:
            continue
        for x in range(img.w):
            tx = ox + x
            if not 0 <= tx < box:
                continue
            s = (y * img.w + x) * 4
            sa = src[s + 3]
            if sa == 0:
                continue
            i = (ty * box + tx) * 4
            if sa == 255:
                out[i:i + 4] = src[s:s + 4]
                continue
            k = 255 - sa
            for ch in range(4):
                out[i + ch] = min(255, src[s + ch] + (out[i + ch] * k + 127) // 255)
    return bytes(out)


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


# ------------------------------------------------------------- ortak imleç
class AgentCursorBase:
    """Arka uçların ortak davranışı. Alt sınıf şunları sağlar:

        _place(x, y)     pencereyi ucu (x, y)'de olacak şekilde taşı (hızlı, beklemesiz)
        _draw(ring=0)    kareyi çiz (ring > 0 → tık halkası)
        set_visible(v)   göster / gizle
        close()          pencereyi yok et
        pump()           pencere sisteminin bekleyen olaylarını işle (gerekiyorsa)
        _end_move()      hareket bitince (ör. X sync)
    """
    name = "?"
    x, y = 300, 300

    def pump(self) -> None:
        pass

    def _end_move(self) -> None:
        pass

    def wait(self, sec: float) -> None:
        """Bekle ama pencere olaylarını işlemeye devam et."""
        self.pump()
        if sec > 0:
            time.sleep(sec)

    def move_to(self, x, y, duration=None) -> None:
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
            self.wait(t0 + n * frame - time.monotonic())
            n += 1
        self._place(x, y)
        self._end_move()

    def pulse(self) -> None:
        """Tık geri bildirimi: uçtan genişleyen halka."""
        for r in RINGS:
            self._draw(ring=r)
            self.wait(0.035)
        self._draw()


# ------------------------------------------------------------------ taşıma
def _user() -> str:
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return "user"


def use_tcp() -> bool:
    return os.name == "nt" or os.environ.get("NEXREMOTE_CURSOR_TCP") == "1"


def sock_path() -> str:
    """POSIX Unix soketi (NEXREMOTE_CURSOR_SOCK ile değiştirilebilir)."""
    uid = os.getuid() if hasattr(os, "getuid") else _user()
    return os.environ.get("NEXREMOTE_CURSOR_SOCK", f"/tmp/nexremote-cursor-{uid}.sock")


def endpoint_file() -> str:
    """TCP kipinde port + belirteç dosyası (kullanıcıya özel geçici dizin)."""
    if os.environ.get("NEXREMOTE_CURSOR_ENDPOINT"):
        return os.environ["NEXREMOTE_CURSOR_ENDPOINT"]
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"nexremote-cursor-{_user()}.json")


def _listen() -> tuple[socket.socket, str]:
    """Dinleyen soket + (TCP'de) belirteç. Belirteç Unix sokette boş."""
    if use_tcp():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        token = secrets.token_hex(16)
        path = endpoint_file()
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"port": srv.getsockname()[1], "token": token, "pid": os.getpid()}, f)
        os.replace(tmp, path)
        return srv, token
    path = sock_path()
    if os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(8)
    return srv, ""


def _connect(timeout: float) -> socket.socket:
    if use_tcp():
        with open(endpoint_file()) as f:
            ep = json.load(f)
        s = socket.create_connection(("127.0.0.1", int(ep["port"])), timeout=timeout)
        s.sendall((ep["token"] + "\n").encode())
        return s
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path())
    except BaseException:
        s.close()
        raise
    return s


def _readline(s: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(64)
        if not chunk:
            break
        buf += chunk
    return buf.decode(errors="replace").strip()


# ------------------------------------------------------------------ daemon
def _execute(cursor, cmd: list[str]) -> str:
    c = cmd[0].upper()
    if c == "MOVE":
        cursor.move_to(float(cmd[1]), float(cmd[2]), float(cmd[3]) if len(cmd) > 3 else None)
    elif c == "CLICK":
        cursor.pulse()
    elif c == "SHOW":
        cursor.set_visible(True)
    elif c == "HIDE":
        cursor.set_visible(False)
    else:
        return "ERR"
    return "OK"


def serve(cursor, ready=None) -> None:
    """Komutları dinle; pencere işlerini ÇAĞIRAN (ana) iş parçacığında yap.
    QUIT gelince pencereyi kapatıp döner."""
    srv, token = _listen()
    jobs: "queue.Queue[tuple[list[str], list, threading.Event]]" = queue.Queue()
    stop = threading.Event()
    where = "tcp 127.0.0.1:%d" % srv.getsockname()[1] if token else sock_path()
    print(f"[cursor] hazır ({cursor.name}): {where}", flush=True)
    if ready is not None:
        ready.set()

    def handle(conn: socket.socket) -> None:
        try:
            f = conn.makefile("r", encoding="utf-8", errors="replace")
            if token and not hmac.compare_digest(f.readline().strip(), token):
                return
            for line in f:
                p = line.split()
                if not p:
                    continue
                c = p[0].upper()
                if c == "PING":
                    conn.sendall(b"PONG\n")
                    continue
                if c == "QUIT":
                    stop.set()
                    jobs.put((p, [], threading.Event()))
                    conn.sendall(b"BYE\n")
                    return
                box: list = []
                done = threading.Event()
                jobs.put((p, box, done))
                done.wait()
                conn.sendall(((box[0] if box else "ERR") + "\n").encode())
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def accept() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    try:
        while not stop.is_set():
            try:
                cmd, box, done = jobs.get(timeout=0.03)
            except queue.Empty:
                cursor.pump()
                continue
            if stop.is_set():
                break
            try:
                box.append(_execute(cursor, cmd))
            except Exception as e:
                print(f"[cursor] {cmd[0]}: {e}", file=sys.stderr)
                box.append("ERR")
            done.set()
    finally:
        try:
            srv.close()
        except OSError:
            pass
        try:
            os.unlink(endpoint_file() if token else sock_path())
        except OSError:
            pass
        cursor.close()


def daemon_running() -> bool:
    try:
        s = _connect(1.5)
    except (OSError, ValueError, KeyError):
        return False
    try:
        s.sendall(b"PING\n")
        return _readline(s) == "PONG"
    except OSError:
        return False
    finally:
        s.close()


def send(cmd: str, timeout: float = 5.0) -> str:
    s = _connect(timeout)
    try:
        s.sendall((cmd + "\n").encode())
        return _readline(s)
    finally:
        s.close()


def start_daemon(display: str = "") -> bool:
    """Daemon çalışmıyorsa başlat (ayrı süreç; çağıran kapansa da yaşar)."""
    if daemon_running():
        return True
    import subprocess
    core = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = core + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    args = [sys.executable, "-m", "nexremote.agent_cursor"]
    if display:
        env["DISPLAY"] = display
        args += ["--display", display]
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000 | 0x00000200   # CREATE_NO_WINDOW | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env=env, **kw)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if daemon_running():
            return True
        time.sleep(0.15)
    return False


# ------------------------------------------------------------- arka uç seçimi
def available() -> tuple[bool, str]:
    """Bu sistemde ajan imleci çizilebilir mi? (neden + nasıl)"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.WinDLL("user32")
        except (OSError, AttributeError) as e:
            return (False, f"user32 yüklenemedi: {e}")
        return (True, "hazır (Win32 katmanlı pencere: click-through, en üstte, odak almaz)")
    if sys.platform == "darwin":
        from . import maccursor
        return maccursor.available()
    try:
        import Xlib  # noqa: F401
        from Xlib.ext import shape  # noqa: F401
    except Exception as e:
        return (False, f"python-xlib + SHAPE gerekli: {e}")
    if not os.environ.get("DISPLAY"):
        return (False, "DISPLAY yok (X11/XWayland gerekir)")
    return (True, "hazır (X11/XWayland override-redirect pencere: click-through)")


def make_cursor(display: str = ""):
    if os.name == "nt":
        from .wincursor import WinAgentCursor
        return WinAgentCursor()
    if sys.platform == "darwin":
        from .maccursor import MacAgentCursor
        return MacAgentCursor()
    from .xcursor import XAgentCursor
    return XAgentCursor(display=display or os.environ.get("DISPLAY", ":0"))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    display = ""
    for i, a in enumerate(argv):
        if a == "--display" and i + 1 < len(argv):
            display = argv[i + 1]
    serve(make_cursor(display))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
