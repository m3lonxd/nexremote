"""Uygulamayı erişilebilirliği açık başlatma.

Electron/Chromium uygulamaları (Discord, Vesktop, VS Code, Slack, Chrome)
erişilebilirlik ağacını ancak `--force-renderer-accessibility` ile
başlatılırsa yayınlar; çalışırken açmanın yolu yok (ne ScreenReaderEnabled
ne ACCESSIBILITY_ENABLED canlı etki eder — sandbox'ta denendi). Bu modül
uygulamayı doğru bayrak + ortamla başlatır ve açılan pencereyi bulur.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Callable, Optional

ELECTRON_FLAG = "--force-renderer-accessibility"

# Chromium tabanlı paketin yanında duran dosyalar — biri yeterli.
_CHROMIUM_MARKERS = ("resources.pak", "chrome_100_percent.pak", "v8_context_snapshot.bin",
                     "snapshot_blob.bin", os.path.join("resources", "app.asar"))

# Sarmalayıcı betikle gelenler (/usr/bin/code bir kabuk betiği) yalnızca adla tanınır.
_CHROMIUM_NAMES = {"electron", "code", "code-oss", "codium", "cursor", "discord", "vesktop",
                   "slack", "obsidian", "signal-desktop", "spotify", "teams-for-linux",
                   "chrome", "google-chrome", "google-chrome-stable", "chromium",
                   "chromium-browser", "brave", "brave-browser", "microsoft-edge",
                   "vivaldi", "opera", "element-desktop", "notion-app", "figma-linux"}

# Qt/GTK uygulamalarını da ağaç yayınlamaya zorlar; Chromium'a zararı yok.
_A11Y_ENV = {"QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1", "QT_ACCESSIBILITY": "1",
             "GTK_A11Y": "atspi", "ACCESSIBILITY_ENABLED": "1"}


def resolve(program: str) -> Optional[str]:
    """Komut adı veya yol → çalıştırılabilir dosyanın gerçek yolu."""
    path = program if os.sep in program else shutil.which(program)
    return os.path.realpath(path) if path and os.path.exists(path) else None


def is_chromium(exe: Optional[str]) -> bool:
    """Bu çalıştırılabilir Electron/Chromium ailesinden mi?"""
    if not exe:
        return False
    name = os.path.basename(exe).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name in _CHROMIUM_NAMES:
        return True
    d = os.path.dirname(exe)
    return any(os.path.exists(os.path.join(d, m)) for m in _CHROMIUM_MARKERS)


def pid_exe(pid: Optional[int]) -> Optional[str]:
    """Çalışan sürecin çalıştırılabilir yolu (Linux /proc; diğerlerinde None)."""
    if not pid:
        return None
    try:
        return os.path.realpath(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        return None


def build_argv(argv: list[str]) -> tuple[list[str], bool]:
    """Bayrak eklenmiş argv ve Chromium ailesi olup olmadığı."""
    if not argv:
        raise ValueError("başlatılacak program verilmedi")
    exe = resolve(argv[0])
    if exe is None:
        raise FileNotFoundError(f"program bulunamadı: {argv[0]}")
    chromium = is_chromium(exe) or os.path.basename(argv[0]).lower() in _CHROMIUM_NAMES
    if chromium and ELECTRON_FLAG not in argv:
        argv = [argv[0], ELECTRON_FLAG, *argv[1:]]
    return argv, chromium


def launch(argv: list[str], list_windows: Callable[[], list], wait: float = 15.0) -> dict:
    """Programı erişilebilirlik açık başlat, yeni penceresini bekle.

    `list_windows` facade.windows — öncesi/sonrası farkından yeni pencere
    bulunur. Tek örnekli uygulama zaten açıksa yeni süreç isteği eskisine
    devredip çıkar; bayrak etkisiz kalır ve bu durum açıkça raporlanır."""
    argv, chromium = build_argv(argv)
    try:
        before = {w.ref for w in list_windows()}
    except Exception:
        before = set()
    env = {**os.environ, **_A11Y_ENV}
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL, "env": env}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(argv, **kw)

    result = {"pid": proc.pid, "argv": argv, "electron": chromium, "window": None,
              "title": None, "note": ""}
    start = time.monotonic()
    deadline = start + max(wait, 0)
    while time.monotonic() < deadline:
        time.sleep(0.4)
        try:
            new = [w for w in list_windows() if w.ref not in before]
        except Exception:
            new = []
        if new:
            mine = [w for w in new if w.pid == proc.pid] or new
            result["window"], result["title"] = mine[0].ref, mine[0].title
            return result
        code = proc.poll()
        if code is not None and time.monotonic() - start > 3:
            # Süreç kapandı, 3 sn içinde pencere de gelmedi: tek örnek devri.
            result["note"] = (
                f"süreç {code} koduyla çıktı ve yeni pencere açılmadı — uygulama büyük "
                "olasılıkla zaten açık ve isteği eski örneğe devretti, bayrak etkisiz "
                "kaldı. Önce uygulamayı tamamen kapatın (tepsideki simge dahil), "
                "sonra yeniden `launch` edin.")
            return result
    result["note"] = f"{wait:.0f} sn içinde yeni pencere görünmedi (süreç çalışıyor)"
    return result


def electron_hint(name: str, exe: Optional[str] = None) -> str:
    prog = os.path.basename(exe) if exe else "<program>"
    return (f"'{name}' bir Electron/Chromium uygulaması ve ağacını yayınlamıyor. "
            f"Kapatıp `nexremote launch {prog}` ile yeniden başlatın "
            f"({ELECTRON_FLAG} eklenir); o zamana kadar ekran görüntüsü + "
            "koordinat tıklaması kullanın.")
