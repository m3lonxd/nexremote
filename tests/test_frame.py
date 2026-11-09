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
    def frame(self, path):
        with open(path, "wb") as f:
            f.write(frame)
        return W, H, STRIDE, "BGRA"

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

ok = sum(1 for _, o in results if o)
print(f"KARE: {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
