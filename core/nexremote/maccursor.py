"""Ajan imleci, macOS arka ucu — ctypes ile Objective-C çalışma zamanı (pyobjc gerekmez).

    NSWindow (kenarlıksız)   styleMask 0, saydam zemin, gölgesiz
    ignoresMouseEvents YES   tıklamalar alttaki uygulamaya geçer (click-through)
    seviye 1000              kCGScreenSaverWindowLevel: tam ekran uygulamaların da üstünde
    canJoinAllSpaces …       her Space'te görünür, Mission Control/Cmd+Tab'de yok
    NSApplication accessory  Dock simgesi yok, odak çalmaz

İmleç görüntüsü kullanıcının SİSTEM okudur ([NSCursor arrowCursor] image +
hotSpot); tık halkası bir CALayer'dır. Koordinatlar macos.py ile aynı:
nokta (point), ana ekranın sol üstü (0,0), y aşağı. AppKit pencere
koordinatlarında y yukarı olduğundan yerleştirirken ana ekran yüksekliğiyle
çevrilir.

ÖNİZLEME: bu makinede macOS yok. Çağrı dizisi sahte Objective-C katmanıyla
sınanır (tests/test_cursor_macos.py); gerçek Mac'te çalıştırılmadı.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

from .agent_cursor import BOX, HOT, PULSE, RINGS, AgentCursorBase

NS_BACKING_BUFFERED = 2
NS_ACCESSORY = 1                                   # NSApplicationActivationPolicyAccessory
LEVEL = 1000                                       # kCGScreenSaverWindowLevel
# canJoinAllSpaces | stationary | ignoresCycle | fullScreenAuxiliary
BEHAVIOR = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8)
NS_IMAGE_SCALE_NONE = 2
NS_ANY_EVENT = 0xFFFFFFFFFFFFFFFF

APPKIT = "/System/Library/Frameworks/AppKit.framework/AppKit"
QUARTZCORE = "/System/Library/Frameworks/QuartzCore.framework/QuartzCore"
FOUNDATION = "/System/Library/Frameworks/Foundation.framework/Foundation"
CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def available() -> tuple[bool, str]:
    if sys.platform != "darwin":
        return (False, "macOS değil")
    if not ctypes.util.find_library("objc") or not os.path.exists(APPKIT):
        return (False, "Objective-C çalışma zamanı / AppKit bulunamadı")
    return (True, "hazır (önizleme: NSWindow ignoresMouseEvents, click-through, odak almaz)")


class ObjC:
    """objc_msgSend'i her imza için doğru prototiple çağırır (arm64'te
    değişken argümanlı çağrı yanlış olur; her imzaya ayrı CFUNCTYPE)."""

    def __init__(self) -> None:
        self.lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        for path in (FOUNDATION, APPKIT, QUARTZCORE):
            ctypes.cdll.LoadLibrary(path)
        self.lib.objc_getClass.restype, self.lib.objc_getClass.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        self.lib.sel_registerName.restype, self.lib.sel_registerName.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        self._addr = ctypes.cast(self.lib.objc_msgSend, ctypes.c_void_p).value
        self._fns: dict = {}
        self._sels: dict = {}
        self.foundation = ctypes.cdll.LoadLibrary(FOUNDATION)

    def cls(self, name: str):
        return self.lib.objc_getClass(name.encode())

    def send(self, obj, sel: str, *args, restype=ctypes.c_void_p, argtypes=()):
        key = (restype, tuple(argtypes))
        fn = self._fns.get(key)
        if fn is None:
            fn = self._fns[key] = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p,
                                                   *argtypes)(self._addr)
        s = self._sels.get(sel)
        if s is None:
            s = self._sels[sel] = self.lib.sel_registerName(sel.encode())
        return fn(obj, s, *args)

    def const(self, name: str):
        """Foundation/AppKit'te dışa verilmiş bir NSString sabiti."""
        return ctypes.c_void_p.in_dll(self.foundation, name).value


class CoreGraphics:
    def __init__(self) -> None:
        cg = ctypes.cdll.LoadLibrary(CORE_GRAPHICS)
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayBounds.restype, cg.CGDisplayBounds.argtypes = CGRect, [ctypes.c_uint32]
        cg.CGColorCreateGenericRGB.restype = ctypes.c_void_p
        cg.CGColorCreateGenericRGB.argtypes = [ctypes.c_double] * 4
        self.cg = cg

    def main_height(self) -> float:
        return self.cg.CGDisplayBounds(self.cg.CGMainDisplayID()).size.height

    def rgba(self, r: float, g: float, b: float, a: float):
        return self.cg.CGColorCreateGenericRGB(r, g, b, a)


B = ctypes.c_bool
D = ctypes.c_double
L = ctypes.c_long
UL = ctypes.c_ulong
P = ctypes.c_void_p


class MacAgentCursor(AgentCursorBase):
    name = "AppKit"

    def __init__(self, objc: ObjC | None = None, cg: CoreGraphics | None = None) -> None:
        self.o = objc or ObjC()
        self.cg = cg or CoreGraphics()
        o = self.o
        self._pool = o.send(o.send(o.cls("NSAutoreleasePool"), "alloc"), "init")
        self.app = o.send(o.cls("NSApplication"), "sharedApplication")
        o.send(self.app, "setActivationPolicy:", NS_ACCESSORY, restype=B, argtypes=(L,))
        o.send(self.app, "finishLaunching", restype=None)
        self._mode = o.const("NSDefaultRunLoopMode")

        arrow = o.send(o.cls("NSCursor"), "arrowCursor")
        image = o.send(arrow, "image")
        hot = o.send(arrow, "hotSpot", restype=CGPoint)
        size = o.send(image, "size", restype=CGSize)
        self.img_w, self.img_h = size.width, size.height
        self.hot_x, self.hot_y = hot.x, hot.y
        self.box = max(BOX, int(HOT - hot.x + size.width + 2), int(HOT - hot.y + size.height + 2))
        self._visible = True

        frame = CGRect(CGPoint(0, 0), CGSize(self.box, self.box))
        win = o.send(o.cls("NSWindow"), "alloc")
        self.win = o.send(win, "initWithContentRect:styleMask:backing:defer:",
                          frame, 0, NS_BACKING_BUFFERED, False,
                          argtypes=(CGRect, UL, UL, B))
        clear = o.send(o.cls("NSColor"), "clearColor")
        o.send(self.win, "setOpaque:", False, restype=None, argtypes=(B,))
        o.send(self.win, "setBackgroundColor:", clear, restype=None, argtypes=(P,))
        o.send(self.win, "setHasShadow:", False, restype=None, argtypes=(B,))
        o.send(self.win, "setIgnoresMouseEvents:", True, restype=None, argtypes=(B,))
        o.send(self.win, "setLevel:", LEVEL, restype=None, argtypes=(L,))
        o.send(self.win, "setCollectionBehavior:", BEHAVIOR, restype=None, argtypes=(UL,))
        o.send(self.win, "setReleasedWhenClosed:", False, restype=None, argtypes=(B,))

        content = o.send(self.win, "contentView")
        o.send(content, "setWantsLayer:", True, restype=None, argtypes=(B,))
        # NSView'da y yukarı: görüntünün sol üstü (HOT-hot) noktasına gelsin
        ix = HOT - hot.x
        iy = self.box - (HOT - hot.y) - size.height
        view = o.send(o.cls("NSImageView"), "alloc")
        view = o.send(view, "initWithFrame:", CGRect(CGPoint(ix, iy), size), argtypes=(CGRect,))
        o.send(view, "setImage:", image, restype=None, argtypes=(P,))
        o.send(view, "setImageScaling:", NS_IMAGE_SCALE_NONE, restype=None, argtypes=(UL,))
        o.send(content, "addSubview:", view, restype=None, argtypes=(P,))

        self.ring = o.send(o.cls("CALayer"), "layer")
        r, g, b = (c / 255 for c in PULSE)
        o.send(self.ring, "setBorderColor:", self.cg.rgba(r, g, b, 1.0), restype=None, argtypes=(P,))
        o.send(self.ring, "setBorderWidth:", 2.0, restype=None, argtypes=(D,))
        o.send(self.ring, "setHidden:", True, restype=None, argtypes=(B,))
        o.send(o.send(content, "layer"), "addSublayer:", self.ring, restype=None, argtypes=(P,))

        self._place(self.x, self.y)
        o.send(self.win, "orderFrontRegardless", restype=None)
        self.pump()

    # ------------------------------------------------------------ yerleşim
    def origin(self, x: int, y: int) -> tuple[float, float]:
        """Ucu (x, y) olacak pencerenin AppKit kökeni (sol alt, y yukarı)."""
        return float(x - HOT), self.cg.main_height() - (y - HOT) - self.box

    def _place(self, x: int, y: int) -> None:
        self.x, self.y = x, y
        ox, oy = self.origin(x, y)
        self.o.send(self.win, "setFrameOrigin:", CGPoint(ox, oy), restype=None, argtypes=(CGPoint,))

    def _draw(self, ring: int = 0) -> None:
        o = self.o
        tx = o.cls("CATransaction")
        o.send(tx, "begin", restype=None)
        o.send(tx, "setDisableActions:", True, restype=None, argtypes=(B,))
        if ring:
            cy = self.box - HOT                    # katmanda y yukarı
            fade = max(0.0, 1.0 - (ring - 5) / 12)
            rect = CGRect(CGPoint(HOT - ring, cy - ring), CGSize(2 * ring, 2 * ring))
            o.send(self.ring, "setFrame:", rect, restype=None, argtypes=(CGRect,))
            o.send(self.ring, "setCornerRadius:", float(ring), restype=None, argtypes=(D,))
            o.send(self.ring, "setOpacity:", 0.85 * fade, restype=None, argtypes=(ctypes.c_float,))
            o.send(self.ring, "setHidden:", False, restype=None, argtypes=(B,))
        else:
            o.send(self.ring, "setHidden:", True, restype=None, argtypes=(B,))
        o.send(tx, "commit", restype=None)
        self.pump()

    def pump(self) -> None:
        """Bekleyen AppKit olaylarını işle (pencere sunucusuyla eşzamanlama)."""
        o = self.o
        pool = o.send(o.send(o.cls("NSAutoreleasePool"), "alloc"), "init")
        past = o.send(o.cls("NSDate"), "distantPast")
        for _ in range(64):
            ev = o.send(self.app, "nextEventMatchingMask:untilDate:inMode:dequeue:",
                        NS_ANY_EVENT, past, self._mode, True, argtypes=(ctypes.c_ulonglong, P, P, B))
            if not ev:
                break
            o.send(self.app, "sendEvent:", ev, restype=None, argtypes=(P,))
        o.send(pool, "drain", restype=None)

    def set_visible(self, v: bool) -> None:
        self._visible = bool(v)
        if v:
            self.o.send(self.win, "orderFrontRegardless", restype=None)
        else:
            self.o.send(self.win, "orderOut:", None, restype=None, argtypes=(P,))
        self.pump()

    def close(self) -> None:
        if getattr(self, "win", None):
            self.o.send(self.win, "close", restype=None)
            self.win = None
