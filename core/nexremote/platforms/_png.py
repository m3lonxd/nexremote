"""Bağımlılıksız PNG yazıcı (zlib + struct) — Pillow olmayan sistemler için."""
from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path: str, width: int, height: int, rgb: bytes) -> None:
    """8 bit RGB (satır satır, dolgu yok) baytlarını PNG olarak yaz."""
    if width <= 0 or height <= 0:
        raise ValueError(f"geçersiz görüntü boyutu: {width}x{height}")
    stride = width * 3
    if len(rgb) != stride * height:
        raise ValueError(f"piksel verisi {len(rgb)} bayt, beklenen {stride * height}")
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(_chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(_chunk(b"IEND", b""))


def bgrx_to_rgb(data: bytes) -> bytes:
    """32 bit BGRX/BGRA (X11 ZPixmap, Windows DIB) → 24 bit RGB."""
    out = bytearray(len(data) // 4 * 3)
    out[0::3] = data[2::4]
    out[1::3] = data[1::4]
    out[2::3] = data[0::4]
    return bytes(out)


def png_size(path: str) -> tuple[int, int]:
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return (0, 0)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    return (0, 0)
