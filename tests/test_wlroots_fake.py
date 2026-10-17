#!/usr/bin/env python3
"""Sway / Hyprland sağlayıcıları — sahte `swaymsg` / `hyprctl` ile.

Bu makinede iki bileşimci de yok; gerçek IPC JSON biçimindeki örnek çıktılarla
ayrıştırma ve eylem komutları sınanır. Gerçek bileşimcide çalıştırılmadı.
"""
import json
import os
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

from nexremote.platforms.wlroots import Hyprland, Sway  # noqa: E402

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail and not ok else ""))


SWAY_TREE = {
    "id": 1, "type": "root", "name": "root", "nodes": [
        {"id": 2, "type": "output", "name": "eDP-1", "nodes": [
            {"id": 3, "type": "workspace", "name": "1", "nodes": [
                {"id": 10, "type": "con", "name": "~ — foot", "app_id": "foot", "pid": 111,
                 "focused": True, "visible": True,
                 "rect": {"x": 0, "y": 30, "width": 960, "height": 1050}, "nodes": []},
                {"id": 11, "type": "con", "name": "Firefox", "app_id": None, "pid": 222,
                 "window_properties": {"class": "firefox"}, "focused": False, "visible": True,
                 "rect": {"x": 960, "y": 30, "width": 960, "height": 1050}, "nodes": []},
            ], "floating_nodes": []}]},
        {"id": 4, "type": "output", "name": "__i3", "nodes": [
            {"id": 5, "type": "workspace", "name": "__i3_scratch", "nodes": [], "floating_nodes": [
                {"id": 12, "type": "floating_con", "name": "notlar", "app_id": "gedit", "pid": 333,
                 "focused": False, "visible": False,
                 "rect": {"x": 100, "y": 100, "width": 500, "height": 400}, "nodes": []}]}]}],
}
HYPR_CLIENTS = [
    {"address": "0x55aa01", "mapped": True, "hidden": False, "at": [10, 40], "size": [900, 700],
     "workspace": {"id": 1, "name": "1"}, "class": "kitty", "title": "kitty", "pid": 444},
    {"address": "0x55aa02", "mapped": True, "hidden": False, "at": [0, 0], "size": [800, 600],
     "workspace": {"id": -98, "name": "special:nexremote_min"}, "class": "org.gnome.Nautilus",
     "title": "Dosyalar", "pid": 555},
    {"address": "0x55aa03", "mapped": False, "at": [0, 0], "size": [0, 0],
     "workspace": {"id": 1, "name": "1"}, "class": "x", "title": "gizli", "pid": 666},
]


def fake_bin(d: str, name: str, body: str) -> None:
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(f"#!{sys.executable}\n{body}")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)


tmp = tempfile.mkdtemp()
log = os.path.join(tmp, "calls.jsonl")
common = f"import json, sys\nopen({log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
fake_bin(tmp, "swaymsg", common + f"""
if sys.argv[1:3] == ['-t', 'get_tree']:
    print(json.dumps({SWAY_TREE!r}))
else:
    print('[{{"success": true}}]')
""")
fake_bin(tmp, "hyprctl", common + f"""
a = sys.argv[1:]
if a[:1] == ['clients']: print(json.dumps({HYPR_CLIENTS!r}))
elif a[:1] == ['activewindow']: print(json.dumps({{"address": "0x55aa01"}}))
elif a[:1] == ['cursorpos']: print(json.dumps({{"x": 812, "y": 403}}))
else: print('ok')
""")
os.environ["PATH"] = tmp + os.pathsep + os.environ.get("PATH", "")


def calls() -> list[list[str]]:
    with open(log) as f:
        return [json.loads(x) for x in f]


def reset() -> None:
    open(log, "w").close()


s = Sway()
ws = {w.id: w for w in s.windows()}
check("sway: 3 pencere (scratchpad dahil)", set(ws) == {"10", "11", "12"}, str(ws))
check("sway: app_id / X sınıfı yedeği", ws["10"].app == "foot" and ws["11"].app == "firefox")
check("sway: rect + odak + pid", (ws["10"].x, ws["10"].y, ws["10"].w, ws["10"].h) == (0, 30, 960, 1050)
      and ws["10"].active and ws["10"].pid == 111)
check("sway: scratchpad = simge durumu", ws["12"].minimized and not ws["11"].minimized)
reset()
check("sway: activate", s.act("11", "activate") and calls()[-1] == ["[con_id=11] focus"], str(calls()))
reset()
s.act("12", "activate")
check("sway: scratchpad'den geri getir + odak",
      [c for c in calls() if c[:1] != ["-t"]] == [["[con_id=12] scratchpad show, floating disable"],
                                                  ["[con_id=12] focus"]], str(calls()))
reset()
s.act("10", "minimize")
s.act("10", "close")
check("sway: minimize/close", [c for c in calls() if c[:1] != ["-t"]]
      == [["[con_id=10] move scratchpad"], ["[con_id=10] kill"]], str(calls()))
check("sway: olmayan pencere → False", s.act("999", "close") is False)
try:
    s.act("10", "zıpla")
    check("sway: bilinmeyen eylem → ValueError", False)
except ValueError:
    check("sway: bilinmeyen eylem → ValueError", True)

h = Hyprland()
hw = {w.id: w for w in h.windows()}
check("hyprland: eşlenmemiş pencere atlandı", set(hw) == {"0x55aa01", "0x55aa02"}, str(hw))
check("hyprland: konum/boyut/etkin", (hw["0x55aa01"].x, hw["0x55aa01"].y, hw["0x55aa01"].w,
                                      hw["0x55aa01"].h) == (10, 40, 900, 700) and hw["0x55aa01"].active)
check("hyprland: özel çalışma alanı = simge durumu", hw["0x55aa02"].minimized)
reset()
h.act("0x55aa01", "minimize")
h.act("0x55aa02", "activate")
h.act("0x55aa01", "close")
acts = [c for c in calls() if c[:1] == ["dispatch"]]
check("hyprland: dispatch komutları", acts == [
    ["dispatch", "movetoworkspacesilent", "special:nexremote_min,address:0x55aa01"],
    ["dispatch", "movetoworkspace", "e+0,address:0x55aa02"],
    ["dispatch", "focuswindow", "address:0x55aa02"],
    ["dispatch", "closewindow", "address:0x55aa01"]], str(acts))
check("hyprland: imleç konumu", h.pointer_pos() == (812, 403))
check("hyprland: olmayan pencere → False", h.act("0xdead", "close") is False)

ok = sum(results)
print(f"WLROOTS (sahte IPC): {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
