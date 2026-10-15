#!/usr/bin/env python3
"""Wayland fare + klavye — xdg-desktop-portal RemoteDesktop üzerinden.

Neden portal: KWin `zwp_virtual_keyboard` sunmaz (wtype çalışmaz), /dev/uinput
root ister (ydotool çalışmaz). RemoteDesktop portalı ise kök yetkisi istemeden
gerçek fare/klavye olayı enjekte eder; kullanıcı bunu BİR KEZ onaylar, verilen
restore_token sonraki oturumlarda diyaloğu atlar.

Portal oturumu D-Bus bağlantısı kapanınca ölür; bu yüzden uzun ömürlü bir
daemon oturumu tutar, istemciler Unix soket üzerinden komut gönderir.

Soket protokolü (satır başına bir komut, yanıt tek satır "OK ..."/"ERR ..."):
    PING
    STATUS
    MOVE x y                 mutlak masaüstü koordinatı (mantıksal piksel)
    CLICK x y [btn] [n]      btn: left|right|middle, n: tık sayısı
    DOWN btn | UP btn
    SCROLL dy [dx]           tekerlek adımı (+aşağı / +sağ)
    KEY combo                ör. ctrl+shift+t, Return, alt+F4
    TYPE "<json-metin>"      JSON dizesi (çok satırlı metin güvenle taşınır)
    QUIT
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from typing import Optional

RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/nexremote-{os.getuid()}"
SOCK = os.environ.get("NEXREMOTE_INPUT_SOCK", os.path.join(RUNTIME, "nexremote-input.sock"))
STATE_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                         "nexremote")
TOKEN_FILE = os.path.join(STATE_DIR, "portal.json")

# linux/input-event-codes.h
BTN = {"left": 0x110, "right": 0x111, "middle": 0x112}

_MOD_KEYSYM = {
    "ctrl": 0xffe3, "control": 0xffe3, "shift": 0xffe1, "alt": 0xffe9,
    "super": 0xffeb, "meta": 0xffeb, "win": 0xffeb, "cmd": 0xffeb,
    "altgr": 0xfe03,
}
_NAMED_KEYSYM = {
    "return": 0xff0d, "enter": 0xff0d, "tab": 0xff09, "escape": 0xff1b,
    "esc": 0xff1b, "backspace": 0xff08, "delete": 0xffff, "del": 0xffff,
    "insert": 0xff63, "home": 0xff50, "end": 0xff57, "pageup": 0xff55,
    "pgup": 0xff55, "prior": 0xff55, "pagedown": 0xff56, "pgdn": 0xff56,
    "next": 0xff56, "left": 0xff51, "up": 0xff52, "right": 0xff53,
    "down": 0xff54, "space": 0x20, "menu": 0xff67, "print": 0xff61,
    **{f"f{i}": 0xffbe + i - 1 for i in range(1, 25)},
}


def char_keysym(ch: str) -> int:
    """Tek karakter → X keysym (Latin-1 doğrudan, gerisi Unicode keysym)."""
    cp = ord(ch)
    if ch == "\n":
        return 0xff0d
    if ch == "\t":
        return 0xff09
    if 0x20 <= cp <= 0x7e or 0xa0 <= cp <= 0xff:
        return cp
    return 0x01000000 | cp


def parse_combo(combo: str) -> tuple[list[int], int]:
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError("boş tuş kombinasyonu")
    *mods, main = parts
    mod_syms = []
    for m in mods:
        if m.lower() not in _MOD_KEYSYM:
            raise ValueError(f"bilinmeyen değiştirici: {m}")
        mod_syms.append(_MOD_KEYSYM[m.lower()])
    low = main.lower()
    if low in _NAMED_KEYSYM:
        key = _NAMED_KEYSYM[low]
    elif low in _MOD_KEYSYM:
        key = _MOD_KEYSYM[low]
    elif len(main) == 1:
        # kombinasyonda harfler küçük gönderilir; shift ayrıca belirtilir
        key = char_keysym(main.lower() if mods else main)
    else:
        raise ValueError(f"bilinmeyen tuş: {main}")
    return mod_syms, key


# =============================================================== daemon
class PortalSession:
    """RemoteDesktop + ScreenCast oturumu (mutlak koordinat için yayın gerekir)."""

    def __init__(self) -> None:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        DBusGMainLoop(set_as_default=True)
        self.dbus = dbus
        self.bus = dbus.SessionBus()
        obj = self.bus.get_object("org.freedesktop.portal.Desktop",
                                  "/org/freedesktop/portal/desktop")
        self.rd = dbus.Interface(obj, "org.freedesktop.portal.RemoteDesktop")
        self.sc = dbus.Interface(obj, "org.freedesktop.portal.ScreenCast")
        self.props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        self.session: Optional[str] = None
        self.streams: list[dict] = []   # {node, x, y, w, h}
        self.devices = 0                # portalın verdiği cihaz maskesi (1 klavye, 2 fare)
        self._counter = 0
        self._sender = self.bus.get_unique_name()[1:].replace(".", "_")

    # --------------------------------------------------- Request yardımcı
    def _request(self, call, *args, options: dict, timeout: float = 120.0) -> dict:
        """Portal çağrısını yap ve Request.Response sinyalini bekle."""
        from gi.repository import GLib
        self._counter += 1
        token = f"nexr{os.getpid()}_{self._counter}"
        path = f"/org/freedesktop/portal/desktop/request/{self._sender}/{token}"
        box: dict = {}
        loop = GLib.MainLoop()

        def on_response(code, results):
            box["code"] = int(code)
            box["results"] = results
            loop.quit()

        match = self.bus.add_signal_receiver(
            on_response, "Response", "org.freedesktop.portal.Request",
            "org.freedesktop.portal.Desktop", path)
        options = dict(options)
        options["handle_token"] = token
        try:
            call(*args, options)
            GLib.timeout_add(int(timeout * 1000), loop.quit)
            loop.run()
        finally:
            match.remove()
        if "code" not in box:
            raise TimeoutError("portal yanıt vermedi (izin diyaloğu açık kalmış olabilir)")
        if box["code"] == 1:
            raise PermissionError("kullanıcı uzaktan kontrol iznini reddetti")
        if box["code"] != 0:
            raise RuntimeError(f"portal hatası (kod {box['code']})")
        return box["results"]

    def start(self) -> None:
        dbus = self.dbus
        version = int(self.props.Get("org.freedesktop.portal.RemoteDesktop", "version"))
        res = self._request(self.rd.CreateSession, options={
            "session_handle_token": dbus.String(f"nexrs{os.getpid()}")})
        self.session = str(res["session_handle"])

        dev_opts = {"types": dbus.UInt32(1 | 2)}          # klavye | fare
        if version >= 2:
            dev_opts["persist_mode"] = dbus.UInt32(2)       # iptal edilene kadar
            token = _load_token()
            if token:
                dev_opts["restore_token"] = dbus.String(token)
        self._request(self.rd.SelectDevices, dbus.ObjectPath(self.session),
                      options=dev_opts)
        # Mutlak hareket bir yayın (stream) koordinat sistemine bağlıdır.
        self._request(self.sc.SelectSources, dbus.ObjectPath(self.session), options={
            "types": dbus.UInt32(1), "multiple": dbus.Boolean(True)})
        res = self._request(self.rd.Start, dbus.ObjectPath(self.session), "",
                            options={}, timeout=600.0)
        self.devices = int(res.get("devices", 0))
        if res.get("restore_token"):
            _save_token(str(res["restore_token"]))
        self.streams = []
        for node, props in res.get("streams", []):
            pos = props.get("position", (0, 0))
            size = props.get("size", (0, 0))
            self.streams.append({"node": int(node), "x": int(pos[0]), "y": int(pos[1]),
                                 "w": int(size[0]), "h": int(size[1])})
        if not self.streams:
            raise RuntimeError("portal ekran yayını vermedi; mutlak tıklama yapılamaz")

    # --------------------------------------------------------- olaylar
    def _stream_for(self, x: float, y: float) -> tuple[dict, float, float]:
        for s in self.streams:
            if s["x"] <= x < s["x"] + s["w"] and s["y"] <= y < s["y"] + s["h"]:
                return s, x - s["x"], y - s["y"]
        raise ValueError(f"({x:.0f},{y:.0f}) hiçbir ekranın içinde değil; "
                         f"ekranlar: {self.screens_text()}")

    def screens_text(self) -> str:
        return ", ".join(f"{s['w']}x{s['h']}+{s['x']}+{s['y']}" for s in self.streams)

    def move(self, x: float, y: float) -> None:
        s, lx, ly = self._stream_for(x, y)
        self.rd.NotifyPointerMotionAbsolute(self.dbus.ObjectPath(self.session), {},
                                            self.dbus.UInt32(s["node"]),
                                            float(lx), float(ly))

    def button(self, btn: str, pressed: bool) -> None:
        if btn not in BTN:
            raise ValueError(f"bilinmeyen fare tuşu: {btn}")
        self.rd.NotifyPointerButton(self.dbus.ObjectPath(self.session), {},
                                    self.dbus.Int32(BTN[btn]),
                                    self.dbus.UInt32(1 if pressed else 0))

    def scroll(self, dy: int, dx: int = 0) -> None:
        # xdp-kde "adım" değerini eksen değeri (px) olarak aktarıyor; gerçek
        # bir tekerlek çentiği libinput'ta 15 px'tir (sandbox ölçümü: 360 → GTK
        # delta 36.0, yani delta = değer/10).
        sess = self.dbus.ObjectPath(self.session)
        if dy:
            self.rd.NotifyPointerAxisDiscrete(sess, {}, self.dbus.UInt32(0),
                                              self.dbus.Int32(int(dy) * 15))
        if dx:
            self.rd.NotifyPointerAxisDiscrete(sess, {}, self.dbus.UInt32(1),
                                              self.dbus.Int32(int(dx) * 15))

    def keysym(self, sym: int, pressed: bool) -> None:
        self.rd.NotifyKeyboardKeysym(self.dbus.ObjectPath(self.session), {},
                                     self.dbus.Int32(sym),
                                     self.dbus.UInt32(1 if pressed else 0))

    def close(self) -> None:
        if not self.session:
            return
        try:
            obj = self.bus.get_object("org.freedesktop.portal.Desktop", self.session)
            self.dbus.Interface(obj, "org.freedesktop.portal.Session").Close()
        except Exception:
            pass
        self.session = None


def _load_token() -> Optional[str]:
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f).get("restore_token")
    except (OSError, ValueError):
        return None


def _save_token(token: str) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"restore_token": token, "saved": time.time()}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_FILE)


class Daemon:
    # olaylar arası kısa bekleme: bazı uygulamalar aynı karede gelen bas/bırak'ı yutar
    GAP = 0.012

    def __init__(self, portal: PortalSession) -> None:
        self.p = portal
        self.last = (0.0, 0.0)

    def handle(self, line: str) -> str:
        cmd, _, rest = line.partition(" ")
        cmd = cmd.upper()
        args = rest.split()
        p = self.p
        if cmd == "PING":
            return "OK pong"
        if cmd == "STATUS":
            return "OK " + json.dumps({"screens": p.streams, "pointer": self.last,
                                       "keyboard_granted": bool(p.devices & 1),
                                       "pointer_granted": bool(p.devices & 2)})
        if cmd == "MOVE":
            x, y = float(args[0]), float(args[1])
            p.move(x, y)
            self.last = (x, y)
            return "OK"
        if cmd == "CLICK":
            x, y = float(args[0]), float(args[1])
            btn = args[2] if len(args) > 2 else "left"
            n = int(args[3]) if len(args) > 3 else 1
            p.move(x, y)
            self.last = (x, y)
            time.sleep(self.GAP * 3)
            for _ in range(max(1, min(n, 3))):
                p.button(btn, True)
                time.sleep(self.GAP)
                p.button(btn, False)
                time.sleep(self.GAP * 4)
            return "OK"
        if cmd in ("DOWN", "UP"):
            p.button(args[0] if args else "left", cmd == "DOWN")
            return "OK"
        if cmd == "SCROLL":
            p.scroll(int(args[0]), int(args[1]) if len(args) > 1 else 0)
            return "OK"
        if cmd == "KEY":
            mods, key = parse_combo(rest.strip())
            for m in mods:
                p.keysym(m, True)
                time.sleep(self.GAP)
            p.keysym(key, True)
            time.sleep(self.GAP)
            p.keysym(key, False)
            for m in reversed(mods):
                time.sleep(self.GAP)
                p.keysym(m, False)
            return "OK"
        if cmd == "TYPE":
            text = json.loads(rest)
            if not isinstance(text, str):
                raise ValueError("TYPE bir JSON dizesi bekler")
            for ch in text:
                sym = char_keysym(ch)
                p.keysym(sym, True)
                time.sleep(self.GAP)
                p.keysym(sym, False)
                time.sleep(self.GAP)
            return "OK"
        if cmd == "QUIT":
            return "OK bye"
        return f"ERR bilinmeyen komut: {cmd}"


def serve() -> int:
    from gi.repository import GLib
    portal = PortalSession()
    try:
        portal.start()
    except Exception as e:
        print(f"[nexremote-input] portal başlatılamadı: {e}", file=sys.stderr)
        return 2
    daemon = Daemon(portal)

    if os.path.exists(SOCK):
        os.unlink(SOCK)
    os.makedirs(os.path.dirname(SOCK), exist_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o600)
    srv.listen(8)
    loop = GLib.MainLoop()

    def on_conn(_fd, _cond):
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            line = buf.decode("utf-8", "replace").rstrip("\n")
            try:
                reply = daemon.handle(line)
            except Exception as e:
                reply = f"ERR {e}"
            conn.sendall((reply + "\n").encode())
            if line.upper().startswith("QUIT"):
                loop.quit()
        except OSError:
            pass
        finally:
            conn.close()
        return True

    def on_session_closed(*_a):
        print("[nexremote-input] portal oturumu kapatıldı", file=sys.stderr)
        loop.quit()

    portal.bus.add_signal_receiver(on_session_closed, "Closed",
                                   "org.freedesktop.portal.Session",
                                   "org.freedesktop.portal.Desktop", portal.session)
    GLib.io_add_watch(srv.fileno(), GLib.IO_IN, on_conn)
    print(f"[nexremote-input] hazır: {SOCK} · ekranlar: {portal.screens_text()}",
          file=sys.stderr, flush=True)
    try:
        loop.run()
    finally:
        portal.close()
        srv.close()
        try:
            os.unlink(SOCK)
        except OSError:
            pass
    return 0


# =============================================================== istemci
def send(line: str, timeout: float = 15.0) -> str:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCK)
        s.sendall(line.encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.decode("utf-8", "replace").strip()
    finally:
        s.close()


def running() -> bool:
    try:
        return send("PING", timeout=2.0) == "OK pong"
    except OSError:
        return False


def ensure(wait: float = 600.0) -> None:
    """Daemon yoksa başlat; ilk seferde kullanıcı izin diyaloğunu onaylamalı."""
    if running():
        return
    import subprocess
    log = os.path.join(RUNTIME, "nexremote-input.log")
    with open(log, "ab") as lf:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"],
                         stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if running():
            return
        time.sleep(0.2)
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            tail = f.read().strip().splitlines()[-1:]
    except OSError:
        tail = []
    raise RuntimeError("giriş servisi başlamadı" + (f": {tail[0]}" if tail else "")
                       + " — ekrandaki 'Uzaktan kontrol' iznini onayladınız mı?")


def call(line: str) -> str:
    """Komutu gönder; ERR ise istisna fırlat."""
    if "\n" in line:
        raise ValueError("komut tek satır olmalı (TYPE metni JSON ile kodlanır)")
    ensure()
    reply = send(line)
    if not reply.startswith("OK"):
        raise RuntimeError(reply[4:] if reply.startswith("ERR ") else reply or "yanıt yok")
    return reply[3:]


def _keyable(ch: str) -> bool:
    """Klavye düzeninden bağımsız tuşlanabilir mi? (ASCII + Latin-1)

    KWin keysym'i etkin düzende arar; Ğ/Ş/İ/ı, emoji, CJK gibi Latin-1 dışı
    karakterlerin çoğu US düzeninde yoktur ve sessizce düşer. Bunlar panodan
    yapıştırılır.
    """
    cp = ord(ch)
    return ch in "\n\t" or 0x20 <= cp <= 0x7e or 0xa0 <= cp <= 0xff


def _clipboard_get() -> Optional[bytes]:
    import subprocess
    try:
        r = subprocess.run(["wl-paste", "-n"], capture_output=True, timeout=2)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _clipboard_set(data: bytes) -> bool:
    import subprocess
    try:
        return subprocess.run(["wl-copy"], input=data, timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def type_text(text: str, paste_combo: str = "ctrl+v") -> None:
    """Metni yaz. Tuşlanamayan parçalar panodan yapıştırılır (paste_combo;
    terminallerde genelde ctrl+shift+v). Kullanıcının panosu geri yüklenir."""
    runs: list[tuple[bool, str]] = []
    for ch in text:
        k = _keyable(ch)
        if runs and runs[-1][0] == k:
            runs[-1] = (k, runs[-1][1] + ch)
        else:
            runs.append((k, ch))
    saved: Optional[bytes] = None
    touched = False
    try:
        for keyable, chunk in runs:
            if keyable:
                call("TYPE " + json.dumps(chunk, ensure_ascii=False))
                continue
            if not touched:
                saved = _clipboard_get()
                touched = True
            if not _clipboard_set(chunk.encode("utf-8")):
                raise RuntimeError("wl-copy gerekli (Latin-1 dışı karakterler panodan yapıştırılır)")
            call("KEY " + paste_combo)
            time.sleep(0.15)   # hedef uygulama panoyu okusun
    finally:
        if touched and saved is not None:
            _clipboard_set(saved)


def available() -> tuple[bool, str]:
    try:
        import dbus  # noqa: F401
        from gi.repository import GLib  # noqa: F401
    except Exception as e:
        return False, f"python3-dbus + python3-gi gerekli: {e}"
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.path.exists(
            os.path.join(RUNTIME, "bus")):
        return False, "D-Bus oturum veri yolu yok"
    try:
        import dbus
        bus = dbus.SessionBus()
        obj = bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
        v = dbus.Interface(obj, "org.freedesktop.DBus.Properties").Get(
            "org.freedesktop.portal.RemoteDesktop", "version")
    except Exception:
        return False, "xdg-desktop-portal RemoteDesktop arayüzü yok"
    state = "çalışıyor" if running() else ("izin kayıtlı" if _load_token() else "ilk kullanımda onay ister")
    return True, f"RemoteDesktop portal v{int(v)} ({state})"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        sys.exit(serve())
    print(__doc__)
