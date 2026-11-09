"""Girdi kirası (interaction lease): makine genelinde tek yazar.

agent-desktop'tan uyarlanan kavram: birden çok ajan aynı masaüstünde çalışırken
sentetik fare/klavye olayları birbirinin arasına girmesin. Kira yalnızca
gönderim (dispatch) süresince tutulur, öğenin hazır olmasını beklerken değil —
yoksa makinedeki her çağıran sıraya girerdi.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from .platforms.base import runtime_dir

DEFAULT_TIMEOUT = 15.0
_local = threading.local()          # aynı süreçte iç içe çağrı kilitlenmesin


class LeaseTimeout(RuntimeError):
    code = "LEASE_TIMEOUT"


def _path() -> str:
    base = os.environ.get("NEXREMOTE_HOME") or runtime_dir()
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "nexremote-input.lock")


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def input_lease(timeout: float | None = None) -> Iterator[None]:
    """Fiziksel girdi gönderirken tutulan kilit. Başka süreç tutuyorsa
    `timeout` saniye beklenir, sonra LeaseTimeout."""
    if getattr(_local, "depth", 0):
        _local.depth += 1
        try:
            yield
        finally:
            _local.depth -= 1
        return
    if timeout is None:
        timeout = float(os.environ.get("NEXREMOTE_LEASE_TIMEOUT", DEFAULT_TIMEOUT))
    fd = os.open(_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LeaseTimeout(f"başka bir ajan {timeout:g} sn'dir fare/klavyeyi "
                                   "kullanıyor — sonra yeniden deneyin")
            time.sleep(0.05)
        _local.depth = 1
        try:
            yield
        finally:
            _local.depth = 0
            _unlock(fd)
    finally:
        os.close(fd)
