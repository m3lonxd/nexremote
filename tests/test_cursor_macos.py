#!/usr/bin/env python3
"""Ajan imleci, macOS arka ucu — sahte Objective-C katmanıyla çağrı dizisi.

Bu makinede macOS yok: objc_msgSend ve CoreGraphics taklit edilir. Sınanan
şey bizim gönderdiğimiz mesajlar, argümanları ve C prototipleridir (arm64'te
yanlış prototip = çöken çağrı). Gerçek AppKit davranışı (pencerenin
görünmesi, tıklamaların geçmesi) burada doğrulanamaz.
"""
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

from nexremote import agent_cursor as ac  # noqa: E402
from nexremote import maccursor as mc  # noqa: E402

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail and not ok else ""))


class FakeObjC:
    HOT = mc.CGPoint(4.0, 4.0)
    SIZE = mc.CGSize(17.0, 23.0)

    def __init__(self):
        self.calls = []
        self._n = 100
        self.classes = {}

    def cls(self, name):
        return self.classes.setdefault(name, f"<{name}>")

    def const(self, name):
        return f"<const {name}>"

    def send(self, obj, sel, *args, restype=mc.P, argtypes=()):
        self.calls.append((obj, sel, args, restype, tuple(argtypes)))
        if sel == "hotSpot":
            return self.HOT
        if sel == "size":
            return self.SIZE
        if sel.startswith("nextEventMatchingMask"):
            return None
        if restype is None:
            return None
        self._n += 1
        return self._n

    def find(self, sel, obj=None):
        return [c for c in self.calls if c[1] == sel and (obj is None or c[0] == obj)]


class FakeCG:
    def main_height(self):
        return 900.0

    def rgba(self, *c):
        return ("rgba",) + c


o, cg = FakeObjC(), FakeCG()
cur = mc.MacAgentCursor(objc=o, cg=cg)
box = cur.box

init = o.find("initWithContentRect:styleMask:backing:defer:")
check("NSWindow kenarlıksız, tamponlu", len(init) == 1 and init[0][2][1:] == (0, 2, False)
      and init[0][2][0].size.width == box, str(init[0][2][1:] if init else None))
check("init prototipi CGRect değerle", init and init[0][4] == (mc.CGRect, mc.UL, mc.UL, mc.B))


def last_arg(sel):
    c = o.find(sel, cur.win)
    return c[-1][2][0] if c else "YOK"


check("ignoresMouseEvents YES (click-through)", last_arg("setIgnoresMouseEvents:") is True)
check("saydam: opak değil + clearColor zemin + gölgesiz",
      last_arg("setOpaque:") is False and last_arg("setHasShadow:") is False
      and o.find("clearColor") and last_arg("setBackgroundColor:") is not None)
check("seviye ekran koruyucu (1000) — tam ekranın üstünde", last_arg("setLevel:") == 1000)
beh = last_arg("setCollectionBehavior:")
check("her Space'te, sabit, Cmd+Tab'de yok", beh & 1 and beh & 16 and beh & 64, hex(beh))
pol = o.find("setActivationPolicy:")
check("Dock simgesi yok (accessory)", pol and pol[0][2] == (1,))
check("sistem oku kullanıldı (NSCursor arrowCursor)", bool(o.find("arrowCursor")))

view = o.find("initWithFrame:")
fr = view[0][2][0] if view else None
check("görüntü ucu HOT noktasında (NSView y yukarı)",
      fr is not None and fr.origin.x == ac.HOT - 4 and fr.origin.y == box - (ac.HOT - 4) - 23,
      f"{fr.origin.x},{fr.origin.y}" if fr else "")
check("pencere öne getirildi", bool(o.find("orderFrontRegardless", cur.win)))

# ----------------------------------------------------------- koordinatlar
check("köken: üst-sol (x,y) → AppKit sol-alt",
      cur.origin(500, 400) == (500 - ac.HOT, 900 - (400 - ac.HOT) - box))
check("köken: ekranın tepesi", cur.origin(ac.HOT, ac.HOT) == (0, 900 - box))
o.calls.clear()
cur.move_to(500, 400, 0.05)
moves = o.find("setFrameOrigin:", cur.win)
end = moves[-1][2][0] if moves else None
check("move_to hedefte biter", end is not None and (end.x, end.y) == cur.origin(500, 400),
      f"{(end.x, end.y) if end else None}")
check("setFrameOrigin prototipi CGPoint", moves and moves[-1][4] == (mc.CGPoint,))
check("hareket ara kareler üretti", len(moves) > 2, str(len(moves)))
check("hareket boyunca olaylar işlendi (pump)", bool(o.find("drain")))

o.calls.clear()
cur.pulse()
hid = [c[2][0] for c in o.find("setHidden:", cur.ring)]
check("tık halkası: göster … sonunda gizle", hid[:1] == [False] and hid[-1] is True, str(hid))
frames = [c[2][0] for c in o.find("setFrame:", cur.ring)]
check("halka uç etrafında büyür", len(frames) == len(ac.RINGS)
      and all(f.origin.x + f.size.width / 2 == ac.HOT for f in frames)
      and all(f.origin.y + f.size.height / 2 == box - ac.HOT for f in frames)
      and frames[0].size.width < frames[-1].size.width)
check("CATransaction dengeli, animasyon kapalı",
      len(o.find("begin")) == len(o.find("commit")) == len(ac.RINGS) + 1
      and all(c[2] == (True,) for c in o.find("setDisableActions:")))
check("setOpacity float prototipi", all(c[4] == (mc.ctypes.c_float,) for c in o.find("setOpacity:")))

o.calls.clear()
cur.set_visible(False)
check("HIDE → orderOut:", bool(o.find("orderOut:", cur.win)))
cur.set_visible(True)
check("SHOW → orderFrontRegardless", bool(o.find("orderFrontRegardless", cur.win)))
cur.close()
check("close", bool(o.find("close")) and cur.win is None)

check("available() macOS dışında hayır", mc.available()[0] is False)
with mock.patch.object(ac.sys, "platform", "darwin"), mock.patch.object(ac.os, "name", "posix"), \
        mock.patch.object(mc, "ObjC", FakeObjC), mock.patch.object(mc, "CoreGraphics", FakeCG):
    picked = ac.make_cursor()
check("make_cursor macOS'ta AppKit arka ucunu seçer", isinstance(picked, mc.MacAgentCursor))

print(f"MACOS İMLEÇ: {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
