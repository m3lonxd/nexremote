#!/usr/bin/env python3
"""Ajan imleci, Linux arka ucu — saf Xlib, override-redirect pencere.

Neden Xlib: KDE Wayland'de XWayland üzerinden konumlanan TEK güvenilir yol
override-redirect penceredir. WM ona dokunmaz, konum tam kontrolümüzdedir.
Pencere yalnızca İMLEÇ BOYUTUNDA (küçük) ve arkasından tıklanabilir —
kullanıcıyı hiç engellemez.

Hareket, kare çizimi ve daemon platformdan bağımsızdır: agent_cursor.py.
Buradaki adlar (min_jerk, serve, send…) geriye uyum için yeniden dışa verilir.
"""
from __future__ import annotations

import math
import os
import sys

try:
    from .agent_cursor import (ARROW, BOX, COLOR, EDGE, FPS, HOT, PULSE, AgentCursorBase,  # noqa: F401
                               alpha_spans, arc_control, arrow_spans, bezier, compose_frame,
                               daemon_running, frame_box, min_jerk, move_duration, send,
                               serve, start_daemon, theme_cursor, _inside)
except ImportError:  # dosya yoluyla çalıştırıldı (paket bağlamı yok)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from nexremote.agent_cursor import (ARROW, BOX, COLOR, EDGE, FPS, HOT, PULSE,  # noqa: F401
                                        AgentCursorBase, alpha_spans, arc_control, arrow_spans,
                                        bezier, compose_frame, daemon_running, frame_box,
                                        min_jerk, move_duration, send, serve, start_daemon,
                                        theme_cursor, _inside)


def _to16(v: int) -> int:
    return v * 257  # 8-bit -> 16-bit


class XAgentCursor(AgentCursorBase):
    name = "X11"

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
    def _end_move(self) -> None:
        self.d.sync()

    def _place(self, x: int, y: int) -> None:
        self.x, self.y = x, y
        try:
            self._win.configure(x=x - HOT, y=y - HOT)
        except Exception:
            return
        self.d.flush()          # sync değil: kare başına gidiş-dönüş beklemesi yok

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


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    display = os.environ.get("DISPLAY", ":0")
    for i, a in enumerate(argv):
        if a == "--display" and i + 1 < len(argv):
            display = argv[i + 1]
    serve(XAgentCursor(display=display))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
