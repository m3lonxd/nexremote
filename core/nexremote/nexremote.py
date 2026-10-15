"""NexRemote — ana facade.

Tüm backend'leri toplar, tek bir birleşik API sunar. Adaptörler (MCP/CLI/HTTP)
bu sınıfı sarar. Hiçbir adaptör backend detayını bilmez.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from .base import Backend
from .models import Capability, CommandResult, Element, Screenshot, Window


def _runtime_dir() -> str:
    d = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/nexremote-{os.getuid()}"
    os.makedirs(d, exist_ok=True)
    return d


def _save_shot_meta(shot: Screenshot) -> None:
    with open(os.path.join(_runtime_dir(), "nexremote-shot.json"), "w") as f:
        json.dump(shot.to_dict(), f)


class NexRemote:
    def __init__(self, backends: Optional[list[Backend]] = None):
        if backends is None:
            backends = self._default_backends()
        self.backends: dict[str, Backend] = {b.name: b for b in backends}

    @staticmethod
    def _default_backends() -> list[Backend]:
        """Var olan tüm backends'leri yükle. Bir backend eksik/bozuksa
        diğerlerini engellemesin — kısmi ortamlarda da çalışmalı."""
        import importlib
        out: list[Backend] = []
        for mod_name, cls_name in (("gui", "GuiBackend"), ("tui", "TuiBackend")):
            try:
                mod = importlib.import_module(f".backends.{mod_name}", __package__)
                out.append(getattr(mod, cls_name)())
            except Exception:
                pass
        return out

    def backend(self, name: str) -> Backend:
        if name not in self.backends:
            raise KeyError(f"backend yok: {name}. Mevcut: {list(self.backends)}")
        return self.backends[name]

    def _gui(self) -> Backend:
        return self.backend("gui")

    def _tui(self) -> Backend:
        return self.backend("tui")

    # ---------------------------------------------------------------- keşif
    def capabilities(self) -> dict[str, Any]:
        """Ne yapabildiğimi anlat — metin-tabanlı modeller bunu okur."""
        caps = {name: b.capability() for name, b in self.backends.items()}
        return {
            "backends": {
                n: {"available": c.available, "detail": c.detail, "needs": c.needs}
                for n, c in caps.items()
            },
            "input": self._input_cap(),
            "windows": self._windows_cap(),
            "terminals": self._terminals_cap(),
            "pointer": self._pointer_cap(),
            "actions": [
                "windows", "window_action", "tree", "find", "click", "click_at",
                "move", "scroll", "drag", "key", "type_text", "set_text",
                "screenshot", "terminals", "terminal_read", "terminal_send",
                "run", "read_screen", "send_keys", "sessions", "cursor",
            ],
        }

    @staticmethod
    def _input_cap() -> dict[str, Any]:
        from . import portal_input
        ok, msg = portal_input.available()
        return {"available": ok, "detail": msg}

    @staticmethod
    def _windows_cap() -> dict[str, Any]:
        from . import kwin
        if kwin.available():
            return {"available": True, "detail": "KWin: ekrandaki tüm pencereler"}
        return {"available": False, "detail": "KWin yok — yalnızca AT-SPI pencereleri"}

    @staticmethod
    def _terminals_cap() -> dict[str, Any]:
        from . import terminals
        try:
            n = len(terminals.list_terminals())
        except Exception as e:
            return {"available": False, "detail": str(e)}
        return {"available": True, "detail": f"{n} açık terminal (Konsole D-Bus + tmux)"}

    def _pointer_cap(self) -> dict[str, Any]:
        try:
            ok, msg = self.pointer_available()
            return {"available": ok, "detail": msg}
        except Exception as e:
            return {"available": False, "detail": str(e)}

    # ---------------------------------------------------------------- GUI
    def windows(self) -> list[Window]:
        """Ekrandaki pencereler. KWin her pencereyi bilir; AT-SPI yalnızca
        erişilebilirlik yayınlayanları — KWin yoksa ona düşülür."""
        from . import kwin
        if kwin.available():
            try:
                return [Window(ref=f"win:{w['id']}", title=w["title"], app=w["app"],
                               pid=w["pid"], focused=w["active"],
                               bounds=(w["x"], w["y"], w["w"], w["h"]))
                        for w in kwin.windows()]
            except Exception:
                pass
        return self._gui().windows()

    def window_action(self, ref: str, action: str) -> bool:
        """activate | close | minimize — ref: windows() çıktısındaki win:{...}"""
        from . import kwin
        if not ref.startswith("win:"):
            raise ValueError("pencere eylemi KWin ref'i ister (win:{...}); `windows` ile alın")
        return kwin.act(ref[4:], action)

    def tree(self, window_ref: Optional[str] = None) -> str:
        """Pencere ağacını LLM dostu metin olarak döndür."""
        ref = self._atspi_ref(window_ref) if window_ref else ""
        return self._gui().tree(ref).render()

    def _atspi_ref(self, ref: str) -> str:
        """win:{id} (KWin) → aynı pencerenin atspi: ref'i (pid + başlık eşleşmesi)."""
        if not ref.startswith("win:"):
            return ref
        target = next((w for w in self.windows() if w.ref == ref), None)
        if target is None:
            raise ValueError(f"pencere bulunamadı: {ref}")
        cands = [w for w in self._gui().windows() if w.pid == target.pid]
        exact = [w for w in cands if w.title == target.title]
        if exact or cands:
            return (exact or cands)[0].ref
        raise RuntimeError(
            f"'{target.title}' erişilebilirlik ağacı yayınlamıyor — "
            "`nexremote setup` ile erişilebilirliği açıp uygulamayı yeniden başlatın "
            "ya da ekran görüntüsü + koordinat tıklaması kullanın")

    def find(self, query: str, window_ref: Optional[str] = None) -> list[Element]:
        return self._gui().find(query, self._atspi_ref(window_ref) if window_ref else None)

    def click(self, ref: str) -> bool:
        return self._gui().click(ref)

    def bounds(self, ref: str) -> Optional[tuple[int, int, int, int]]:
        """Öğenin ekran dikdörtgeni (x, y, w, h) — AT-SPI ref."""
        gui = self._gui()
        node = gui._resolve(ref)
        return gui._extents(node) if node is not None else None

    def set_text(self, ref: str, text: str) -> bool:
        return self._gui().set_text(ref, text)

    def key(self, combo: str) -> bool:
        from . import portal_input
        if portal_input.available()[0]:
            portal_input.call("KEY " + combo)
            return True
        return self._gui().key(combo)

    def type_text(self, text: str) -> bool:
        from . import portal_input
        if portal_input.available()[0]:
            portal_input.type_text(text, paste_combo=self._paste_combo())
            return True
        return self._gui().type_text(text)

    @staticmethod
    def _paste_combo() -> str:
        """Terminaller ctrl+v'yi yapıştırma olarak değil ^V olarak alır."""
        from . import kwin
        terminals = ("konsole", "kitty", "alacritty", "foot", "wezterm", "terminal",
                     "xterm", "ghostty", "yakuake", "tilix", "terminator")
        try:
            w = kwin.active() if kwin.available() else None
        except Exception:
            w = None
        app = (w or {}).get("app", "").lower()
        return "ctrl+shift+v" if any(t in app for t in terminals) else "ctrl+v"

    # ------------------------------------------------ fare (gerçek, portal)
    def click_at(self, x: float, y: float, button: str = "left", count: int = 1,
                 restore_pointer: bool = True, show_cursor: bool = True,
                 from_shot: bool = False) -> bool:
        """Ekran koordinatına gerçek tıklama. Wayland'de tek imleç vardır;
        kullanıcının imleci tıklamadan sonra eski yerine geri konur.

        from_shot=True: (x, y) son ekran görüntüsü üzerindeki pikseldir.
        """
        from . import portal_input
        x, y = self.screen_point(x, y, from_shot)
        back = self._user_pointer() if restore_pointer else None
        if show_cursor:
            self._agent_cursor(x, y)
        portal_input.call(f"CLICK {int(x)} {int(y)} {button} {int(count)}")
        if back is not None:
            portal_input.call(f"MOVE {back[0]} {back[1]}")
        return True

    def screen_point(self, x: float, y: float, from_shot: bool) -> tuple[int, int]:
        if not from_shot:
            return int(x), int(y)
        shot = self.last_screenshot()
        if shot is None:
            raise RuntimeError("kayıtlı ekran görüntüsü yok — önce screenshot alın")
        return shot.to_screen(x, y)

    def move(self, x: int, y: int) -> bool:
        from . import portal_input
        portal_input.call(f"MOVE {int(x)} {int(y)}")
        return True

    def scroll(self, dy: int, dx: int = 0, at: Optional[tuple[int, int]] = None,
               restore_pointer: bool = True) -> bool:
        from . import portal_input
        back = self._user_pointer() if (at and restore_pointer) else None
        if at:
            portal_input.call(f"MOVE {int(at[0])} {int(at[1])}")
        portal_input.call(f"SCROLL {int(dy)} {int(dx)}")
        if back is not None:
            portal_input.call(f"MOVE {back[0]} {back[1]}")
        return True

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left",
             steps: int = 12) -> bool:
        import time
        from . import portal_input
        back = self._user_pointer()
        portal_input.call(f"MOVE {int(x1)} {int(y1)}")
        portal_input.call(f"DOWN {button}")
        try:
            for i in range(1, steps + 1):
                portal_input.call(f"MOVE {x1 + (x2 - x1) * i / steps:.1f} "
                                  f"{y1 + (y2 - y1) * i / steps:.1f}")
                time.sleep(0.01)
        finally:
            portal_input.call(f"UP {button}")
        if back is not None:
            portal_input.call(f"MOVE {back[0]} {back[1]}")
        return True

    @staticmethod
    def _user_pointer() -> Optional[tuple[int, int]]:
        from . import kwin
        try:
            return kwin.cursor_pos() if kwin.available() else None
        except Exception:
            return None

    def _agent_cursor(self, x: int, y: int) -> None:
        """Görsel geri bildirim: ajan imleci hedefe gider (varsa; hata yutulur
        çünkü tıklamanın kendisi imlece bağlı değildir)."""
        if os.environ.get("NEXREMOTE_CURSOR", "1") == "0" or not self.pointer_available()[0]:
            return
        try:
            import time
            from . import xcursor as xc
            if not xc.daemon_running():
                xc.start_daemon(os.environ.get("DISPLAY", ":1"))
            xc.send(f"MOVE {int(x)} {int(y)} 0.25")
            time.sleep(0.25)
            xc.send("CLICK")
        except Exception:
            pass

    # -------------------------------------------- masaüstündeki terminaller
    def terminals(self) -> list[dict]:
        from . import terminals
        return [t.to_dict() for t in terminals.list_terminals()]

    def terminal_read(self, ref: str, history: int = 0) -> str:
        from . import terminals
        return terminals.read(ref, history)

    def terminal_send(self, ref: str, text: str = "", enter: bool = False,
                      keys: Optional[list[str]] = None) -> bool:
        from . import terminals
        terminals.send(ref, text, enter=enter, keys=keys)
        return True

    def screenshot(self, path: Optional[str] = None, window_ref: Optional[str] = None,
                   max_width: int = 0) -> Screenshot:
        """Ekran görüntüsü; istenirse tek pencereye kırpılır ve küçültülür.

        Ölçek ve orijin kaydedilir: `click_at(..., from_shot=True)` görüntü
        üzerindeki koordinatı ekran koordinatına kendisi çevirir.
        """
        path = os.path.abspath(path or os.path.join(_runtime_dir(), "nexremote-shot.png"))
        shot = self._gui().screenshot(path)
        if not shot.backend:
            raise RuntimeError("ekran görüntüsü alınamadı (spectacle/grim yok veya reddedildi)")
        crop = None
        if window_ref:
            w = next((w for w in self.windows() if w.ref == window_ref), None)
            if w is None or not w.bounds:
                raise ValueError(f"pencere bulunamadı: {window_ref}")
            x, y, ww, hh = w.bounds
            crop = (max(0, x), max(0, y), min(shot.width, x + ww), min(shot.height, y + hh))
        if crop or (max_width and shot.width > max_width):
            try:
                from PIL import Image
            except ImportError as e:
                raise RuntimeError("kırpma/küçültme için python3-pil gerekli") from e
            img = Image.open(path)
            if crop:
                img = img.crop(crop)
                shot.origin = (crop[0], crop[1])
            if max_width and img.width > max_width:
                shot.scale = max_width / img.width
                img = img.resize((max_width, round(img.height * shot.scale)), Image.LANCZOS)
            img.save(path)
            shot.width, shot.height = img.size
        _save_shot_meta(shot)
        return shot

    @staticmethod
    def last_screenshot() -> Optional[Screenshot]:
        try:
            with open(os.path.join(_runtime_dir(), "nexremote-shot.json")) as f:
                d = json.load(f)
            d["origin"] = tuple(d["origin"])
            return Screenshot(**d)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def focus(self, ref: str) -> bool:
        return self._gui().focus(ref)

    # ------------------------------------------------- görünür fare imleci
    def cursor(self, x: int, y: int, click: bool = False,
               button: int = 1, visible: bool = True) -> bool:
        """AJANIN KENDİ imlecini (x,y)'ye götür; istenirse tıkla.

        Kullanıcının faresine DOKUNMAZ. Saf Xlib ile override-redirect küçük
        bir pencere kullanır (imleç boyutunda, click-through): arkasındaki her
        şey normal tıklanabilir kalır. Tıklama AT-SPI ile yapılır.
        """
        from . import xcursor as xc
        if not xc.daemon_running():
            xc.start_daemon(os.environ.get("DISPLAY", ":1"))
        if not xc.daemon_running():
            raise RuntimeError("ajan imleci daemon'ı başlatılamadı")
        xc.send(f"MOVE {int(x)} {int(y)} 0.5")
        if click:
            import time
            time.sleep(0.5)
            xc.send("CLICK")
        return True

    def cursor_move(self, x: int, y: int, steps: int = 24,
                    duration: float = 0.5) -> bool:
        """Ajan imlecini yumuşak adımlarla (x,y)'ye taşı."""
        from . import xcursor as xc
        if not xc.daemon_running():
            xc.start_daemon(os.environ.get("DISPLAY", ":1"))
        if not xc.daemon_running():
            raise RuntimeError("ajan imleci daemon'ı başlatılamadı")
        return xc.send(f"MOVE {int(x)} {int(y)} {duration}") == "OK"

    def cursor_hide(self) -> bool:
        from . import xcursor as xc
        if xc.daemon_running():
            xc.send("HIDE")
            return True
        return False

    def pointer_available(self) -> tuple[bool, str]:
        """Ajan imleci kullanılabilir mi? (X11/XWayland + python-xlib)"""
        try:
            import Xlib  # noqa
            from Xlib.ext import shape  # noqa
        except Exception as e:
            return (False, f"python-xlib + SHAPE gerekli: {e}")
        if not os.environ.get("DISPLAY"):
            return (False, "DISPLAY yok (X11/XWayland gerekir)")
        return (True, "hazır (ajan imleci: küçük, click-through, kullanıcıyı engellemez)")

    # ---------------------------------------------------------------- TUI
    def run(self, command: str, session: str = "default",
            timeout: float = 30.0) -> CommandResult:
        return self._tui().run(command, session, timeout)

    def read_screen(self, session: str = "default") -> str:
        return self._tui().read_screen(session)

    def send_keys(self, keys: str, session: str = "default") -> bool:
        return self._tui().send_keys(keys, session)

    def sessions(self) -> list[str]:
        try:
            return self._tui().list_sessions()
        except Exception:
            return []

    def cleanup(self) -> list[str]:
        """Kendi açtığı TUI oturumlarını kapat (tmux). Kullanıcınınkine dokunmaz."""
        b = self.backends.get("tui")
        if b is not None and hasattr(b, "cleanup"):
            try:
                return b.cleanup()
            except Exception:
                return []
        return []

    def close(self) -> None:
        for b in self.backends.values():
            try:
                b.close()
            except Exception:
                pass
