"""GUI backend — AT-SPI (Linux erişilebilirlik ağacı) ile masaüstü görme + kontrol.

Piksel tahmini yapmaz: her buton/menü/alan yapısal olarak okunur, isimle
tıklanır. KDE/GNOME/GTK/Qt uygulamalarında çalışır. Electron uygulamaları
(Discord/VSCode) iç butonlar için --force-renderer-accessibility ister.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from typing import Optional

from ..base import Backend
from ..models import Capability, Element, Role, Screenshot, Window
from .. import input as inp

# AT-SPI rol adı -> normalize Role
_ROLE_MAP = {
    "push button": Role.BUTTON, "button": Role.BUTTON,
    "entry": Role.ENTRY, "text": Role.ENTRY, "password text": Role.ENTRY,
    "frame": Role.FRAME, "window": Role.WINDOW, "dialog": Role.DIALOG,
    "menu": Role.MENU, "menu item": Role.MENU_ITEM, "menu bar": Role.MENU,
    "check box": Role.CHECKBOX, "check menu item": Role.CHECKBOX,
    "radio button": Role.RADIO, "radio menu item": Role.RADIO,
    "combo box": Role.COMBOBOX, "page tab": Role.TAB, "page tab list": Role.TAB,
    "list": Role.LIST, "list item": Role.LIST_ITEM,
    "tree": Role.TREE, "tree item": Role.TREE_ITEM, "table cell": Role.TREE_ITEM,
    "link": Role.LINK, "image": Role.IMAGE, "icon": Role.IMAGE,
    "slider": Role.SLIDER, "panel": Role.PANEL, "filler": Role.PANEL,
    "label": Role.TEXT, "terminal": Role.TERMINAL,
    "scroll bar": Role.SLIDER, "separator": Role.PANEL, "tool bar": Role.PANEL,
}

# AT-SPI eylem adı -> normalize
_ACTION_MAP = {
    "click": "click", "press": "click", "activate": "click",
    "dodefault": "click", "do default": "click", "jump": "click",
    "toggle": "toggle", "check": "toggle",
    "settext": "set_text", "set text": "set_text",
    "expand": "expand", "collapse": "expand",
    "grab focus": "focus", "focus": "focus", "select": "select",
}

_INTERACTIVE = {Role.BUTTON, Role.ENTRY, Role.CHECKBOX, Role.RADIO,
                Role.COMBOBOX, Role.MENU_ITEM, Role.TAB, Role.LINK,
                Role.LIST_ITEM, Role.TREE_ITEM, Role.SLIDER}


def _atspi():
    """Atspi modülünü tembel yükle; yoksa None."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        return Atspi
    except Exception:
        return None


def _normalize_action(name: str) -> str:
    return _ACTION_MAP.get((name or "").lower().replace("_", " ").strip(), "")


class GuiBackend(Backend):
    name = "gui"
    kind = "gui"

    def __init__(self) -> None:
        self._Atspi = _atspi()
        self._max_depth = 12

    # ------------------------------------------------------------ capability
    def capability(self) -> Capability:
        needs: list[str] = []
        if self._Atspi is None:
            needs.append("python3-gi + gir1.2-atspi-2.0")
        ok = self._Atspi is not None
        detail = ("AT-SPI: isimle bul/tıkla (erişilebilirlik yayınlayan uygulamalar)"
                  if ok else "AT-SPI yok — öğe ağacı devre dışı")
        return Capability(name=self.name, available=ok, detail=detail, needs=needs)

    # ------------------------------------------------------------ ref yardım
    @staticmethod
    def _parse_ref(ref: str) -> Optional[list[int]]:
        if not ref.startswith("atspi:"):
            return None
        try:
            body = ref[len("atspi:"):]           # "0:1/2/3"
            app_part, _, rest = body.partition(":")  # "0", "1/2/3"
            parts = [int(app_part)]
            if rest:
                parts += [int(x) for x in rest.split("/") if x != ""]
            return parts
        except Exception:
            return None

    def _resolve(self, ref: str):
        """'atspi:<app>:<win>/<c>/<c>' -> Atspi düğümü."""
        if self._Atspi is None:
            return None
        path = self._parse_ref(ref)
        if not path:
            return None
        node = self._Atspi.get_desktop(0).get_child_at_index(path[0])
        for idx in path[1:]:
            if node is None:
                return None
            node = node.get_child_at_index(idx)
        return node

    def _make_ref(self, path: list[int]) -> str:
        return "atspi:" + f"{path[0]}:{path[1]}" + "".join(f"/{p}" for p in path[2:])

    # ------------------------------------------------------------ pencere
    def windows(self) -> list[Window]:
        A = self._Atspi
        if A is None:
            return []
        out: list[Window] = []
        desktop = A.get_desktop(0)
        for ai in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(ai)
            if app is None:
                continue
            aname = app.get_name() or ""
            try:
                pid = app.get_process_id()
            except Exception:
                pid = None
            for wi in range(app.get_child_count()):
                win = app.get_child_at_index(wi)
                if win is None:
                    continue
                try:
                    role = win.get_role_name() or ""
                except Exception:
                    role = ""
                if role not in ("frame", "window", "dialog", "alert"):
                    continue
                bounds = self._extents(win)
                focused = self._state(win, "ACTIVE") or self._state(win, "FOCUSED")
                out.append(Window(
                    ref=self._make_ref([ai, wi]),
                    title=(win.get_name() or "").strip(),
                    app=aname, pid=pid, focused=focused, bounds=bounds,
                ))
        return out

    # ------------------------------------------------------------ ağaç
    def tree(self, window_ref: str) -> Element:
        A = self._Atspi
        if A is None:
            return Element(ref="atspi:0:0", role=Role.FRAME, name="AT-SPI yok")
        if not window_ref:
            ws = self.windows()
            if not ws:
                return Element(ref="atspi:0:0", role=Role.FRAME, name="pencere yok")
            window_ref = ws[0].ref
        path = self._parse_ref(window_ref)
        if not path:
            return Element(ref=window_ref, role=Role.FRAME, name="geçersiz ref")
        node = A.get_desktop(0).get_child_at_index(path[0])
        for idx in path[1:]:
            node = node.get_child_at_index(idx) if node else None
        if node is None:
            return Element(ref=window_ref, role=Role.FRAME, name="pencere bulunamadı")
        # Kapalı menüler, gizli sekmeler ağaçta durur ama ekranda yoktur; onları
        # göstermek modeli görünmeyen öğeye tıklamaya iter. Pencerenin kendisi
        # SHOWING değilse (simge durumu) süzgeç kapatılır, yoksa ağaç boşalır.
        only_showing = self._state(node, "SHOWING") is True
        return self._walk(node, path, 0, only_showing)

    def _walk(self, node, path: list[int], depth: int,
              only_showing: bool = False) -> Element:
        role = self._role(node)
        el = Element(
            ref=self._make_ref(path),
            role=role,
            name=(self._safe(node, "get_name") or "").strip(),
            value=self._value(node),
            actions=self._actions(node),
            bounds=self._extents(node),
            focused=self._state(node, "FOCUSED"),
            enabled=not self._state(node, "ENABLED") is False,
            depth=depth,
        )
        if depth < self._max_depth:
            try:
                n = node.get_child_count()
            except Exception:
                n = 0
            for i in range(min(n, 400)):  # tek düğümde patlamayı önle
                ch = node.get_child_at_index(i)
                if ch is None:
                    continue
                if only_showing and self._state(ch, "SHOWING") is False:
                    continue
                child = self._walk(ch, path + [i], depth + 1, only_showing)
                # boş dekoratif düğümleri atla
                if child.name or child.actions or child.children:
                    el.children.append(child)
        return el

    # ------------------------------------------------------------ bul
    def find(self, query: str, window_ref: Optional[str] = None) -> list[Element]:
        q = (query or "").lower()
        if not q:
            return []
        trees: list[Element] = []
        if window_ref:
            trees.append(self.tree(window_ref))
        else:
            for w in self.windows():
                trees.append(self.tree(w.ref))
        hits: list[Element] = []

        def visit(e: Element) -> None:
            if q in e.name.lower() or q in (e.value or "").lower():
                if e.actions or e.role in _INTERACTIVE:
                    hits.append(e)
            for c in e.children:
                visit(c)

        for t in trees:
            visit(t)
        return hits

    # ------------------------------------------------------------ eylemler
    def click(self, ref: str) -> bool:
        node = self._resolve(ref)
        if node is None:
            return False
        a = self._iface(node, "get_action_iface")
        if a is not None:
            try:
                n = a.get_n_actions()
            except Exception:
                n = 0
            for i in range(n):
                try:
                    an = _normalize_action(a.get_action_name(i))
                except Exception:
                    an = ""
                if an == "click":
                    try:
                        a.do_action(i)
                        return True
                    except Exception:
                        pass
            if n > 0:
                try:
                    a.do_action(0)
                    return True
                except Exception:
                    pass
        # son çare: koordinata fiziksel tıkla
        b = self._extents(node)
        if b:
            return inp.click_at(b[0] + b[2] // 2, b[1] + b[3] // 2)
        return False

    def set_text(self, ref: str, text: str) -> bool:
        node = self._resolve(ref)
        if node is None:
            return False
        et = self._iface(node, "get_editable_text_iface")
        if et is not None:
            try:
                et.set_text_contents(text)
                return True
            except Exception:
                pass
        self.focus(ref)
        return inp.type_text(text)

    def focus(self, ref: str) -> bool:
        node = self._resolve(ref)
        if node is None:
            return False
        c = self._iface(node, "get_component_iface")
        if c is not None:
            try:
                return bool(c.grab_focus())
            except Exception:
                pass
        a = self._iface(node, "get_action_iface")
        if a is not None:
            for i in range(self._safe_n(a)):
                try:
                    if _normalize_action(a.get_action_name(i)) == "focus":
                        a.do_action(i)
                        return True
                except Exception:
                    pass
        return False

    def bounds(self, ref: str) -> Optional[tuple[int, int, int, int]]:
        node = self._resolve(ref)
        return self._extents(node) if node is not None else None

    def key(self, combo: str) -> bool:
        return inp.key(combo)

    def type_text(self, text: str) -> bool:
        return inp.type_text(text)

    # ------------------------------------------------------------ ekran
    def screenshot(self, path: str) -> Screenshot:
        path = os.path.abspath(path)
        tools = [
            (["spectacle", "-b", "-n", "-o", path], "spectacle"),   # KDE
            (["grim", path], "grim"),                               # wlroots
            (["gnome-screenshot", "-f", path], "gnome-screenshot"),
            (["scrot", path], "scrot"),                             # X11
        ]
        used = ""
        for cmd, nm in tools:
            if shutil.which(nm) and not used:
                try:
                    subprocess.run(cmd, timeout=15,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, check=True)
                    if os.path.exists(path) and os.path.getsize(path) > 0:
                        used = nm
                        break
                except Exception:
                    continue
        w, h = self._png_size(path)
        return Screenshot(path=path, width=w, height=h,
                          display=os.environ.get("WAYLAND_DISPLAY")
                          or os.environ.get("DISPLAY"), backend=used)

    # ------------------------------------------------------------ yardımcı
    def _iface(self, node, getter: str):
        try:
            return getattr(node, getter)()
        except Exception:
            return None

    def _role(self, node) -> Role:
        try:
            return _ROLE_MAP.get((node.get_role_name() or "").lower(), Role.UNKNOWN)
        except Exception:
            return Role.UNKNOWN

    def _safe(self, node, meth: str):
        try:
            return getattr(node, meth)()
        except Exception:
            return None

    def _safe_n(self, a) -> int:
        try:
            return a.get_n_actions()
        except Exception:
            return 0

    def _value(self, node) -> str:
        t = self._iface(node, "get_text_iface")
        if t is not None:
            try:
                n = t.get_character_count()
                if 0 < n <= 200:
                    return t.get_text(0, n)
            except Exception:
                pass
        v = self._iface(node, "get_value_iface")
        if v is not None:
            try:
                return str(v.get_current_value())
            except Exception:
                pass
        return ""

    def _actions(self, node) -> list[str]:
        a = self._iface(node, "get_action_iface")
        if a is None:
            return []
        out: list[str] = []
        for i in range(self._safe_n(a)):
            try:
                nm = _normalize_action(a.get_action_name(i))
            except Exception:
                nm = ""
            if nm and nm not in out:
                out.append(nm)
        return out

    def _extents(self, node):
        c = self._iface(node, "get_component_iface")
        if c is None:
            return None
        try:
            A = self._Atspi
            e = c.get_extents(A.CoordType.SCREEN)
            if e.width > 0 and e.height > 0:
                return (e.x, e.y, e.width, e.height)
        except Exception:
            pass
        return None

    def _state(self, node, name: str) -> Optional[bool]:
        try:
            A = self._Atspi
            st = node.get_state_set()
            return st.contains(getattr(A.StateType, name))
        except Exception:
            return None

    @staticmethod
    def _png_size(path: str) -> tuple[int, int]:
        """PNG IHDR'den genişlik/yükseklik (PIL'siz)."""
        try:
            with open(path, "rb") as f:
                head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return w, h
        except Exception:
            pass
        return 0, 0
