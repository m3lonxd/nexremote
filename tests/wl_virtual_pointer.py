#!/usr/bin/env python3
"""Başsız wlroots bileşimcisine boş bir sanal fare aygıtı takar ve açık tutar.

    wl_virtual_pointer.py &      # WAYLAND_DISPLAY ortamdan okunur

Başsız Sway'de giriş aygıtı yoktur (seat capabilities=0); istemciler
wl_pointer almaz ve `seat cursor` olayları hiçbir pencereye ulaşmaz. Gerçek
oturumda fare zaten vardır — bu yardımcı yalnızca sandbox içindir.
"""
import os
import signal
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from nexremote.wl_vpointer import VirtualPointer  # noqa: E402

vp = VirtualPointer()
vp._sock.settimeout(None)
print("ok", flush=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    while vp._sock.recv(4096):          # aygıt bağlantı açık kaldıkça yaşar
        pass
except (KeyboardInterrupt, OSError):
    pass
