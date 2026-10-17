"""Kullanıcının imleç temasından ok imleci — ajan imleci kullanıcınınkiyle aynı görünsün.

Sıra:
  1. Temanın SVG'si — hyprcursor arşivi (`hyprcursors/left_ptr.hlc`, içinde SVG)
     ya da KDE'nin `cursors_scalable/left_ptr/*.svg` dosyası. pycairo ile her
     boyutta keskin çizilir.
  2. Temanın Xcursor dosyası (`cursors/left_ptr`) — en yakın boyuttaki kare.
  3. Hiçbiri yoksa None: çağıran yerleşik oka düşer.

Dönüş: CursorImage(w, h, hot_x, hot_y, argb) — argb satır satır, piksel başına
4 bayt (B, G, R, A), ön-çarpılmış (premultiplied) alfa. X sunucusunun 32 bit
ARGB görseli bu düzeni doğrudan alır.

SVG desteği bilinçli olarak küçüktür: imleç temalarının kullandığı `path`
(M L H V C S Q T Z, göreli/mutlak), `fill`, `stroke`, `stroke-width`,
`stroke-linejoin`, `fill-rule`, `opacity`, `transform` (matrix/translate/scale)
ve `viewBox`. Gradyan, metin, filtre yok — tanınmayan öğe görülürse SVG
reddedilir ve Xcursor'a düşülür (yanlış çizmektense hiç çizmemek).
"""
from __future__ import annotations

import configparser
import math
import os
import re
import struct
import zipfile
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET

NAMES = ("left_ptr", "default", "arrow", "top_left_arrow")


@dataclass
class CursorImage:
    w: int
    h: int
    hot_x: int
    hot_y: int
    argb: bytes          # BGRA, ön-çarpılmış
    source: str          # bilgi: hangi dosyadan, hangi yolla

    def alpha_at(self, x: int, y: int) -> int:
        return self.argb[(y * self.w + x) * 4 + 3]


# ------------------------------------------------------------- tema bulma
def theme_and_size() -> tuple[Optional[str], int]:
    """Masaüstünün imleç teması ve boyutu (mantıksal piksel)."""
    theme = os.environ.get("XCURSOR_THEME") or None
    size = 0
    try:
        size = int(os.environ.get("XCURSOR_SIZE", "0"))
    except ValueError:
        size = 0
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                       "kcminputrc")
    if os.path.isfile(cfg):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(cfg, encoding="utf-8")
            if cp.has_section("Mouse"):
                # ortam değişkeni (oturumun gerçekte kullandığı) önceliklidir
                theme = theme or cp.get("Mouse", "cursorTheme", fallback=None)
                try:
                    size = size or int(cp.get("Mouse", "cursorSize", fallback="0"))
                except ValueError:
                    pass
        except configparser.Error:
            pass
    if not theme:
        gtk = os.path.expanduser("~/.config/gtk-3.0/settings.ini")
        if os.path.isfile(gtk):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(gtk, encoding="utf-8")
                theme = cp.get("Settings", "gtk-cursor-theme-name", fallback=None)
                size = size or int(cp.get("Settings", "gtk-cursor-theme-size", fallback="0"))
            except (configparser.Error, ValueError):
                pass
    return theme, size or 24


def _theme_dirs(theme: str) -> list[str]:
    bases = [os.path.expanduser("~/.local/share/icons"), os.path.expanduser("~/.icons")]
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d:
            bases.append(os.path.join(d, "icons"))
    bases.append("/usr/share/pixmaps")
    return [os.path.join(b, theme) for b in bases if os.path.isdir(os.path.join(b, theme))]


def _inherits(theme_dir: str) -> list[str]:
    idx = os.path.join(theme_dir, "index.theme")
    if not os.path.isfile(idx):
        return []
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(idx, encoding="utf-8")
        raw = cp.get("Icon Theme", "Inherits", fallback="")
    except configparser.Error:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


# ------------------------------------------------------------------ yükle
def load(theme: Optional[str] = None, size: Optional[int] = None,
         scale: float = 1.0) -> Optional[CursorImage]:
    """Ok imlecini yükle. scale: ekran ölçeği (HiDPI'da 2 → iki kat piksel)."""
    t, s = theme_and_size()
    theme = theme or t
    size = int(round((size or s) * scale))
    if not theme:
        return None
    seen: set[str] = set()
    queue = [theme]
    while queue:
        name = queue.pop(0)
        if name in seen or len(seen) > 8:
            continue
        seen.add(name)
        for d in _theme_dirs(name):
            img = _from_svg(d, size) or _from_xcursor(d, size)
            if img is not None:
                return img
            queue.extend(_inherits(d))
    return None


def _from_svg(theme_dir: str, size: int) -> Optional[CursorImage]:
    try:
        import cairo  # noqa: F401
    except ImportError:
        return None
    # hyprcursor: hyprcursors/<ad>.hlc = zip(meta.hl + svg/png)
    for name in NAMES:
        hlc = os.path.join(theme_dir, "hyprcursors", name + ".hlc")
        if not os.path.isfile(hlc):
            continue
        try:
            with zipfile.ZipFile(hlc) as z:
                meta = z.read("meta.hl").decode("utf-8", "replace")
                hx, hy, files = _parse_hl_meta(meta)
                svgs = [f for f in files if f.lower().endswith(".svg")]
                if not svgs:
                    continue
                data = z.read(svgs[0])
        except (zipfile.BadZipFile, KeyError, OSError):
            continue
        img = render_svg(data, size, hx, hy, f"{hlc}:{svgs[0]}")
        if img is not None:
            return img
    # KDE (Plasma 6): cursors_scalable/<ad>/metadata.json + svg
    for name in NAMES:
        d = os.path.join(theme_dir, "cursors_scalable", name)
        meta = os.path.join(d, "metadata.json")
        if not os.path.isfile(meta):
            continue
        try:
            import json
            with open(meta, encoding="utf-8") as f:
                frames = json.load(f)
            fr = frames[0]
            nominal = float(fr.get("nominal_size", 32)) or 32.0
            with open(os.path.join(d, fr["filename"]), "rb") as f:
                data = f.read()
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
        img = render_svg(data, size, float(fr.get("hotspot_x", 0)) / nominal,
                         float(fr.get("hotspot_y", 0)) / nominal, os.path.join(d, fr["filename"]))
        if img is not None:
            return img
    return None


def _parse_hl_meta(meta: str) -> tuple[float, float, list[str]]:
    hx = hy = 0.0
    files: list[str] = []
    for line in meta.splitlines():
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k == "hotspot_x":
            hx = float(v)
        elif k == "hotspot_y":
            hy = float(v)
        elif k == "define_size":
            # "0, dosya.svg" ya da "24, dosya.png, 50"
            parts = [p.strip() for p in v.split(",")]
            if len(parts) >= 2:
                files.append(parts[1])
    return hx, hy, files


def _from_xcursor(theme_dir: str, size: int) -> Optional[CursorImage]:
    for name in NAMES:
        p = os.path.join(theme_dir, "cursors", name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "rb") as f:
                img = parse_xcursor(f.read(), size)
        except (OSError, ValueError, struct.error):
            continue
        if img is not None:
            img.source = p
            return img
    return None


def parse_xcursor(b: bytes, size: int) -> Optional[CursorImage]:
    """Xcursor dosyasından istenen boyuta en yakın ilk kare (animasyonun ilk karesi)."""
    if b[:4] != b"Xcur":
        raise ValueError("Xcursor değil")
    _hdr, _ver, n = struct.unpack("<III", b[4:16])
    best = None
    for i in range(n):
        typ, sub, pos = struct.unpack("<III", b[16 + 12 * i:28 + 12 * i])
        if typ != 0xFFFD0002:
            continue
        if best is None or abs(sub - size) < abs(best[0] - size):
            best = (sub, pos)
    if best is None:
        return None
    pos = best[1]
    _h, _t, _s, _v, w, h, xh, yh, _delay = struct.unpack("<IIIIIIIII", b[pos:pos + 36])
    if not (0 < w <= 512 and 0 < h <= 512):
        raise ValueError("geçersiz Xcursor boyutu")
    raw = b[pos + 36:pos + 36 + w * h * 4]
    if len(raw) != w * h * 4:
        raise ValueError("kesik Xcursor")
    # Xcursor pikselleri zaten ön-çarpılmış ARGB (küçük uçlu → B G R A baytları)
    return CursorImage(w, h, xh, yh, bytes(raw), "xcursor")


# --------------------------------------------------------------- SVG çizimi
_NUM = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"([MmLlHhVvCcSsQqTtAaZz])|({_NUM})")


def parse_path(d: str) -> list[tuple[str, list[float]]]:
    """SVG path verisini mutlak komutlara çevir: M L C Q Z (H/V/S/T açılır)."""
    toks = _TOKEN.findall(d)
    out: list[tuple[str, list[float]]] = []
    i = 0
    cmd = ""
    cx = cy = sx = sy = 0.0
    last_c2: Optional[tuple[float, float]] = None   # S için
    last_q: Optional[tuple[float, float]] = None    # T için

    def nums(k: int) -> list[float]:
        nonlocal i
        vals = []
        for _ in range(k):
            if i >= len(toks) or toks[i][0]:
                raise ValueError(f"path: {cmd} için eksik sayı")
            vals.append(float(toks[i][1]))
            i += 1
        return vals

    while i < len(toks):
        if toks[i][0]:
            cmd = toks[i][0]
            i += 1
        elif not cmd:
            raise ValueError("path komutla başlamıyor")
        rel = cmd.islower()
        c = cmd.upper()
        if c == "Z":
            out.append(("Z", []))
            cx, cy = sx, sy
            last_c2 = last_q = None
            continue
        if c == "A":
            raise ValueError("path: yay (A) desteklenmiyor")
        if c == "M":
            x, y = nums(2)
            if rel:
                x, y = cx + x, cy + y
            out.append(("M", [x, y]))
            cx, cy = sx, sy = x, y
            cmd = "l" if rel else "L"     # M'den sonraki çiftler L sayılır
            last_c2 = last_q = None
        elif c == "L":
            x, y = nums(2)
            if rel:
                x, y = cx + x, cy + y
            out.append(("L", [x, y]))
            cx, cy = x, y
            last_c2 = last_q = None
        elif c == "H":
            (x,) = nums(1)
            cx = cx + x if rel else x
            out.append(("L", [cx, cy]))
            last_c2 = last_q = None
        elif c == "V":
            (y,) = nums(1)
            cy = cy + y if rel else y
            out.append(("L", [cx, cy]))
            last_c2 = last_q = None
        elif c in ("C", "S"):
            if c == "C":
                x1, y1, x2, y2, x, y = nums(6)
                if rel:
                    x1, y1, x2, y2 = cx + x1, cy + y1, cx + x2, cy + y2
            else:
                x2, y2, x, y = nums(4)
                if rel:
                    x2, y2 = cx + x2, cy + y2
                x1, y1 = (2 * cx - last_c2[0], 2 * cy - last_c2[1]) if last_c2 else (cx, cy)
            if rel:
                x, y = cx + x, cy + y
            out.append(("C", [x1, y1, x2, y2, x, y]))
            last_c2, last_q = (x2, y2), None
            cx, cy = x, y
        elif c in ("Q", "T"):
            if c == "Q":
                qx, qy, x, y = nums(4)
                if rel:
                    qx, qy = cx + qx, cy + qy
            else:
                x, y = nums(2)
                qx, qy = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
            if rel:
                x, y = cx + x, cy + y
            out.append(("Q", [qx, qy, x, y]))
            last_q, last_c2 = (qx, qy), None
            cx, cy = x, y
    return out


def _color(v: Optional[str]) -> Optional[tuple[float, float, float]]:
    if v is None:
        return None
    v = v.strip().lower()
    if v in ("none", "transparent", ""):
        return None
    named = {"black": "#000000", "white": "#ffffff"}
    v = named.get(v, v)
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    m = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", v)
    if m:
        return tuple(int(g) / 255 for g in m.groups())  # type: ignore[return-value]
    raise ValueError(f"desteklenmeyen renk: {v}")


def _transform(v: Optional[str]):
    import cairo
    m = cairo.Matrix()
    if not v:
        return m
    for fn, args in re.findall(r"(\w+)\s*\(([^)]*)\)", v):
        a = [float(x) for x in re.findall(_NUM, args)]
        if fn == "matrix" and len(a) == 6:
            t = cairo.Matrix(*a)
        elif fn == "translate":
            t = cairo.Matrix(x0=a[0], y0=a[1] if len(a) > 1 else 0.0)
        elif fn == "scale":
            t = cairo.Matrix(xx=a[0], yy=a[1] if len(a) > 1 else a[0])
        else:
            raise ValueError(f"desteklenmeyen dönüşüm: {fn}")
        m = t.multiply(m)
    return m


def _style(el: ET.Element, parent: dict) -> dict:
    st = dict(parent)
    for k in ("fill", "stroke", "stroke-width", "stroke-linejoin", "stroke-linecap",
              "fill-rule", "opacity", "fill-opacity", "stroke-opacity"):
        if el.get(k) is not None:
            st[k] = el.get(k)
    for decl in (el.get("style") or "").split(";"):
        k, _, v = decl.partition(":")
        if k.strip():
            st[k.strip()] = v.strip()
    return st


def render_svg(data: bytes, size: int, hot_x: float, hot_y: float,
               source: str = "svg") -> Optional[CursorImage]:
    """İmleç SVG'sini size×size ARGB'ye çiz. hot_x/hot_y: 0..1 oranı.
    Desteklenmeyen içerikte None (çağıran Xcursor'a düşer)."""
    import cairo
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    vb = [float(x) for x in re.findall(_NUM, root.get("viewBox") or "")]
    if len(vb) != 4:
        try:
            vb = [0, 0, float(re.findall(_NUM, root.get("width", "0"))[0]),
                  float(re.findall(_NUM, root.get("height", "0"))[0])]
        except IndexError:
            return None
    if vb[2] <= 0 or vb[3] <= 0 or size <= 0:
        return None
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surf)
    ctx.scale(size / vb[2], size / vb[3])
    ctx.translate(-vb[0], -vb[1])
    base = {"fill": root.get("fill", "#000000") or "#000000"}
    try:
        _draw(ctx, root, _style(root, base))
    except ValueError:
        return None
    surf.flush()
    stride = surf.get_stride()
    buf = bytes(surf.get_data())
    if stride != size * 4:
        buf = b"".join(buf[r * stride:r * stride + size * 4] for r in range(size))
    if not any(buf[3::4]):
        return None
    # cairo ARGB32 = yerel uçlu ön-çarpılmış ARGB → x86/ARM küçük uçta B G R A
    return CursorImage(size, size, int(round(hot_x * size)), int(round(hot_y * size)),
                       buf, f"svg:{source}")


_SKIP = {"title", "desc", "metadata", "defs"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _draw(ctx, el: ET.Element, st: dict) -> None:
    for ch in el:
        tag = _local(ch.tag)
        if tag in _SKIP:
            continue
        cst = _style(ch, st)
        ctx.save()
        if ch.get("transform"):
            ctx.transform(_transform(ch.get("transform")))
        if tag == "g":
            _draw(ctx, ch, cst)
        elif tag == "path":
            _path(ctx, ch.get("d") or "", cst)
        elif tag == "rect":
            x, y = float(ch.get("x", 0)), float(ch.get("y", 0))
            w, h = float(ch.get("width", 0)), float(ch.get("height", 0))
            _path(ctx, f"M{x} {y}H{x + w}V{y + h}H{x}Z", cst)
        elif tag == "circle":
            cx, cy, r = float(ch.get("cx", 0)), float(ch.get("cy", 0)), float(ch.get("r", 0))
            ctx.new_path()
            ctx.arc(cx, cy, r, 0, 2 * math.pi)
            _paint(ctx, cst)
        else:
            raise ValueError(f"desteklenmeyen SVG öğesi: {tag}")
        ctx.restore()


def _path(ctx, d: str, st: dict) -> None:
    ctx.new_path()
    cx = cy = 0.0
    for c, a in parse_path(d):
        if c == "M":
            ctx.move_to(*a)
            cx, cy = a
        elif c == "L":
            ctx.line_to(*a)
            cx, cy = a
        elif c == "C":
            ctx.curve_to(*a)
            cx, cy = a[4], a[5]
        elif c == "Q":
            qx, qy, x, y = a          # ikinci dereceyi kübiğe yükselt
            ctx.curve_to(cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy),
                         x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), x, y)
            cx, cy = x, y
        elif c == "Z":
            ctx.close_path()
    _paint(ctx, st)


def _paint(ctx, st: dict) -> None:
    import cairo
    op = float(st.get("opacity", 1) or 1)
    fill = _color(st.get("fill"))
    stroke = _color(st.get("stroke"))
    path = ctx.copy_path()
    if fill is not None:
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD if st.get("fill-rule") == "evenodd"
                          else cairo.FILL_RULE_WINDING)
        ctx.set_source_rgba(*fill, op * float(st.get("fill-opacity", 1) or 1))
        ctx.fill()
    if stroke is not None:
        ctx.new_path()
        ctx.append_path(path)
        ctx.set_line_width(float(re.findall(_NUM, str(st.get("stroke-width", 1)))[0]))
        ctx.set_line_join({"round": cairo.LINE_JOIN_ROUND, "bevel": cairo.LINE_JOIN_BEVEL}
                          .get(st.get("stroke-linejoin", ""), cairo.LINE_JOIN_MITER))
        ctx.set_line_cap({"round": cairo.LINE_CAP_ROUND, "square": cairo.LINE_CAP_SQUARE}
                         .get(st.get("stroke-linecap", ""), cairo.LINE_CAP_BUTT))
        ctx.set_source_rgba(*stroke, op * float(st.get("stroke-opacity", 1) or 1))
        ctx.stroke()
    ctx.new_path()
