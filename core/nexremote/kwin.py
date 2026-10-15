"""KWin (KDE Wayland) pencere yönetimi — scripting API üzerinden.

AT-SPI yalnızca erişilebilirlik ağacı yayınlayan uygulamaları görür; KWin ise
ekrandaki HER pencereyi bilir (başlık, uygulama, pid, konum, etkin mi). Wayland
güvenlik modeli dışarıdan pencere listesi vermez, ama KWin'e yüklenen bir betik
`workspace` nesnesine erişir. Betik sonucu `callDBus` ile bize geri yollar.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from typing import Any, Optional

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
