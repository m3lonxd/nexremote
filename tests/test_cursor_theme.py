#!/usr/bin/env python3
"""İmleç teması yükleyicisi (cursor_theme.py) — X gerektirmez, gerçek temaya dokunmaz.

Geçici dizinde sahte bir tema kurar (hyprcursor SVG + Xcursor), yükleyiciyi
ortam değişkenleriyle oraya yönlendirir.
"""
import os
import struct
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

from nexremote import cursor_theme as ct  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("  ✓ " if ok else "  ✗ ") + name + (f" — {detail}" if detail else ""))


# ----------------------------------------------------------- path ayrıştırma
p = ct.parse_path("M10 10 h5 v5 l-5 0 z")
check("göreli h/v/l + z", p == [("M", [10, 10]), ("L", [15, 10]), ("L", [15, 15]),
                                 ("L", [10, 15]), ("Z", [])], str(p))
p = ct.parse_path("M0 0 C1 1 2 2 3 3 S5 5 6 6")
check("S yansıyan kontrol noktası", p[2] == ("C", [4, 4, 5, 5, 6, 6]), str(p[2]))
p = ct.parse_path("m1 1 2 2")
check("M sonrası çiftler L sayılır (göreli)", p == [("M", [1, 1]), ("L", [3, 3])], str(p))
p = ct.parse_path("M0,0Q5,5,10,0T20,0")
check("Q + T", p[2] == ("Q", [15, -5, 20, 0]), str(p[2]))
p = ct.parse_path("M1e1-2.5L.5.5")
check("bilimsel gösterim ve bitişik sayılar", p == [("M", [10, -2.5]), ("L", [0.5, 0.5])], str(p))
for bad in ("L 1", "M 1", "M0 0 A1 1 0 0 1 5 5"):
    try:
        ct.parse_path(bad)
        check(f"hatalı path reddedilir: {bad!r}", False)
    except ValueError:
        check(f"hatalı path reddedilir: {bad!r}", True)

# ------------------------------------------------------------ hl meta
hx, hy, files = ct._parse_hl_meta("resize_algorithm = none\nhotspot_x = 0.21\nhotspot_y = 0.07\n"
                                  "define_override = arrow\ndefine_size = 0, left_ptr.svg\n")
check("hyprcursor meta", (hx, hy, files) == (0.21, 0.07, ["left_ptr.svg"]))

# ------------------------------------------------------------ SVG çizimi
SVG = (b'<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">'
       b'<g transform="translate(2 2)"><path d="M0 0 L0 24 L16 16 Z" fill="#202020" '
       b'stroke="#ffffff" stroke-width="2" stroke-linejoin="round"/></g></svg>')
try:
    import cairo  # noqa: F401
    have_cairo = True
except ImportError:
    have_cairo = False

if have_cairo:
    img = ct.render_svg(SVG, 64, 2 / 32, 2 / 32, "test")
    check("SVG 64 px'e çizildi", img is not None and (img.w, img.h) == (64, 64))
    check("hotspot ölçeklenir", (img.hot_x, img.hot_y) == (4, 4))
    check("dolgu opak", img.alpha_at(10, 30) == 255)
    check("dış saydam", img.alpha_at(60, 4) == 0)
    px = img.argb[(30 * 64 + 10) * 4:(30 * 64 + 10) * 4 + 3]
    check("dolgu rengi #202020", all(abs(c - 0x20) <= 2 for c in px), str(tuple(px)))
    check("desteklenmeyen öğe → None",
          ct.render_svg(b'<svg viewBox="0 0 8 8"><text>x</text></svg>', 16, 0, 0) is None)
    check("bozuk XML → None", ct.render_svg(b"<svg", 16, 0, 0) is None)
else:
    print("  ⊘ pycairo yok — SVG çizim testleri atlandı")


# ------------------------------------------------------------ Xcursor
def xcursor(sizes):
    toc, chunks = [], []
    pos = 16 + 12 * len(sizes)
    for s in sizes:
        pixels = bytes([0, 0, 0, 255]) * (s * s)
        chunk = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, s, 1, s, s, 1, 2, 0) + pixels
        toc.append(struct.pack("<III", 0xFFFD0002, s, pos))
        chunks.append(chunk)
        pos += len(chunk)
    return b"Xcur" + struct.pack("<III", 16, 0x10000, len(sizes)) + b"".join(toc) + b"".join(chunks)


x = ct.parse_xcursor(xcursor([16, 24, 48]), 30)
check("Xcursor en yakın boyut", x is not None and x.w == 24 and (x.hot_x, x.hot_y) == (1, 2))
try:
    ct.parse_xcursor(b"PNG.." + b"\0" * 20, 24)
    check("Xcursor olmayan dosya reddedilir", False)
except ValueError:
    check("Xcursor olmayan dosya reddedilir", True)

# ------------------------------------------------------- tema arama zinciri
with tempfile.TemporaryDirectory() as tmp:
    icons = os.path.join(tmp, "icons")
    child = os.path.join(icons, "nexr-child")
    parent = os.path.join(icons, "nexr-parent")
    os.makedirs(os.path.join(child))
    os.makedirs(os.path.join(parent, "hyprcursors"))
    os.makedirs(os.path.join(parent, "cursors"))
    with open(os.path.join(child, "index.theme"), "w") as f:
        f.write("[Icon Theme]\nName=c\nInherits=nexr-parent\n")
    with zipfile.ZipFile(os.path.join(parent, "hyprcursors", "left_ptr.hlc"), "w") as z:
        z.writestr("meta.hl", "hotspot_x = 0.0625\nhotspot_y = 0.0625\ndefine_size = 0, a.svg\n")
        z.writestr("a.svg", SVG)
    with open(os.path.join(parent, "cursors", "left_ptr"), "wb") as f:
        f.write(xcursor([24]))
    env = dict(os.environ)
    os.environ.update(XCURSOR_THEME="nexr-child", XCURSOR_SIZE="24", XDG_DATA_DIRS=tmp,
                      HOME=tmp, XDG_CONFIG_HOME=os.path.join(tmp, "cfg"))
    try:
        check("tema/boyut ortamdan", ct.theme_and_size() == ("nexr-child", 24))
        img = ct.load()
        if have_cairo:
            check("Inherits zinciriyle SVG bulundu", img is not None and img.source.startswith("svg:")
                  and (img.w, img.hot_x) == (24, 2), img and img.source)
        os.environ["XCURSOR_SIZE"] = "24"
        os.remove(os.path.join(parent, "hyprcursors", "left_ptr.hlc"))
        img = ct.load()
        check("SVG yoksa Xcursor'a düşer", img is not None and img.source.endswith("left_ptr"),
              img and img.source)
        check("olmayan tema → None", ct.load(theme="yok-boyle-tema") is None)
    finally:
        os.environ.clear()
        os.environ.update(env)

ok = sum(1 for _, o in results if o)
print(f"İMLEÇ TEMASI: {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
