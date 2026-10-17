"""wlroots tabanlı Wayland bileşimcileri — Sway ve Hyprland pencere yönetimi.

Her ikisi de pencere listesini ve eylemleri kendi IPC'si üzerinden verir
(`swaymsg -t get_tree`, `hyprctl clients -j`). Giriş bu bileşimcilerde
RemoteDesktop portalı (xdg-desktop-portal-wlr/hyprland) ya da ydotool/wtype
ile yapılır; o seçim linux.py'dedir.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

from .base import Unsupported, WinInfo


def _run_json(cmd: list[str]):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unsupported(f"{cmd[0]} çalıştırılamadı: {e}")
    if r.returncode != 0:
        raise Unsupported(f"{' '.join(cmd)} başarısız: {r.stderr.strip() or r.stdout.strip()}")
    try:
        return json.loads(r.stdout)
    except ValueError as e:
        raise Unsupported(f"{cmd[0]} çıktısı JSON değil: {e}")


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unsupported(f"{cmd[0]} çalıştırılamadı: {e}")
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} başarısız: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


# ---------------------------------------------------------------- Sway
class Sway:
    name = "sway"

    @staticmethod
    def available() -> bool:
        return shutil.which("swaymsg") is not None

    def windows(self) -> list[WinInfo]:
        tree = _run_json(["swaymsg", "-t", "get_tree", "-r"])
        out: list[WinInfo] = []

        def walk(node, scratch: bool) -> None:
            is_scratch = scratch or node.get("name") == "__i3_scratch"
            for ch in node.get("nodes", []) + node.get("floating_nodes", []):
                walk(ch, is_scratch)
            if node.get("type") in ("con", "floating_con") and node.get("pid"):
                r = node.get("rect") or {}
                app = node.get("app_id") or (node.get("window_properties") or {}).get("class", "")
                out.append(WinInfo(id=str(node["id"]), title=node.get("name") or "",
                                   app=app or "", pid=node.get("pid"),
                                   x=r.get("x", 0), y=r.get("y", 0),
                                   w=r.get("width", 0), h=r.get("height", 0),
                                   active=bool(node.get("focused")),
                                   minimized=is_scratch))  # başka çalışma alanı ≠ simge durumu

        walk(tree, False)
        return out

    def act(self, wid: str, action: str) -> bool:
        cmd = {"activate": "focus", "close": "kill", "minimize": "move scratchpad"}.get(action)
        if cmd is None:
            raise ValueError(f"bilinmeyen pencere eylemi: {action}")
        win = next((w for w in self.windows() if w.id == wid), None)
        if win is None:
            return False
        if action == "activate" and win.minimized:
            # scratchpad'deki (simge durumundaki) pencereyi önce döşemeye geri getir
            _run(["swaymsg", f"[con_id={wid}] scratchpad show, floating disable"])
        _run(["swaymsg", f"[con_id={wid}] {cmd}"])
        return True

    @staticmethod
    def pointer_pos() -> Optional[tuple[int, int]]:
        return None  # Sway IPC imleç konumu vermez


# ------------------------------------------------------------- Hyprland
class Hyprland:
    name = "hyprland"
    _MIN_WS = "special:nexremote_min"

    @staticmethod
    def available() -> bool:
        return shutil.which("hyprctl") is not None

    def windows(self) -> list[WinInfo]:
        clients = _run_json(["hyprctl", "clients", "-j"])
        active = _run_json(["hyprctl", "activewindow", "-j"]) or {}
        out = []
        for c in clients:
            if not c.get("mapped", True):
                continue
            (x, y), (w, h) = c.get("at", (0, 0)), c.get("size", (0, 0))
            ws = (c.get("workspace") or {}).get("name", "")
            out.append(WinInfo(id=c["address"], title=c.get("title") or "",
                               app=c.get("class") or c.get("initialClass") or "",
                               pid=c.get("pid"), x=x, y=y, w=w, h=h,
                               active=c["address"] == active.get("address"),
                               minimized=ws.startswith("special:") or bool(c.get("hidden"))))
        return out

    def act(self, wid: str, action: str) -> bool:
        wins = {w.id: w for w in self.windows()}
        if wid not in wins:
            return False
        target = f"address:{wid}"
        if action == "activate":
            if wins[wid].minimized:
                _run(["hyprctl", "dispatch", "movetoworkspace", f"e+0,{target}"])
            _run(["hyprctl", "dispatch", "focuswindow", target])
        elif action == "close":
            _run(["hyprctl", "dispatch", "closewindow", target])
        elif action == "minimize":
            # Hyprland'de simge durumu yok; gizli özel çalışma alanı bunun karşılığı
            _run(["hyprctl", "dispatch", "movetoworkspacesilent", f"{self._MIN_WS},{target}"])
        else:
            raise ValueError(f"bilinmeyen pencere eylemi: {action}")
        return True

    @staticmethod
    def pointer_pos() -> Optional[tuple[int, int]]:
        try:
            d = _run_json(["hyprctl", "cursorpos", "-j"])
            return int(d["x"]), int(d["y"])
        except (Unsupported, KeyError, TypeError, ValueError):
            return None
