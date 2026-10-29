#!/usr/bin/env python3
"""Ajan imleci (xcursor.py) — İZOLE Xvfb'de: biçim, uç hizası, hareketin akıcılığı.

    tests/x11_sandbox.sh python3 tests/test_cursor_x11.py

Gerçek ekrana dokunmaz. Kök pencere düz beyaza boyanır; imleç penceresinin
görünen pikselleri kökten okunarak ok biçimi ve saydam zemin doğrulanır.
"""
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

if os.environ.get("NEXR_SANDBOX_KIND") != "x11":
    sys.exit("bu test yalnızca tests/x11_sandbox.sh içinde çalışır (gerçek masaüstüne dokunmaz)")

# yerleşik ok önce sınanır; tema imleci aşağıda ayrıca
os.environ["NEXREMOTE_CURSOR_THEME"] = "0"
from nexremote import xcursor as xc  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("  ✓ " if ok else "  ✗ ") + name + (f" — {detail}" if detail else ""))


# ------------------------------------------------------------- saf mantık
check("min_jerk uçlar 0 ve 1", xc.min_jerk(0) == 0 and abs(xc.min_jerk(1) - 1) < 1e-9)
samples = [xc.min_jerk(i / 100) for i in range(101)]
check("min_jerk monoton artar", all(b >= a for a, b in zip(samples, samples[1:])))
# uçlarda hız ~0: ilk ve son adım ortadaki adımdan çok küçük
mid = samples[51] - samples[50]
check("min_jerk yumuşak kalkış/duruş", samples[1] < mid * 0.01 and 1 - samples[99] < mid * 0.01)
d_short, d_long = xc.move_duration(40), xc.move_duration(1800)
check("süre mesafeyle artar, sınırlı", 0.14 < d_short < d_long <= 0.7, f"{d_short:.2f}s / {d_long:.2f}s")
cx, cy = xc.arc_control(0, 0, 1000, 0)
check("kavis küçük ve dik", abs(cx - 500) < 1e-6 and 0 < abs(cy) <= 48, f"ofset {cy:.1f}px")
edge, fill = xc.arrow_spans()
check("ok ucu (0,0) pikselinde", (0, 0, 1) in edge)
check("ok kenar + dolgu", len(edge) > 20 and len(fill) > 15, f"{len(edge)} kenar, {len(fill)} dolgu aralığı")

# --------------------------------------------------------- gerçek X sunucusu
from Xlib import X, display  # noqa: E402

d = display.Display(os.environ["DISPLAY"])
root = d.screen().root
white = d.screen().white_pixel
root.change_attributes(background_pixel=white)
root.clear_area(0, 0, 0, 0)
d.sync()

cur = xc.XAgentCursor(os.environ["DISPLAY"])
cur.move_to(400, 300, duration=0.05)
time.sleep(0.2)


def px(x, y):
    img = root.get_image(x, y, 1, 1, X.ZPixmap, 0xffffffff)
    b = img.data
    return (b[2], b[1], b[0])


tip = px(400, 300)
check("uç piksel tam hedefte ve koyu kenar", sum(tip) < 120, str(tip))
inner = px(400 + 3, 300 + 8)
check("ok içi yeşil dolgu", inner[1] > 180 and inner[0] < 150, str(inner))
outside = [px(400 + 25, 300 + 2), px(400 - 10, 300 - 10), px(400 + 2, 300 + 30)]
check("ok dışı saydam (siyah kare yok)", all(p == (255, 255, 255) for p in outside), str(outside))

# hareket: kare zamanları ve yol
frames: list[tuple[float, int, int]] = []
orig = cur._place


def spy(x, y):
    frames.append((time.monotonic(), x, y))
    orig(x, y)


cur._place = spy
t0 = time.monotonic()
cur.move_to(1200, 700)
took = time.monotonic() - t0
want = xc.move_duration(math.hypot(800, 400))
check("süre plana uygun", abs(took - want) < 0.05, f"{took:.3f}s / plan {want:.3f}s")
# uçlarda hız piksel altıdır, konum birkaç kare değişmez (kaydedilmez) —
# takılma ölçüsü hareketin ortadaki %80'inde
body = [f for f in frames if 0.1 * took <= f[0] - t0 <= 0.9 * took]
gaps = [b[0] - a[0] for a, b in zip(body, body[1:])]
fps = len(frames) / took
check("kare hızı ≥ 90 fps", fps >= 90, f"{fps:.0f} fps, {len(frames)} kare")
check("takılma yok (en uzun kare arası < 2 kare)", max(gaps) < 2 / xc.FPS, f"{max(gaps) * 1000:.1f} ms")
check("hedefte biter", (frames[-1][1], frames[-1][2]) == (1200, 700))
steps = [math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(frames, frames[1:])]
check("orta hız uçlardan yüksek (yumuşak)", max(steps[:3]) < max(steps) / 3
      and max(steps[-3:]) < max(steps) / 3, f"en büyük adım {max(steps):.0f}px")
check("geri gitme yok", all(b[1] >= a[1] for a, b in zip(frames, frames[1:])))
cur.close()

# ------------------------------------------------- tema imleci (ARGB pencere)
os.environ["NEXREMOTE_CURSOR_THEME"] = "1"
theme = xc.theme_cursor()
if theme is None:
    print("  ⊘ bu makinede imleç teması bulunamadı — tema imleci testi atlandı")
else:
    cur = xc.XAgentCursor(os.environ["DISPLAY"])
    check("tema imleci yüklendi", cur.theme is not None, theme.source.split(":")[0])
    check("32 bit ARGB pencere", cur._win.get_geometry().depth == 32)
    cur.move_to(500, 400, duration=0.05)
    time.sleep(0.2)
    opaque = [(x, y) for y in range(theme.h) for x in range(theme.w)
              if theme.alpha_at(x, y) == 255]
    ox, oy = opaque[len(opaque) // 2]
    inside = px(500 - theme.hot_x + ox, 400 - theme.hot_y + oy)
    check("tema pikseli ekranda (ucu hedefte hizalı)", inside != (255, 255, 255), str(inside))
    far = px(500 - theme.hot_x + theme.w + 3, 400 - theme.hot_y + theme.h + 3)
    check("tema imleci dışı saydam", far == (255, 255, 255), str(far))
    cur.pulse()
    check("tık halkası sonrası imleç yerinde", px(500 - theme.hot_x + ox, 400 - theme.hot_y + oy) == inside)
    cur.close()

# ------------------------------------- daemon + facade (ayrı süreç, Unix soketi)
import tempfile  # noqa: E402
os.environ["NEXREMOTE_CURSOR_SOCK"] = os.path.join(tempfile.mkdtemp(), "c.sock")
from nexremote import agent_cursor as ac  # noqa: E402
from nexremote.nexremote import NexRemote  # noqa: E402
nr = NexRemote()
check("facade.pointer_available X11'de", nr.pointer_available()[0], nr.pointer_available()[1])
try:
    moved = nr.cursor(640, 360, click=True)
except Exception as e:
    moved = False
    print(f"    {type(e).__name__}: {e}")
check("facade.cursor daemon'ı başlattı (python -m nexremote.agent_cursor)",
      moved and ac.daemon_running())
time.sleep(0.2)
tip = px(641, 362)
check("daemon'ın imleci ekranda, ucu hedefte", tip != (255, 255, 255), str(tip))
check("cursor_hide", nr.cursor_hide() and (time.sleep(0.2) or px(641, 362) == (255, 255, 255)))
check("QUIT", ac.send("QUIT") == "BYE")

ok = sum(1 for _, o in results if o)
print(f"İMLEÇ: {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
