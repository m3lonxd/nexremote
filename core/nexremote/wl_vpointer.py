"""wlroots sanal fare (zwlr_virtual_pointer_v1) — bağımlılıksız Wayland istemcisi.

Sway'in `seat cursor press button4/5` komutu 1.10'da kaydırma olayı üretmiyor
(WAYLAND_DEBUG'da axis yok — başsız Sway'de doğrulandı). wlroots tabanlı
bileşimciler (Sway, Hyprland, river, labwc…) sanal fare protokolünü sunar;
bu modül tel protokolünü elle konuşur, pywayland/wlrctl gerekmez.
Olaylar seat imlecinin bulunduğu yüzeye gider.
"""
from __future__ import annotations

import os
import socket
import struct
import time

IFACE = "zwlr_virtual_pointer_manager_v1"

# zwlr_virtual_pointer_v1 istek numaraları
_BUTTON, _AXIS, _FRAME, _AXIS_SOURCE, _AXIS_DISCRETE = 2, 3, 4, 5, 7
_WHEEL = 0
_STEP = 15.0  # bir tekerlek çentiği (libinput/wlroots geleneği)


def _msg(obj: int, op: int, payload: bytes = b"") -> bytes:
    return struct.pack("<II", obj, (8 + len(payload)) << 16 | op) + payload


def _string(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + b + b"\0" * (-len(b) % 4)


def _fixed(v: float) -> bytes:
    return struct.pack("<i", int(round(v * 256)))


class VirtualPointer:
    """Bağlantı açık kaldıkça bileşimcide bir fare aygıtı olarak yaşar."""

    def __init__(self, display: str | None = None, timeout: float = 5.0):
        name = display or os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        path = name if os.path.isabs(name) else os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), name)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        try:
            self._sock.connect(path)
            self._next = 2
            registry = self._new_id()
            self._send(_msg(1, 1, struct.pack("<I", registry)))       # wl_display.get_registry
            found = None
            for obj, op, body in self._roundtrip():
                if obj == registry and op == 0:                         # wl_registry.global
                    gname, slen = struct.unpack_from("<II", body)
                    if body[8:8 + slen - 1].decode() == IFACE:
                        found = gname
            if found is None:
                raise RuntimeError(f"bileşimci {IFACE} sunmuyor (wlroots tabanlı değil)")
            manager = self._new_id()
            self._send(_msg(registry, 0, struct.pack("<I", found) + _string(IFACE)
                            + struct.pack("<II", 1, manager)))
            self._ptr = self._new_id()
            self._send(_msg(manager, 0, struct.pack("<II", 0, self._ptr)))  # seat=null → varsayılan
            for _ in self._roundtrip():
                pass
        except BaseException:
            self._sock.close()
            raise

    def _new_id(self) -> int:
        self._next += 1
        return self._next - 1

    def _send(self, data: bytes) -> None:
        self._sock.sendall(data)

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("bileşimci bağlantıyı kapattı")
            buf += chunk
        return buf

    def _roundtrip(self):
        """wl_display.sync → done gelene dek olayları üret; protokol hatasında istisna."""
        cb = self._new_id()
        self._send(_msg(1, 0, struct.pack("<I", cb)))
        while True:
            obj, word = struct.unpack("<II", self._recv(8))
            op, size = word & 0xFFFF, word >> 16
            body = self._recv(size - 8)
            if obj == cb:
                return
            if obj == 1 and op == 0:                                    # wl_display.error
                code = struct.unpack_from("<I", body, 4)[0]
                raise RuntimeError(f"Wayland protokol hatası (kod {code})")
            yield obj, op, body

    @staticmethod
    def _now() -> bytes:
        return struct.pack("<I", int(time.monotonic() * 1000) & 0xFFFFFFFF)

    def scroll(self, dy: int, dx: int = 0) -> None:
        """Tekerlek çentikleri: +dy aşağı, +dx sağa."""
        out = b""
        for axis, steps in ((0, int(dy)), (1, int(dx))):
            for _ in range(min(abs(steps), 50)):
                sign = 1 if steps > 0 else -1
                out += _msg(self._ptr, _AXIS_SOURCE, struct.pack("<I", _WHEEL))
                out += _msg(self._ptr, _AXIS_DISCRETE, self._now() + struct.pack("<I", axis)
                            + _fixed(sign * _STEP) + struct.pack("<i", sign))
                out += _msg(self._ptr, _FRAME)
        if out:
            self._send(out)
            for _ in self._roundtrip():
                pass

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> "VirtualPointer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def scroll(dy: int, dx: int = 0) -> None:
    with VirtualPointer() as vp:
        vp.scroll(dy, dx)
