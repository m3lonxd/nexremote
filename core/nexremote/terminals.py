"""Masaüstünde ZATEN AÇIK olan terminaller — listele, ekranını oku, yaz.

TUI backend'i (tui.py) NexRemote'un kendi açtığı tmux oturumlarını yönetir.
Bu modül ise kullanıcının kendi terminallerini hedefler: Konsole sekmeleri
(D-Bus) ve tmux bölmeleri. Böylece bir ajan, ekranda çalışan başka bir CLI
ajanının (claude, aider, codex...) ne yazdığını okuyup ona girdi verebilir.

Referans biçimi:
    konsole:<pid>:<oturum>     ör. konsole:7891:2
    tmux:<oturum>:<pencere>.<bölme>
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Optional

_KONSOLE_SVC = re.compile(r"^org\.kde\.konsole-(\d+)$")


@dataclass
class Terminal:
    ref: str
    title: str
    program: str          # ön plandaki süreç (ör. claude, vim, bash)
    cwd: str
    kind: str             # konsole | tmux
    pid: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _proc_name(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


def _proc_cwd(pid: int) -> str:
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd


# ------------------------------------------------------------------ Konsole
def _bus():
    from ._dbus import session_bus
    return session_bus()


def _konsole_services(bus) -> list[tuple[str, int]]:
    import dbus
    names = dbus.Interface(bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                           "org.freedesktop.DBus").ListNames()
    out = []
    for n in names:
        m = _KONSOLE_SVC.match(str(n))
        if m:
            out.append((str(n), int(m.group(1))))
    return sorted(out, key=lambda x: x[1])


def _konsole_session_ids(bus, service: str) -> list[int]:
    import dbus
    import xml.etree.ElementTree as ET
    xml = dbus.Interface(bus.get_object(service, "/Sessions"),
                         "org.freedesktop.DBus.Introspectable").Introspect()
    return sorted(int(n.get("name")) for n in ET.fromstring(str(xml)).findall("node")
                  if str(n.get("name", "")).isdigit())


def _konsole_session(bus, service: str, sid: int):
    import dbus
    return dbus.Interface(bus.get_object(service, f"/Sessions/{sid}"),
                          "org.kde.konsole.Session")


def _konsole_list() -> list[Terminal]:
    try:
        bus = _bus()
        services = _konsole_services(bus)
    except Exception:
        return []
    out = []
    for service, kpid in services:
        try:
            sids = _konsole_session_ids(bus, service)
        except Exception:
            continue
        for sid in sids:
            try:
                s = _konsole_session(bus, service, sid)
                fg = int(s.foregroundProcessId())
                shell = int(s.processId())
                title = str(s.title(1)) or str(s.title(0))
            except Exception:
                continue
            out.append(Terminal(ref=f"konsole:{kpid}:{sid}", title=title,
                                program=_proc_name(fg) or _proc_name(shell),
                                cwd=_proc_cwd(fg) or _proc_cwd(shell),
                                kind="konsole", pid=fg or shell))
    return out


# --------------------------------------------------------------------- tmux
def _tmux(*args: str) -> Optional[str]:
    if not shutil.which("tmux"):
        return None
    try:
        r = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _tmux_list() -> list[Terminal]:
    raw = _tmux("list-panes", "-a", "-F",
                "#{session_name}:#{window_index}.#{pane_index}\t#{pane_title}\t"
                "#{pane_current_command}\t#{pane_current_path}\t#{pane_pid}")
    out = []
    for line in (raw or "").splitlines():
        target, title, cmd, path, pid = (line.split("\t") + [""] * 5)[:5]
        home = os.path.expanduser("~")
        out.append(Terminal(ref=f"tmux:{target}", title=title, program=cmd,
                            cwd="~" + path[len(home):] if path.startswith(home) else path,
                            kind="tmux", pid=int(pid) if pid.isdigit() else None))
    return out


# -------------------------------------------------------------------- genel
def list_terminals() -> list[Terminal]:
    return _konsole_list() + _tmux_list()


def _parse(ref: str) -> tuple[str, str]:
    kind, _, rest = ref.partition(":")
    if kind not in ("konsole", "tmux") or not rest:
        raise ValueError(f"geçersiz terminal ref: {ref!r} (konsole:PID:N veya tmux:OTURUM:P.B)")
    return kind, rest


def history_supported(ref: str) -> bool:
    # Konsole D-Bus yalnızca görünen ekranı verir (negatif ofsetler boş döner).
    return _parse(ref)[0] == "tmux"


def read(ref: str, history: int = 0) -> str:
    """Terminalin görünen ekranını metin olarak döndür.

    history > 0 ise (yalnızca tmux) ekranın üstündeki geçmişten o kadar satır
    da eklenir; Konsole'da yok sayılır — bkz. history_supported().
    """
    kind, rest = _parse(ref)
    if kind == "konsole":
        kpid, _, sid = rest.partition(":")
        s = _konsole_session(_bus(), f"org.kde.konsole-{kpid}", int(sid))
        return str(s.getAllDisplayedText(True)).rstrip()
    args = ["capture-pane", "-p", "-J", "-t", rest]
    if history > 0:
        args += ["-S", f"-{int(history)}"]
    text = _tmux(*args)
    if text is None:
        raise RuntimeError(f"tmux bölmesi okunamadı: {rest}")
    return text.rstrip()


# send() içinde tanınan özel tuşlar → terminale giden ham bayt
_KEYS = {
    "enter": "\r", "return": "\r", "tab": "\t", "esc": "\x1b", "escape": "\x1b",
    "backspace": "\x7f", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C",
    "left": "\x1b[D", "home": "\x1b[H", "end": "\x1b[F", "pgup": "\x1b[5~",
    "pgdn": "\x1b[6~", "delete": "\x1b[3~", "space": " ",
}


def key_bytes(name: str) -> str:
    low = name.strip().lower()
    if low in _KEYS:
        return _KEYS[low]
    m = re.fullmatch(r"(?:c-|ctrl\+)([a-z\[\\\]^_])", low)
    if m:
        return chr(ord(m.group(1).upper()) & 0x1f)
    raise ValueError(f"bilinmeyen tuş: {name}")


def send(ref: str, text: str = "", enter: bool = False, keys: Optional[list[str]] = None) -> None:
    """Terminale metin ve/veya tuş gönder (klavyeden yazılmış gibi)."""
    payload = text + "".join(key_bytes(k) for k in (keys or []))
    if enter:
        payload += "\r"
    if not payload:
        raise ValueError("gönderilecek metin veya tuş yok")
    kind, rest = _parse(ref)
    if kind == "konsole":
        kpid, _, sid = rest.partition(":")
        _konsole_session(_bus(), f"org.kde.konsole-{kpid}", int(sid)).sendText(payload)
        return
    if _tmux("send-keys", "-t", rest, "-l", payload) is None:
        raise RuntimeError(f"tmux bölmesine yazılamadı: {rest}")
