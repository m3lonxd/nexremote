"""Zamanlama izi — `NEXREMOTE_TRACE=1` ile her adımın süresi stderr'e yazılır.

    [trace]   ağaç okuma 182.4 ms düğüm=412 kip=hafif
    [trace]   imleç 0.6 ms
    [trace] click 201.3 ms

Kapalıyken maliyeti bir ortam değişkeni okumasıdır. Servis kipinde satırlar
isteğin çıktısına eklenir (istemcinin stderr'ine gider); `sink()` bunu sağlar.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

T0 = time.perf_counter()           # modülün yüklendiği an ≈ süreç başlangıcı
_local = threading.local()


def enabled() -> bool:
    forced = getattr(_local, "forced", None)
    if forced is not None:
        return forced
    return os.environ.get("NEXREMOTE_TRACE", "") not in ("", "0")


def _emit(line: str) -> None:
    out: Optional[list] = getattr(_local, "sink", None)
    if out is not None:
        out.append(line)
    else:
        sys.stderr.write(f"[trace] {line}\n")
        sys.stderr.flush()


@contextmanager
def span(name: str, **info) -> Iterator[dict]:
    """Bloğun süresini yaz. Dönen sözlüğe eklenen anahtarlar satıra yazılır
    (ör. düğüm sayısı blok içinde öğrenilir)."""
    if not enabled():
        yield info
        return
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    t = time.perf_counter()
    try:
        yield info
    finally:
        _local.depth = depth
        ms = (time.perf_counter() - t) * 1000
        extra = " ".join(f"{k}={v}" for k, v in info.items() if v is not None)
        _emit(f"{'  ' * depth}{name} {ms:.1f} ms {extra}".rstrip())


def mark(name: str, **info) -> None:
    """Süreç başlangıcından bu yana geçen süreyi yaz."""
    if enabled():
        extra = " ".join(f"{k}={v}" for k, v in info.items())
        _emit(f"{name} @{(time.perf_counter() - T0) * 1000:.1f} ms {extra}".rstrip())


@contextmanager
def sink(on: bool) -> Iterator[list]:
    """Bu iş parçacığındaki iz satırlarını bir listeye topla (servis kipi)."""
    lines: list = []
    old_sink, old_forced = getattr(_local, "sink", None), getattr(_local, "forced", None)
    _local.sink, _local.forced = lines, on
    try:
        yield lines
    finally:
        _local.sink, _local.forced = old_sink, old_forced
