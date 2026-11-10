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
    CLICK x y [btn] [n] [bx by]
                             btn: left|right|middle, n: tık sayısı; bx by verilirse
                             tıktan sonra imleç oraya geri konur (tek gidiş-dönüş)
    DOWN btn | UP btn
    SCROLL dy [dx]           tekerlek adımı (+aşağı / +sağ)
    KEY combo                ör. ctrl+shift+t, Return, alt+F4
    TYPE "<json-metin>"      JSON dizesi (çok satırlı metin güvenle taşınır)
    FRAME "<json-yol>" [x y w h]
                             ekran yayınının son karesi → ham bayt dosyası; yanıt
                             "OK w h satır_adımı pil_ham_modu ekran_x ekran_y ekran_w ekran_h
                             [kırpma_x kırpma_y tam_w tam_h]". x y w h (mantıksal piksel)
                             verilirse yalnızca o dikdörtgen yazılır (pencere görüntüsü).
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


def _base_parse(combo: str):
    try:
        from .platforms.base import parse_combo as base
    except ImportError:               # daemon dosya yoluyla çalışır (paket bağlamı yok)
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from nexremote.platforms.base import parse_combo as base
    return base(combo)


def parse_combo(combo: str) -> tuple[list[int], int]:
    """Diğer platformlarla aynı yazım: 'ctrl++', 'cmd+s', 'option+x', 'ins'.
    Kombinasyondaki büyük harf (ctrl+A) Shift demektir — Windows/macOS gibi."""
    mods, main = _base_parse(combo)
    mod_syms = [_MOD_KEYSYM[m] for m in mods]
    low = main.lower()
    if low in _NAMED_KEYSYM:
        key = _NAMED_KEYSYM[low]
    elif low in _MOD_KEYSYM:
        key = _MOD_KEYSYM[low]
    elif len(main) == 1:
        if mods and main.isalpha() and main != main.lower():
            if _MOD_KEYSYM["shift"] not in mod_syms:
                mod_syms.append(_MOD_KEYSYM["shift"])
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
        for st in self.streams:
            st["sx"] = st["sy"] = 1.0   # masaüstü pikseli → yayın pikseli
        if len(self.streams) > 1 and not any("position" in props
                                              for _, props in res.get("streams", [])):
            self._place_streams()

    # ------------------------------------------- çoklu ekran yerleşimi
    def _place_streams(self) -> None:
        """KDE portalı yayın konumu (position) göndermez: hepsi +0+0 görünür ve
        ilk ekran dışındaki ekranlara tıklanamaz. Konumları KWin'in çıkış
        listesinden tamamla. Boyutu tek olan yayın doğrudan eşlenir; aynı boyutta
        birden çok ekran varsa imleç yayının ortasına bir kez götürülüp KWin'in
        bildirdiği konumla hangi ekran olduğu ölçülür, sonra imleç geri konur."""
        try:
            from . import kwin  # type: ignore[attr-defined]
        except ImportError:   # daemon dosya yoluyla çalışır (paket bağlamı yok)
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from nexremote import kwin
        try:
            outs = kwin.screens()
        except Exception as e:
            sys.stderr.write(f"nexremote: ekran yerleşimi okunamadı ({e}); "
                             "yalnızca ilk ekrana tıklanabilir\n")
            return
        free = list(outs)
        pending = []
        for st in self.streams:
            same = [o for o in free if (o["w"], o["h"]) == (st["w"], st["h"])]
            others = [t for t in self.streams if t is not st
                      and (t["w"], t["h"]) == (st["w"], st["h"])]
            if len(same) == 1 and not others:
                self._assign(st, same[0])
                free.remove(same[0])
            else:
                pending.append(st)
        if not pending:
            return
        home = None
        try:
            home = kwin.cursor_pos()
        except Exception:
            pass
        try:
            for st in pending:
                self.rd.NotifyPointerMotionAbsolute(
                    self.dbus.ObjectPath(self.session), {}, self.dbus.UInt32(st["node"]),
                    float(st["w"] // 2), float(st["h"] // 2))
                time.sleep(0.05)
                cx, cy = kwin.cursor_pos()
                hit = next((o for o in free if o["x"] <= cx < o["x"] + o["w"]
                            and o["y"] <= cy < o["y"] + o["h"]), None)
                if hit is not None:
                    self._assign(st, hit)
                    free.remove(hit)
        except Exception as e:
            sys.stderr.write(f"nexremote: ekran kalibrasyonu yarım kaldı ({e})\n")
        finally:
            if home is not None:
                try:
                    self.move(*home)
                except ValueError:
                    pass

    @staticmethod
    def _assign(st: dict, out: dict) -> None:
        # yayın pikseli ölçekli ekranlarda mantıksal pikselden büyük olabilir
        st["sx"] = st["w"] / out["w"] if out["w"] else 1.0
        st["sy"] = st["h"] / out["h"] if out["h"] else 1.0
        st.update(x=out["x"], y=out["y"], w=out["w"], h=out["h"], output=out.get("name", ""))

    # --------------------------------------------------------- olaylar
    def _stream_for(self, x: float, y: float) -> tuple[dict, float, float]:
        for s in self.streams:
            if s["x"] <= x < s["x"] + s["w"] and s["y"] <= y < s["y"] + s["h"]:
                return s, (x - s["x"]) * s.get("sx", 1.0), (y - s["y"]) * s.get("sy", 1.0)
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


class Grabber:
    """Oturumun ekran yayınından (PipeWire) son kareyi bellekte tutar.

    Ekran görüntüsü yeni bir yakalama değil, zaten gelen karenin kopyasıdır.
    Akış ham formatta tutulur (dönüşüm yalnızca SHOT'ta), IDLE sn
    kullanılmazsa durdurulur. KWin kareyi yalnızca ekran değişince yollar;
    appsink son kareyi saklar, durağan ekranda da hazırdır.
    """
    IDLE = 30.0
    FIRST_FRAME = 2.0

    def __init__(self, portal: PortalSession) -> None:
        self.p = portal
        self.pipe = None
        self.sink = None
        self.last_use = 0.0

    @staticmethod
    def available() -> bool:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            return all(Gst.ElementFactory.find(e) for e in ("pipewiresrc", "appsink"))
        except Exception:
            return False

    def _start(self) -> None:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        if len(self.p.streams) != 1:
            raise RuntimeError("çoklu ekranda yayından kare alma desteklenmiyor")
        fd = self.p.sc.OpenPipeWireRemote(self.p.dbus.ObjectPath(self.p.session), {})
        fd = fd.take()
        node = self.p.streams[0]["node"]
        pipe = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={node} do-timestamp=true ! "
            "video/x-raw,format={BGRx,BGRA,RGBx,RGBA,xRGB,ARGB} ! "
            "appsink name=sink max-buffers=1 drop=true sync=false "
            "emit-signals=false enable-last-sample=true")
        self.sink = pipe.get_by_name("sink")
        if pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            pipe.set_state(Gst.State.NULL)
            raise RuntimeError("PipeWire akışı başlatılamadı")
        self.pipe = pipe
        from gi.repository import GLib
        GLib.timeout_add_seconds(5, self._idle_check)

    def _idle_check(self) -> bool:
        if self.pipe is not None and time.monotonic() - self.last_use > self.IDLE:
            self.stop()
            return False
        return self.pipe is not None

    def stop(self) -> None:
        if self.pipe is not None:
            from gi.repository import Gst
            self.pipe.set_state(Gst.State.NULL)
        self.pipe = self.sink = None

    def _error(self) -> Optional[str]:
        from gi.repository import Gst
        msg = self.pipe.get_bus().pop_filtered(Gst.MessageType.ERROR)
        return msg.parse_error()[0].message if msg else None

    def frame(self, path: str, crop=None, stream=None):
        self.last_use = time.monotonic()
        if self.pipe is not None and self._error():
            self.stop()                       # akış koptu (ör. ekran değişti): yeniden kur
        if self.pipe is None:
            self._start()
        sample = self.sink.get_property("last-sample")
        if sample is None:                    # yeni akış: ilk kare birazdan gelir
            # (pipewiresrc kendi iş parçacığında çalışır; ana döngüyü
            # döndürmeye gerek yok — döndürmek soket işleyicisini iç içe çağırırdı)
            end = time.monotonic() + self.FIRST_FRAME
            while sample is None and time.monotonic() < end:
                err = self._error()
                if err:
                    self.stop()
                    raise RuntimeError(f"PipeWire: {err}")
                time.sleep(0.005)
                sample = self.sink.get_property("last-sample")
        if sample is None:
            raise RuntimeError("yayından kare gelmedi")
        return self._save(sample, path, crop, stream)

    @staticmethod
    def _save(sample, path: str, crop=None, stream=None):
        """Kareyi olduğu gibi (ham bayt) yaz: PNG kodlaması istemcide, kırpma ve
        küçültmeden SONRA bir kez yapılır. Dönüş: genişlik, yükseklik, satır
        adımı, PIL ham modu."""
        from gi.repository import Gst
        st = sample.get_caps().get_structure(0)
        w, h = st.get_value("width"), st.get_value("height")
        fmt = st.get_value("format")   # ör. BGRx
        # x (dolgu) baytı alfa gibi okunur, RGB'ye çevirirken atılır
        raw = {"BGRx": "BGRA", "BGRA": "BGRA", "RGBx": "RGBA", "RGBA": "RGBA",
               "xRGB": "ARGB", "ARGB": "ARGB"}[fmt]
        buf = sample.get_buffer()
        ok, m = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError("kare belleği okunamadı")
        try:
            data = bytes(m.data)
        finally:
            buf.unmap(m)
        # 4 bayt/piksel paketli format: satır adımı = boyut / yükseklik (dolgu dahil)
        stride = len(data) // h
        if stride < w * 4:
            raise RuntimeError(f"beklenmeyen kare boyutu: {len(data)} bayt, {w}x{h}")
        full = (w, h)
        if crop is not None:
            data, w, h, stride, cx, cy = crop_raw(data, w, h, stride, crop, stream)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        if crop is not None:
            return w, h, stride, raw, cx, cy, full[0], full[1]
        return w, h, stride, raw


def crop_raw(data: bytes, w: int, h: int, stride: int, crop, stream):
    """Ham kareden (4 bayt/piksel) mantıksal dikdörtgeni kes; yalnızca pencerenin
    baytları diske yazılır. Dönüş: (veri, w, h, satır_adımı, kırpma_x, kırpma_y)."""
    sx = w / stream["w"] if stream.get("w") else 1.0
    sy = h / stream["h"] if stream.get("h") else 1.0
    x, y, cw, ch = crop
    x0 = max(0, min(w, round((x - stream["x"]) * sx)))
    y0 = max(0, min(h, round((y - stream["y"]) * sy)))
    x1 = max(0, min(w, round((x + cw - stream["x"]) * sx)))
    y1 = max(0, min(h, round((y + ch - stream["y"]) * sy)))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("kırpma dikdörtgeni ekranın dışında")
    row = (x1 - x0) * 4
    out = b"".join(data[r * stride + x0 * 4:r * stride + x0 * 4 + row] for r in range(y0, y1))
    return out, x1 - x0, y1 - y0, row, x0, y0


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
        self.grab = Grabber(portal)

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
            n = max(1, min(int(args[3]) if len(args) > 3 else 1, 3))
            p.move(x, y)
            self.last = (x, y)
            time.sleep(self.GAP * 3)
            for i in range(n):
                p.button(btn, True)
                time.sleep(self.GAP)
                p.button(btn, False)
                if i + 1 < n:
                    time.sleep(self.GAP * 4)   # yalnızca tıklar arasında
            if len(args) > 5:                  # kullanıcının imlecini geri koy
                bx, by = float(args[4]), float(args[5])
                time.sleep(self.GAP)
                try:
                    p.move(bx, by)
                    self.last = (bx, by)
                except ValueError:
                    pass                       # eski konum artık hiçbir ekranda değil
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
        if cmd == "FRAME":
            path, end = json.JSONDecoder().raw_decode(rest.strip())
            tail = rest.strip()[end:].strip()
            if not isinstance(path, str) or not os.path.isabs(path):
                raise ValueError("FRAME mutlak yol (JSON dizesi) bekler")
            crop = [float(v) for v in tail.split()] if tail else None
            if crop is not None and len(crop) != 4:
                raise ValueError("FRAME kırpma: x y w h")
            s = p.streams[0]   # Grabber tek ekranı destekler
            if crop is None:
                w, h, stride, raw = self.grab.frame(path)
                return f"OK {w} {h} {stride} {raw} {s['x']} {s['y']} {s['w']} {s['h']}"
            w, h, stride, raw, cx, cy, fw, fh = self.grab.frame(path, crop=crop, stream=s)
            return (f"OK {w} {h} {stride} {raw} {s['x']} {s['y']} {s['w']} {s['h']} "
                    f"{cx} {cy} {fw} {fh}")
        if cmd == "QUIT":
            self.grab.stop()
            return "OK bye"
        return f"ERR bilinmeyen komut: {cmd}"


def serve() -> int:
    from gi.repository import GLib
    if running():
        print("[nexremote-input] zaten çalışıyor", file=sys.stderr)
        return 0
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
        daemon.grab.stop()
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


def _spawn_lock():
    """Aynı anda yalnızca bir çağıran servisi başlatsın (yoksa her biri ayrı
    izin diyaloğu açar ve sonraki öncekinin soketini siler)."""
    import fcntl
    fd = os.open(SOCK + ".spawn", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def ensure(wait: float = 600.0) -> None:
    """Daemon yoksa başlat; ilk seferde kullanıcı izin diyaloğunu onaylamalı."""
    if running():
        return
    import subprocess
    log = os.path.join(RUNTIME, "nexremote-input.log")
    lock = _spawn_lock()
    if lock is not None:
        try:
            if not running():
                with open(log, "ab") as lf:
                    subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"],
                                     stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                                     start_new_session=True)
        finally:
            # kilit servis hazır olana kadar tutulur: bekleyenler ikinci kez başlatmaz
            pass
    deadline = time.monotonic() + wait
    try:
        while time.monotonic() < deadline:
            if running():
                return
            time.sleep(0.2)
    finally:
        if lock is not None:
            os.close(lock)
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            tail = f.read().strip().splitlines()[-1:]
    except OSError:
        tail = []
    raise RuntimeError("giriş servisi başlamadı" + (f": {tail[0]}" if tail else "")
                       + " — ekrandaki 'Uzaktan kontrol' iznini onayladınız mı?")


def call(line: str) -> str:
    """Komutu gönder; ERR ise istisna fırlat. Servis zaten çalışıyorsa ayrı
    bir PING gidiş-dönüşü yapılmaz: bağlantı kurulamazsa başlatılır."""
    if "\n" in line:
        raise ValueError("komut tek satır olmalı (TYPE metni JSON ile kodlanır)")
    try:
        reply = send(line)
    except (FileNotFoundError, ConnectionRefusedError):
        ensure()
        reply = send(line)
    if not reply.startswith("OK"):
        raise RuntimeError(reply[4:] if reply.startswith("ERR ") else reply or "yanıt yok")
    return reply[3:]


def frame_image(crop=None):
    """Ekran yayınının son karesi: (PIL RGB görüntüsü, ekran orijini (x, y),
    ölçek = görüntü pikseli / mantıksal piksel). crop=(x, y, w, h) mantıksal
    piksel verilirse servis yalnızca o bölgeyi yazar (eski servis bunu bilmez:
    tam kare gelir, çağıran kırpar). Servis yoksa ve kayıtlı izin de yoksa
    başlatmaz (izin diyaloğu açmamak için) — RuntimeError."""
    path = os.path.join(RUNTIME, "nexremote-frame.raw")
    line = "FRAME " + json.dumps(path)
    if crop is not None:
        line += " " + " ".join(f"{float(v):g}" for v in crop)
    try:
        reply = send(line)
    except (FileNotFoundError, ConnectionRefusedError):
        if not _load_token():
            raise RuntimeError("giriş servisi kapalı ve kayıtlı izin yok") from None
        ensure(wait=10.0)
        reply = send(line)
    if not reply.startswith("OK "):
        raise RuntimeError(reply[4:] if reply.startswith("ERR ") else reply or "yanıt yok")
    f = reply[3:].split()
    w, h, stride, raw, sx, sy, sw, _sh = f[:8]
    cx = cy = 0
    full_w = int(w)
    if len(f) >= 12:
        cx, cy, full_w = int(f[8]), int(f[9]), int(f[10])
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        try:
            os.remove(path)      # ~8 MB; tmpfs'te bekletme
        except OSError:
            pass
    from PIL import Image
    img = Image.frombuffer("RGBA", (int(w), int(h)), data, "raw", raw,
                           int(stride), 1).convert("RGB")
    scale = full_w / int(sw) if int(sw) else 1.0
    origin = (int(sx), int(sy))
    if cx or cy:
        origin = (round(int(sx) + cx / scale), round(int(sy) + cy / scale))
    return img, origin, scale


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
        # wl-copy panoyu sunmak için arka planda kalır; çıktılarını kapatmazsak
        # çağıranın borusunu açık tutar ve çıktıyı yakalayan harness'lar asılır.
        return subprocess.run(["wl-copy"], input=data, timeout=3,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              close_fds=True).returncode == 0
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
        try:
            from ._dbus import session_bus
        except ImportError:          # betik olarak çalıştırıldığında
            from nexremote._dbus import session_bus
        bus = session_bus()
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
