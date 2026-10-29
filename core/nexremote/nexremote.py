"""NexRemote — ana facade.

Tüm backend'leri toplar, tek bir birleşik API sunar. Adaptörler (MCP/CLI/HTTP)
bu sınıfı sarar. Hiçbir adaptör backend detayını bilmez.

İşletim sistemine bağlı her şey (pencereler, fare/klavye, ekran görüntüsü,
masaüstündeki terminaller) `platforms.Desktop` üzerinden gider; erişilebilirlik
ağacı (öğe bul/tıkla) platformun verdiği GUI backend'idir (AT-SPI / UIA).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

from . import launch as launcher
from .base import Backend
from .models import CommandResult, Element, Screenshot, Window
from .platforms import Desktop, Unsupported, detect
from .platforms.base import runtime_dir as _runtime_dir


def _save_shot_meta(shot: Screenshot) -> None:
    with open(os.path.join(_runtime_dir(), "nexremote-shot.json"), "w") as f:
        json.dump(shot.to_dict(), f)


class NexRemote:
    def __init__(self, backends: Optional[list[Backend]] = None,
                 desktop: Optional[Desktop] = None):
        self.desktop: Desktop = desktop if desktop is not None else detect()
        if backends is None:
            backends = self._default_backends(self.desktop)
        self.backends: dict[str, Backend] = {b.name: b for b in backends}

    @staticmethod
    def _default_backends(desktop: Desktop) -> list[Backend]:
        """Var olan tüm backends'leri yükle. Bir backend eksik/bozuksa
        diğerlerini engellemesin — kısmi ortamlarda da çalışmalı."""
        out: list[Backend] = []
        try:
            gui = desktop.gui_backend()
            if gui is not None:
                out.append(gui)
        except Exception:
            pass
        try:
            from .backends.tui import TuiBackend
            out.append(TuiBackend())
        except Exception:
            pass
        return out

    def backend(self, name: str) -> Backend:
        if name not in self.backends:
            raise KeyError(f"backend yok: {name}. Mevcut: {list(self.backends)}")
        return self.backends[name]

    def _gui(self) -> Backend:
        if "gui" not in self.backends:
            raise Unsupported(
                f"erişilebilirlik ağacı bu sistemde yok ({self.desktop.name}) — "
                "`nexremote caps` eksikleri gösterir; ekran görüntüsü + koordinat "
                "tıklaması her zaman kullanılabilir")
        return self.backends["gui"]

    def _tui(self) -> Backend:
        return self.backend("tui")

    # ---------------------------------------------------------------- keşif
    def capabilities(self) -> dict[str, Any]:
        """Ne yapabildiğimi anlat — metin-tabanlı modeller bunu okur."""
        caps = {name: b.capability() for name, b in self.backends.items()}
        try:
            dcaps = {k: c.to_dict() for k, c in self.desktop.caps().items()}
        except Exception as e:
            dcaps = {k: {"available": False, "detail": f"yoklanamadı: {e}"}
                     for k in ("windows", "input", "screenshot", "terminals")}
        return {
            "platform": self.desktop.name,
            "backends": {
                n: {"available": c.available, "detail": c.detail, "needs": c.needs}
                for n, c in caps.items()
            },
            **dcaps,
            "pointer": self._pointer_cap(),
            "actions": [
                "windows", "window_action", "tree", "find", "click", "click_at",
                "move", "scroll", "drag", "key", "type_text", "set_text",
                "screenshot", "terminals", "terminal_read", "terminal_send",
                "run", "read_screen", "send_keys", "sessions", "cursor",
            ],
        }

    def setup(self) -> list[tuple[str, bool, str]]:
        """İlk kurulum (izinler, erişilebilirlik) — platforma özgü."""
        return self.desktop.setup()

    def _pointer_cap(self) -> dict[str, Any]:
        try:
            ok, msg = self.pointer_available()
            return {"available": ok, "detail": msg}
        except Exception as e:
            return {"available": False, "detail": str(e)}

    # ---------------------------------------------------------------- GUI
    def windows(self) -> list[Window]:
        """Ekrandaki pencereler. Pencere yöneticisi her pencereyi bilir;
        erişilebilirlik ağacı yalnızca yayınlayanları — ona yalnızca
        pencere yöneticisine erişilemezse düşülür."""
        try:
            return [Window(ref=f"win:{w.id}", title=w.title, app=w.app, pid=w.pid,
                           focused=w.active, bounds=(w.x, w.y, w.w, w.h))
                    for w in self.desktop.windows()]
        except Unsupported:
            if "gui" not in self.backends:
                raise
            return self._gui().windows()

    def window_action(self, ref: str, action: str) -> bool:
        """activate | close | minimize — ref: windows() çıktısındaki win:{...}"""
        if not ref.startswith("win:"):
            raise ValueError("pencere eylemi win:... ref'i ister; `windows` ile alın")
        if action not in ("activate", "close", "minimize"):
            raise ValueError(f"bilinmeyen pencere eylemi: {action}")
        return self.desktop.window_action(ref[4:], action)

    def tree(self, window_ref: Optional[str] = None) -> str:
        """Pencere ağacını LLM dostu metin olarak döndür."""
        ref = self._atspi_ref(window_ref) if window_ref else ""
        root = self._gui().tree(ref)
        text = root.render()
        if not root.children:
            win = next((w for w in self._gui().windows() if w.ref == root.ref), None)
            exe = launcher.pid_exe(win.pid) if win is not None else None
            if launcher.is_chromium(exe):
                text += "\n# " + launcher.electron_hint(win.title or win.app, exe)
        return text

    def launch(self, argv: list[str], wait: float = 15.0) -> dict:
        """Programı erişilebilirlik açık başlat (Electron'a bayrağı ekler) ve
        açılan pencerenin win: ref'ini döndür."""
        return launcher.launch(argv, self.windows, wait)

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
        """Öğeye isimle tıkla. Öğe eylem sunmuyorsa ortasına gerçek tık."""
        gui = self._gui()
        if gui.click(ref):
            return True
        b = gui.bounds(ref)
        if not b or b[2] <= 0 or b[3] <= 0:
            return False
        self.desktop.click(b[0] + b[2] // 2, b[1] + b[3] // 2)
        return True

    def bounds(self, ref: str) -> Optional[tuple[int, int, int, int]]:
        """Öğenin ekran dikdörtgeni (x, y, w, h)."""
        return self._gui().bounds(ref)

    def set_text(self, ref: str, text: str) -> bool:
        return self._gui().set_text(ref, text)

    def key(self, combo: str) -> bool:
        self.desktop.key(combo)
        return True

    def type_text(self, text: str) -> bool:
        self.desktop.type_text(text)
        return True

    # --------------------------------------------------------- gerçek fare
    def click_at(self, x: float, y: float, button: str = "left", count: int = 1,
                 restore_pointer: bool = True, show_cursor: bool = True,
                 from_shot: bool = False) -> bool:
        """Ekran koordinatına gerçek tıklama. Sistemde tek imleç vardır;
        kullanıcının imleci tıklamadan sonra eski yerine geri konur.

        from_shot=True: (x, y) son ekran görüntüsü üzerindeki pikseldir.
        """
        self._check_button(button)
        x, y = self.screen_point(x, y, from_shot)
        back = self._user_pointer() if restore_pointer else None
        if show_cursor:
            self._agent_cursor(x, y)
        self.desktop.click(x, y, button, int(count))
        if back is not None:
            self.desktop.move(*back)
        return True

    @staticmethod
    def _check_button(button: str) -> None:
        if button not in ("left", "right", "middle"):
            raise ValueError(f"bilinmeyen fare düğmesi: {button}")

    def screen_point(self, x: float, y: float, from_shot: bool) -> tuple[int, int]:
        if not from_shot:
            return int(x), int(y)
        shot = self.last_screenshot()
        if shot is None:
            raise RuntimeError("kayıtlı ekran görüntüsü yok — önce screenshot alın")
        return shot.to_screen(x, y)

    def move(self, x: int, y: int) -> bool:
        self.desktop.move(int(x), int(y))
        return True

    def scroll(self, dy: int, dx: int = 0, at: Optional[tuple[int, int]] = None,
               restore_pointer: bool = True) -> bool:
        back = self._user_pointer() if (at and restore_pointer) else None
        if at:
            self.desktop.move(int(at[0]), int(at[1]))
        self.desktop.scroll(int(dy), int(dx))
        if back is not None:
            self.desktop.move(*back)
        return True

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left",
             steps: int = 12) -> bool:
        import time
        self._check_button(button)
        steps = max(1, int(steps))
        back = self._user_pointer()
        self.desktop.move(int(x1), int(y1))
        self.desktop.button(button, True)
        try:
            for i in range(1, steps + 1):
                self.desktop.move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
                time.sleep(0.01)
        finally:
            self.desktop.button(button, False)
        if back is not None:
            self.desktop.move(*back)
        return True

    def _user_pointer(self) -> Optional[tuple[int, int]]:
        try:
            return self.desktop.pointer_pos()
        except Exception:
            return None  # imleci geri koymak isteğe bağlı; konum bilinmiyorsa atla

    def _agent_cursor(self, x: int, y: int) -> None:
        """Görsel geri bildirim: ajan imleci hedefe gider (varsa; hata yutulur
        çünkü tıklamanın kendisi imlece bağlı değildir)."""
        if os.environ.get("NEXREMOTE_CURSOR", "1") == "0" or not self.pointer_available()[0]:
            return
        try:
            from . import xcursor as xc
            if not xc.daemon_running():
                xc.start_daemon(os.environ.get("DISPLAY", ":1"))
            xc.send(f"MOVE {int(x)} {int(y)}")   # yanıt hareket bitince gelir
            xc.send("CLICK")
        except Exception:
            pass

    # -------------------------------------------- masaüstündeki terminaller
    def terminals(self) -> list[dict]:
        return [t.to_dict() for t in self.desktop.terminals()]

    def terminal_read(self, ref: str, history: int = 0) -> str:
        return self.desktop.terminal_read(ref, history)

    def terminal_history_supported(self, ref: str) -> bool:
        return self.desktop.terminal_history_supported(ref)

    def terminal_send(self, ref: str, text: str = "", enter: bool = False,
                      keys: Optional[list[str]] = None) -> bool:
        self.desktop.terminal_send(ref, text, enter=enter, keys=keys)
        return True

    def screenshot(self, path: Optional[str] = None, window_ref: Optional[str] = None,
                   max_width: int = 0) -> Screenshot:
        """Ekran görüntüsü; istenirse tek pencereye kırpılır ve küçültülür.

        Ölçek ve orijin kaydedilir: `click_at(..., from_shot=True)` görüntü
        üzerindeki koordinatı ekran koordinatına kendisi çevirir.
        """
        path = os.path.abspath(path or os.path.join(_runtime_dir(), "nexremote-shot.png"))
        shot = self.desktop.screenshot(path)
        crop = None
        if window_ref:
            w = next((w for w in self.windows() if w.ref == window_ref), None)
            if w is None or not w.bounds:
                raise ValueError(f"pencere bulunamadı: {window_ref}")
            # ekran koordinatı → görüntü pikseli (orijin ve ölçek platformdan gelir)
            x, y, ww, hh = w.bounds
            ox, oy, sc = shot.origin[0], shot.origin[1], shot.scale
            crop = (max(0, round((x - ox) * sc)), max(0, round((y - oy) * sc)),
                    min(shot.width, round((x + ww - ox) * sc)),
                    min(shot.height, round((y + hh - oy) * sc)))
            if crop[2] <= crop[0] or crop[3] <= crop[1]:
                raise ValueError(f"pencere ekranın dışında: {window_ref}")
        if crop or (max_width and shot.width > max_width):
            try:
                from PIL import Image
            except ImportError as e:
                raise RuntimeError("kırpma/küçültme için Pillow gerekli "
                                   "(pip install pillow)") from e
            img = Image.open(path)
            if crop:
                img = img.crop(crop)
                shot.origin = (round(shot.origin[0] + crop[0] / shot.scale),
                               round(shot.origin[1] + crop[1] / shot.scale))
            if max_width and img.width > max_width:
                factor = max_width / img.width
                shot.scale *= factor
                img = img.resize((max_width, round(img.height * factor)), Image.LANCZOS)
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
        xc.send(f"MOVE {int(x)} {int(y)}")   # yanıt hareket bitince gelir
        if click:
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
        if not sys.platform.startswith("linux"):
            return (False, f"ajan imleci şimdilik yalnızca Linux'ta (X11/XWayland); "
                           f"{self.desktop.name} üzerinde tıklamalar imleçsiz yapılır")
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
