#!/usr/bin/env python3
"""Ekran yayınından kare (FRAME) — protokol ve görüntü çözümü. Gerçek
PipeWire/portal gerektirmez; sahte servis ve sahte yakalayıcı kullanır."""
import json
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

from nexremote import portal_input as pi  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("  ✓ " if ok else "  ✗ ") + name + (f" — {detail}" if detail else ""))


W, H, STRIDE = 4, 2, 20          # satır sonunda 4 bayt dolgu
# BGRx: piksel (0,0) kırmızı, (1,0) yeşil, (0,1) mavi
frame = bytearray(STRIDE * H)
frame[0:4] = bytes([0, 0, 255, 0])
frame[4:8] = bytes([0, 255, 0, 0])
frame[STRIDE:STRIDE + 4] = bytes([255, 0, 0, 0])


class FakeGrab:
    def frame(self, path, crop=None, stream=None):
        data, w, h, stride = bytes(frame), W, H, STRIDE
        cx = cy = 0
        if crop is not None:
            data, w, h, stride, cx, cy = pi.crop_raw(data, W, H, STRIDE, crop, stream)
        with open(path, "wb") as f:
            f.write(data)
        if crop is not None:
            return w, h, stride, "BGRA", cx, cy, W, H
        return w, h, stride, "BGRA"

    def stop(self):
        pass


class FakePortal:
    streams = [{"node": 1, "x": 100, "y": 0, "w": 2, "h": 1}]   # HiDPI: 2x


with tempfile.TemporaryDirectory() as tmp:
    d = pi.Daemon.__new__(pi.Daemon)
    d.p, d.last, d.grab = FakePortal(), (0.0, 0.0), FakeGrab()
    raw = os.path.join(tmp, "f.raw")
    reply = d.handle("FRAME " + json.dumps(raw))
    check("FRAME yanıtı boyut + adım + mod + ekran", reply == f"OK {W} {H} {STRIDE} BGRA 100 0 2 1", reply)
    try:
        d.handle("FRAME " + json.dumps("göreli.raw"))
        check("göreli yol reddedilir", False)
    except ValueError:
        check("göreli yol reddedilir", True)

    with mock.patch.object(pi, "RUNTIME", tmp), \
            mock.patch.object(pi, "running", lambda: True), \
            mock.patch.object(pi, "send", lambda line: d.handle(line)):
        img, origin, scale = pi.frame_image()
    px = [img.getpixel((0, 0)), img.getpixel((1, 0)), img.getpixel((0, 1))]
    check("renkler doğru (BGRx → RGB, dolgu atlanır)",
          px == [(255, 0, 0), (0, 255, 0), (0, 0, 255)] and img.size == (W, H), str(px))
    check("ekran orijini ve HiDPI ölçeği", origin == (100, 0) and scale == 2.0, f"{origin} {scale}")
    check("ham dosya silinir", not os.path.exists(os.path.join(tmp, "nexremote-frame.raw")))

    with mock.patch.object(pi, "running", lambda: False), \
            mock.patch.object(pi, "_load_token", lambda: None), \
            mock.patch.object(pi, "ensure", lambda **k: (_ for _ in ()).throw(AssertionError)):
        try:
            pi.frame_image()
            check("servis+izin yoksa diyalog açmaz", False)
        except RuntimeError:
            check("servis+izin yoksa diyalog açmaz", True)

# ------------------------------------------------- servis tarafında kırpma
with tempfile.TemporaryDirectory() as tmp:
    d = pi.Daemon.__new__(pi.Daemon)
    d.p, d.last, d.grab = FakePortal(), (0.0, 0.0), FakeGrab()
    raw = os.path.join(tmp, "f.raw")
    # mantıksal (100.5, 0) 0.5x1 → yayın pikseli: x 1, y 0..2 (HiDPI 2x)
    reply = d.handle("FRAME " + json.dumps(raw) + " 100.5 0 0.5 1")
    check("FRAME kırpma: yalnızca dikdörtgen yazılır, yanıtta kırpma orijini + tam boyut",
          reply == "OK 1 2 4 BGRA 100 0 2 1 1 0 4 2", reply)
    check("kırpılmış dosya yalnızca pencerenin baytları", os.path.getsize(raw) == 8)
    with mock.patch.object(pi, "RUNTIME", tmp), \
            mock.patch.object(pi, "send", lambda line: d.handle(line)):
        img, origin, scale = pi.frame_image(crop=(100.5, 0, 0.5, 1))
    check("kırpılmış kare: piksel + orijin + ölçek",
          img.size == (1, 2) and img.getpixel((0, 0)) == (0, 255, 0) and origin == (100, 0)
          and scale == 2.0, f"{img.size} {img.getpixel((0, 0))} {origin} {scale}")
    try:
        pi.crop_raw(bytes(frame), W, H, STRIDE, (500, 500, 10, 10), FakePortal.streams[0])
        check("ekran dışı kırpma → ValueError", False)
    except ValueError:
        check("ekran dışı kırpma → ValueError", True)
    # eski servis kırpmayı bilmez: 8 alanlı yanıt → tam kare, çağıran kırpar
    old = lambda line: d.handle(line.split(" 100")[0]) if " 100" in line else d.handle(line)  # noqa: E731
    with mock.patch.object(pi, "RUNTIME", tmp), mock.patch.object(pi, "send", old):
        img, origin, scale = pi.frame_image(crop=(100.5, 0, 0.5, 1))
    check("eski servis: tam kare döner (geriye uyum)", img.size == (W, H) and origin == (100, 0))

ok = sum(1 for _, o in results if o)
print(f"KARE: {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
