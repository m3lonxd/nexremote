"""KWin (KDE Wayland) pencere yönetimi — scripting API üzerinden.

AT-SPI yalnızca erişilebilirlik ağacı yayınlayan uygulamaları görür; KWin ise
ekrandaki HER pencereyi bilir (başlık, uygulama, pid, konum, etkin mi). Wayland
güvenlik modeli dışarıdan pencere listesi vermez, ama KWin'e yüklenen bir betik
`workspace` nesnesine erişir. Betik sonucu `callDBus` ile bize geri yollar.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from typing import Any, Optional

from . import trace

_LIST_JS = r"""
const out = [];
const active = workspace.activeWindow;
for (const w of workspace.windowList()) {
    if (!w.normalWindow && !w.dialog) continue;
    if (w.skipTaskbar && !w.dialog) continue;
    const g = w.frameGeometry;
    out.push({
        id: String(w.internalId), title: w.caption, app: w.resourceClass,
        pid: w.pid, x: Math.round(g.x), y: Math.round(g.y),
        w: Math.round(g.width), h: Math.round(g.height),
        active: w === active, minimized: w.minimized,
        output: w.output ? w.output.name : "", dialog: w.dialog,
    });
}
callDBus("%(service)s", "/", "org.nexremote.Reply", "reply", JSON.stringify(out));
"""

_ACT_JS = r"""
let ok = false;
for (const w of workspace.windowList()) {
    if (String(w.internalId) !== %(wid)s) continue;
    %(body)s
    ok = true;
    break;
}
callDBus("%(service)s", "/", "org.nexremote.Reply", "reply", JSON.stringify(ok));
"""

_CURSOR_JS = r"""
const p = workspace.cursorPos;
callDBus("%(service)s", "/", "org.nexremote.Reply", "reply",
         JSON.stringify({x: Math.round(p.x), y: Math.round(p.y)}));
"""

_SCREENS_JS = r"""
const out = [];
for (const o of workspace.screens) {
    const g = o.geometry;
    out.push({name: o.name, x: Math.round(g.x), y: Math.round(g.y),
              w: Math.round(g.width), h: Math.round(g.height)});
}
callDBus("%(service)s", "/", "org.nexremote.Reply", "reply", JSON.stringify(out));
"""

_ACTIONS = {
    "activate": "if (w.minimized) w.minimized = false; workspace.activeWindow = w;",
    "close": "w.closeWindow();",
    "minimize": "w.minimized = true;",
}


def available() -> bool:
    try:
        from ._dbus import session_bus
        session_bus().get_object("org.kde.KWin", "/Scripting")
        return True
    except Exception:
        return False


def _run_script(js_template: str, timeout: float = 4.0, **fields: str) -> Any:
    """Betiği KWin'de çalıştır, callDBus ile dönen JSON'u bekle ve döndür."""
    with trace.span("kwin betiği"):
        return _run_script_impl(js_template, timeout, **fields)


def _run_script_impl(js_template: str, timeout: float = 4.0, **fields: str) -> Any:
    import dbus
    import dbus.service
    from gi.repository import GLib

    from ._dbus import session_bus
    # Paylaşılan bağlantı: özel bağlantıyı kapatmak, libdbus'ın
    # exit-on-disconnect davranışı yüzünden süreci sessizce exit(1) ile bitirir.
    bus = session_bus()
    service = f"org.nexremote.r{os.getpid()}x{uuid.uuid4().hex[:8]}"
    name = dbus.service.BusName(service, bus)
    loop = GLib.MainLoop()
    box: dict = {}

    class Reply(dbus.service.Object):
        @dbus.service.method("org.nexremote.Reply", in_signature="s")
        def reply(self, payload):
            box["payload"] = str(payload)
            GLib.idle_add(loop.quit)

    replier = Reply(bus, "/")
    js = js_template % {"service": service, **fields}
    fd, path = tempfile.mkstemp(prefix="nexremote-kwin-", suffix=".js")
    plugin = service.replace(".", "_")
    scripting = dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting"),
                               "org.kde.kwin.Scripting")
    timer = 0
    try:
        with os.fdopen(fd, "w") as f:
            f.write(js)
        sid = int(scripting.loadScript(path, plugin, signature="ss"))
        if sid < 0:
            raise RuntimeError("KWin betiği yüklenemedi")
        script = bus.get_object("org.kde.KWin", f"/Scripting/Script{sid}")
        dbus.Interface(script, "org.kde.kwin.Script").run()
        timer = GLib.timeout_add(int(timeout * 1000), loop.quit)
        loop.run()
    finally:
        if timer:
            GLib.source_remove(timer)
        try:
            scripting.unloadScript(plugin)
        except Exception:
            pass
        os.unlink(path)
        replier.remove_from_connection()
        bus.release_name(service)
        del name
    if "payload" not in box:
        raise TimeoutError("KWin betiği yanıt vermedi")
    return json.loads(box["payload"])


def windows() -> list[dict]:
    """Ekrandaki tüm normal pencereler, üstten alta değil KWin sırasıyla."""
    return _run_script(_LIST_JS)


def act(window_id: str, action: str) -> bool:
    if action not in _ACTIONS:
        raise ValueError(f"bilinmeyen pencere eylemi: {action}")
    return bool(_run_script(_ACT_JS, wid=json.dumps(window_id), body=_ACTIONS[action]))


def active() -> Optional[dict]:
    for w in windows():
        if w["active"]:
            return w
    return None


def cursor_pos() -> tuple[int, int]:
    """Kullanıcının gerçek imleç konumu (Wayland'de istemciler bunu bilemez)."""
    p = _run_script(_CURSOR_JS)
    return int(p["x"]), int(p["y"])


def screens() -> list[dict]:
    """Ekranlar (çıkışlar): {name, x, y, w, h} — mantıksal piksel, masaüstü koordinatı."""
    return _run_script(_SCREENS_JS)


# ------------------------------------------------------------ sürekli izleme
_WATCH_JS = r"""
const SVC = "%(service)s";
function snap(gone) {
    const out = [];
    const active = workspace.activeWindow;
    for (const w of workspace.windowList()) {
        if (w === gone) continue;
        if (!w.normalWindow && !w.dialog) continue;
        if (w.skipTaskbar && !w.dialog) continue;
        const g = w.frameGeometry;
        out.push({
            id: String(w.internalId), title: w.caption, app: w.resourceClass,
            pid: w.pid, x: Math.round(g.x), y: Math.round(g.y),
            w: Math.round(g.width), h: Math.round(g.height),
            active: w === active, minimized: w.minimized,
            output: w.output ? w.output.name : "", dialog: w.dialog,
        });
    }
    callDBus(SVC, "/watch", "org.nexremote.Watch", "update", JSON.stringify(out));
}
function hook(w) {
    try { w.captionChanged.connect(function () { snap(null); }); } catch (e) {}
    try { w.frameGeometryChanged.connect(function () { snap(null); }); } catch (e) {}
    try { w.minimizedChanged.connect(function () { snap(null); }); } catch (e) {}
}
for (const w of workspace.windowList()) hook(w);
workspace.windowAdded.connect(function (w) { hook(w); snap(null); });
workspace.windowRemoved.connect(function (w) { snap(w); });
workspace.windowActivated.connect(function () { snap(null); });
snap(null);
"""


def _norm(ws: list[dict]) -> list[tuple]:
    return sorted((str(w.get("id")), w.get("title"), w.get("x"), w.get("y"), w.get("w"),
                   w.get("h"), bool(w.get("active")), bool(w.get("minimized"))) for w in ws)


class Watcher:
    """Uzun ömürlü süreç için: KWin'e kalıcı bir betik yükler; betik pencere
    eklenince/kapanınca/taşınınca/etkinleşince listeyi D-Bus ile iter.
    `windows()` bellekten yanıt verir (betik yükle-çalıştır-kaldır yok).

    Kendini doğrular: VALIDATE saniyede bir tek seferlik betikle karşılaştırır;
    tutmazsa None döner ve çağıran kalıcı olarak tek seferlik yola döner."""
    VALIDATE = 10.0

    def __init__(self) -> None:
        self._data: Optional[list] = None
        self._checked = time.monotonic()
        self._plugin = f"nexremote_watch_{os.getpid()}"
        self._service = f"org.nexremote.w{os.getpid()}x{uuid.uuid4().hex[:6]}"
        self._obj = self._name = self._scripting = None

    @classmethod
    def start(cls, first_wait: float = 1.5) -> Optional["Watcher"]:
        if not available():
            return None
        w = cls()
        try:
            w._load()
            end = time.monotonic() + first_wait
            while w._data is None and time.monotonic() < end:
                w._drain(block=True)
            if w._data is None:
                raise TimeoutError("izleyici betiği ilk listeyi göndermedi")
            return w
        except Exception as e:
            sys.stderr.write(f"nexremote: KWin izleyici kullanılamıyor ({e}); "
                             "tek seferlik betiğe dönülüyor\n")
            w.stop()
            return None

    def _load(self) -> None:
        import dbus
        import dbus.service
        from ._dbus import session_bus
        bus = session_bus()
        self._name = dbus.service.BusName(self._service, bus)
        me = self

        class Watch(dbus.service.Object):
            @dbus.service.method("org.nexremote.Watch", in_signature="s")
            def update(self, payload):
                try:
                    data = json.loads(str(payload))
                    if isinstance(data, list):
                        me._data = data
                except ValueError:
                    pass

        self._obj = Watch(bus, "/watch")
        self._scripting = dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting"),
                                         "org.kde.kwin.Scripting")
        try:
            self._scripting.unloadScript(self._plugin)
        except Exception:
            pass
        fd, path = tempfile.mkstemp(prefix="nexremote-kwin-watch-", suffix=".js")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(_WATCH_JS % {"service": self._service})
            sid = int(self._scripting.loadScript(path, self._plugin, signature="ss"))
            if sid < 0:
                raise RuntimeError("KWin izleyici betiği yüklenemedi")
            script = bus.get_object("org.kde.KWin", f"/Scripting/Script{sid}")
            dbus.Interface(script, "org.kde.kwin.Script").run()
        finally:
            os.unlink(path)

    @staticmethod
    def _drain(block: bool = False) -> None:
        from gi.repository import GLib
        ctx = GLib.MainContext.default()
        if block:
            ctx.iteration(True) if ctx.pending() else time.sleep(0.01)
        while ctx.pending():
            ctx.iteration(False)

    def windows(self) -> Optional[list[dict]]:
        """Son itilen liste; izleyici güvenilmezse None."""
        try:
            self._drain()
        except Exception:
            return None
        if self._data is None:
            return None
        if time.monotonic() - self._checked > self.VALIDATE:
            fresh = windows()
            self._drain()
            if _norm(fresh) != _norm(self._data):
                sys.stderr.write("nexremote: KWin izleyici listesi tek seferlik okumayla "
                                 "tutmadı; izleyici kapatıldı\n")
                self.stop()
                return None
            self._checked = time.monotonic()
        return json.loads(json.dumps(self._data))

    def invalidate(self) -> None:
        """Pencere eylemi sonrası: bir sonraki okuma doğrulansın."""
        self._checked = 0.0

    def stop(self) -> None:
        try:
            if self._scripting is not None:
                self._scripting.unloadScript(self._plugin)
        except Exception:
            pass
        try:
            if self._obj is not None:
                self._obj.remove_from_connection()
        except Exception:
            pass
        try:
            if self._name is not None:
                from ._dbus import session_bus
                session_bus().release_name(self._service)
        except Exception:
            pass
        self._obj = self._name = self._scripting = None
