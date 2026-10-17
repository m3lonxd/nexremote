"""GUI backend — macOS Accessibility (AXUIElement), yalnızca ctypes ile.

Ref biçimi:
    ax:<pid>                         uygulama öğesi
    ax:<pid>:w<CGWindowID>           üst düzey pencere (MacDesktop.windows() kimliğiyle aynı)
    ax:<pid>:<n>                     pencere, AXWindows dizisindeki sırası (CGWindowID
                                     eşlenemediğinde yedek; pencere sırası değişince kayar)
    ax:<pid>:w4521.0.3.1             o penceredeki öğe: AXChildren indisleri yolu

Öğeler AX'ta kalıcı kimlik taşımaz; ref, pencereden başlayan çocuk indisleri
yoludur. Arayüz yeniden kurulursa (sekme değişimi, liste yenilenmesi) ref başka
bir öğeye düşebilir — eylemden önce `tree`/`find` yenilenmelidir.

Ağaç gezintisinde her düğümün öznitelikleri AXUIElementCopyMultipleAttributeValues
ile tek süreçler arası çağrıda alınır. Koordinatlar Quartz ekran noktasıdır
(MacDesktop ile aynı uzay). Chromium/Electron uygulamaları ağaçlarını yalnızca
istenince yayınlar: ilk gezintide uygulamaya AXManualAccessibility = true yazılır.
"""
from __future__ import annotations

import ctypes
import re
import sys
from typing import Optional

from ..base import Backend
from ..models import Capability, Element, Role, Window
from ..platforms.base import Unsupported

_ROLE_MAP = {
    "AXApplication": Role.FRAME, "AXWindow": Role.WINDOW, "AXSheet": Role.DIALOG,
    "AXDrawer": Role.PANEL, "AXButton": Role.BUTTON, "AXMenuButton": Role.BUTTON,
    "AXDisclosureTriangle": Role.BUTTON, "AXColorWell": Role.BUTTON, "AXDockItem": Role.BUTTON,
    "AXTextField": Role.ENTRY, "AXTextArea": Role.ENTRY, "AXDateField": Role.ENTRY,
    "AXTimeField": Role.ENTRY, "AXCheckBox": Role.CHECKBOX, "AXRadioButton": Role.RADIO,
    "AXMenuItem": Role.MENU_ITEM, "AXMenuBarItem": Role.MENU_ITEM, "AXMenu": Role.MENU,
    "AXMenuBar": Role.MENU, "AXStaticText": Role.TEXT, "AXHeading": Role.TEXT,
    "AXHelpTag": Role.TEXT, "AXLink": Role.LINK, "AXGroup": Role.PANEL,
    "AXRadioGroup": Role.PANEL, "AXScrollArea": Role.PANEL, "AXSplitGroup": Role.PANEL,
    "AXSplitter": Role.PANEL, "AXToolbar": Role.PANEL, "AXLayoutArea": Role.PANEL,
    "AXLayoutItem": Role.PANEL, "AXWebArea": Role.PANEL, "AXMatte": Role.PANEL,
    "AXRuler": Role.PANEL, "AXColumn": Role.PANEL, "AXList": Role.LIST, "AXTable": Role.LIST,
    "AXGrid": Role.LIST, "AXRow": Role.LIST_ITEM, "AXCell": Role.LIST_ITEM,
    "AXOutline": Role.TREE, "AXBrowser": Role.TREE, "AXTabGroup": Role.TAB,
    "AXComboBox": Role.COMBOBOX, "AXPopUpButton": Role.COMBOBOX, "AXSlider": Role.SLIDER,
    "AXIncrementor": Role.SLIDER, "AXScrollBar": Role.SLIDER, "AXValueIndicator": Role.SLIDER,
    "AXProgressIndicator": Role.SLIDER, "AXLevelIndicator": Role.SLIDER,
    "AXRelevanceIndicator": Role.SLIDER, "AXImage": Role.IMAGE,
}
# alt rol rolü inceltir
_SUBROLE_MAP = {
    "AXTabButton": Role.TAB, "AXOutlineRow": Role.TREE_ITEM, "AXDialog": Role.DIALOG,
    "AXSystemDialog": Role.DIALOG, "AXFloatingWindow": Role.WINDOW, "AXSwitch": Role.CHECKBOX,
    "AXToggle": Role.CHECKBOX, "AXSearchField": Role.ENTRY, "AXSecureTextField": Role.ENTRY,
    "AXTextLink": Role.LINK,
}
_TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXDateField", "AXTimeField"}
_NAME_FROM_VALUE = {"AXStaticText", "AXHeading"}
_INTERACTIVE = {Role.BUTTON, Role.ENTRY, Role.CHECKBOX, Role.RADIO, Role.COMBOBOX,
                Role.MENU_ITEM, Role.TAB, Role.LINK, Role.LIST_ITEM, Role.TREE_ITEM, Role.SLIDER}
_NODE_ATTRS = ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXEnabled",
               "AXFocused", "AXPosition", "AXSize", "AXPlaceholderValue", "AXChildren")
_MAX_DEPTH, _MAX_CHILDREN, _MAX_NODES = 20, 400, 4000

_REF = re.compile(r"^ax:(\d+)(?::(w?\d+)((?:\.\d+)*))?$")


def parse_ref(ref: str) -> tuple[int, Optional[str], tuple[int, ...]]:
    """'ax:<pid>[:<pencere>[.<i>…]]' → (pid, pencere belirteci|None, çocuk yolu).
    Pencere belirteci 'w<CGWindowID>' ya da AXWindows indisi ('0', '1'…)."""
    m = _REF.match((ref or "").strip())
    if not m:
        raise ValueError(f"geçersiz AX ref: {ref!r} (ax:PID[:wPENCERE[.i.j…]])")
    pid = int(m.group(1))
    win = m.group(2)
    path = tuple(int(p) for p in m.group(3).split(".")[1:]) if m.group(3) else ()
    if pid <= 0 or (win is not None and win.startswith("w") and int(win[1:]) <= 0):
        raise ValueError(f"geçersiz AX ref: {ref!r}")
    return pid, win, path


def make_ref(pid: int, win: Optional[str] = None, path: tuple[int, ...] = ()) -> str:
    if win is None:
        if path:
            raise ValueError("pencere belirteci olmadan çocuk yolu olamaz")
        return f"ax:{int(pid)}"
    return f"ax:{int(pid)}:{win}" + "".join(f".{int(i)}" for i in path)


def role_for(ax_role: Optional[str], subrole: Optional[str] = None, depth: int = 0) -> Role:
    role = _SUBROLE_MAP.get(subrole or "") or _ROLE_MAP.get(ax_role or "", Role.UNKNOWN)
    if role is Role.WINDOW and depth == 0:
        return Role.FRAME
    return role


def node_name(vals: dict) -> str:
    """Erişilebilir ad: AXTitle → AXDescription → (metin öğesinde) AXValue → yer tutucu."""
    for key in ("AXTitle", "AXDescription"):
        v = vals.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    v = vals.get("AXValue")
    if vals.get("AXRole") in _NAME_FROM_VALUE and isinstance(v, str) and v.strip():
        return v.strip()
    v = vals.get("AXPlaceholderValue")
    return v.strip() if isinstance(v, str) else ""


def node_value(vals: dict) -> str:
    if vals.get("AXRole") in _NAME_FROM_VALUE:
        return ""                                   # değer zaten ad olarak gösteriliyor
    v = vals.get("AXValue")
    if v is None or isinstance(v, (tuple, list, dict)):
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)[:200]


def node_actions(ax_role: Optional[str], ax_actions: list[str], value_settable: bool,
                 role: Role, enabled: bool) -> list[str]:
    acts: list[str] = []
    if "AXPress" in ax_actions:
        acts.append("click")
        if role in (Role.CHECKBOX, Role.RADIO):
            acts.append("toggle")
    if "AXShowMenu" in ax_actions and ax_role in ("AXPopUpButton", "AXComboBox", "AXMenuButton"):
        acts.append("expand")
    if value_settable:
        acts.append("set_text")
    if role in (Role.LIST_ITEM, Role.TREE_ITEM) and "AXPick" in ax_actions:
        acts.append("select")
    if enabled and role in _INTERACTIVE:
        acts.append("focus")
    return acts


class AxBackend(Backend):
    name = "gui"
    kind = "gui"

    def __init__(self, desktop=None) -> None:
        self._desktop = desktop          # MacDesktop: pencere listesi + tık/yazma yedeği
        self._q = None
        self._names = None               # _NODE_ATTRS için sahipli CFArray
        self._manual: set[int] = set()   # AXManualAccessibility yazılmış pid'ler

    # ------------------------------------------------------------ kurulum
    def _desk(self):
        if self._desktop is None:
            from ..platforms.macos import MacDesktop
            self._desktop = MacDesktop()
        return self._desktop

    def _quartz(self):
        if self._q is None:
            self._q = self._desk().q
        return self._q

    def _require(self):
        q = self._quartz()
        if not q.ax_trusted():
            from ..platforms.macos import AX_PERMISSION
            raise Unsupported(AX_PERMISSION)
        return q

    def capability(self) -> Capability:
        if sys.platform != "darwin" and self._desktop is None:
            return Capability(self.name, False, "Accessibility (AX) yalnızca macOS'ta")
        try:
            q = self._quartz()
        except Unsupported as e:
            return Capability(self.name, False, str(e))
        if not q.ax_trusted():
            from ..platforms.macos import AX_PERMISSION
            return Capability(self.name, False, AX_PERMISSION,
                              ["Erişilebilirlik izni (Sistem Ayarları → Gizlilik ve Güvenlik → "
                               "Erişilebilirlik)"])
        return Capability(self.name, True, "macOS Accessibility: isimle bul/tıkla (AppKit, "
                          "SwiftUI, Catalyst, Chromium/Electron, Qt)")

    # ------------------------------------------------------------ pencere
    def _win_token(self, a, index: int) -> str:
        return f"w{a.cgid}" if a.cgid else str(index)

    def windows(self) -> list[Window]:
        q = self._require()
        out: list[Window] = []
        by_pid: dict[int, list] = {}
        for w in self._desk().windows():
            if w.pid:
                by_pid.setdefault(w.pid, []).append(w)
        from ..platforms.macos import match_window
        for pid, wins in by_pid.items():
            try:
                axw = q.ax_windows(pid)
            except RuntimeError:
                continue                         # uygulama yanıt vermiyor: diğerlerini engellemesin
            try:
                cands = [(a.cgid, a.title, a.bounds) for a in axw]
                for w in wins:
                    i = match_window(cands, int(w.id), w.title, (w.x, w.y, w.w, w.h))
                    if i is None:
                        continue                 # AX'ta karşılığı yok (ör. kalkan/yer paylaşımı)
                    a = axw[i]
                    out.append(Window(ref=make_ref(pid, self._win_token(a, i)),
                                      title=a.title or w.title, app=w.app, pid=pid,
                                      focused=w.active, bounds=a.bounds or (w.x, w.y, w.w, w.h)))
            finally:
                q.release_all(a.el for a in axw)
        return out

    def _window_el(self, q, pid: int, win: str) -> Optional[int]:
        axw = q.ax_windows(pid)
        keep = None
        if win.startswith("w"):
            cgid = int(win[1:])
            keep = next((a.el for a in axw if a.cgid == cgid), None)
        elif int(win) < len(axw):
            keep = axw[int(win)].el
        q.release_all(a.el for a in axw if a.el != keep)
        return keep

    def _resolve(self, ref: str) -> Optional[int]:
        """ref → sahipli AXUIElementRef (çağıran CFRelease eder); öğe yoksa None."""
        q = self._require()
        pid, win, path = parse_ref(ref)
        if win is None:
            return q.ax_app(pid)
        el = self._window_el(q, pid, win)
        for i in path:
            if el is None:
                return None
            kids = q.ax_elements(el, "AXChildren")
            q.CFRelease(el)
            el = kids[i] if i < len(kids) else None
            q.release_all(k for k in kids if k != el)
        return el

    def _ref_for(self, w) -> str:
        """MacDesktop WinInfo → ax: pencere ref'i."""
        q = self._require()
        from ..platforms.macos import match_window
        axw = q.ax_windows(w.pid)
        try:
            i = match_window([(a.cgid, a.title, a.bounds) for a in axw], int(w.id), w.title,
                             (w.x, w.y, w.w, w.h))
            if i is None:
                raise RuntimeError(f"'{w.title or w.app}' penceresi erişilebilirlik ağacında yok")
            return make_ref(w.pid, self._win_token(axw[i], i))
        finally:
            q.release_all(a.el for a in axw)

    # ------------------------------------------------------------ öznitelikler
    def _attr_names(self, q) -> int:
        if self._names is None:
            ptrs = (ctypes.c_void_p * len(_NODE_ATTRS))(*[q.cfstr(n) for n in _NODE_ATTRS])
            arr = q.CFArrayCreate(None, ptrs, len(_NODE_ATTRS),
                                  q.const_addr(q.cf, "kCFTypeArrayCallBacks"))
            if not arr:
                raise RuntimeError("CFArrayCreate başarısız")
            self._names = arr
        return self._names

    def _fetch(self, q, el) -> Optional[tuple[dict, list[int], Optional[int]]]:
        """Düğüm öznitelikleri tek çağrıda → (değerler, bırakılacak sahipli ref'ler,
        AXChildren dizisi (ödünç)). Öğe artık yoksa None."""
        from ..platforms.macos import (AX_API_DISABLED, AX_CANNOT_COMPLETE, AX_INVALID_ELEMENT,
                                       AX_SUCCESS)
        q._ax_setup()
        out = ctypes.c_void_p()
        err = q.AXUIElementCopyMultipleAttributeValues(el, self._attr_names(q), 0,
                                                       ctypes.byref(out))
        if err == AX_API_DISABLED:
            raise Unsupported(self._perm())
        if err == AX_INVALID_ELEMENT:
            return None
        if err == AX_CANNOT_COMPLETE:
            # zaman aşımı: tek tek sormak her öznitelikte yeniden beklemek demek
            raise RuntimeError("uygulama erişilebilirlik isteklerine yanıt vermiyor (meşgul/donmuş)")
        vals: dict = {}
        if err == AX_SUCCESS and out.value:
            children = None
            n = min(q.CFArrayGetCount(out.value), len(_NODE_ATTRS))
            for i in range(n):
                v = q.CFArrayGetValueAtIndex(out.value, i)
                if _NODE_ATTRS[i] == "AXChildren":
                    children = v if q.is_type(v, "arr") else None
                else:
                    vals[_NODE_ATTRS[i]] = q.to_py(v)
            return vals, [out.value], children
        # yedek: öznitelik öznitelik (bazı sağlayıcılar çoklu sorguyu desteklemez)
        for name in _NODE_ATTRS[:-1]:
            vals[name] = q.ax_get(el, name)
        _, kids = q.ax_copy(el, "AXChildren")
        if kids and not q.is_type(kids, "arr"):
            q.CFRelease(kids)
            kids = None
        return vals, [kids] if kids else [], kids

    @staticmethod
    def _perm() -> str:
        from ..platforms.macos import AX_PERMISSION
        return AX_PERMISSION

    def _element(self, q, el, vals: dict, ref: str, depth: int) -> Element:
        ax_role = vals.get("AXRole") if isinstance(vals.get("AXRole"), str) else None
        subrole = vals.get("AXSubrole") if isinstance(vals.get("AXSubrole"), str) else None
        role = role_for(ax_role, subrole, depth)
        enabled = vals.get("AXEnabled") is not False
        settable = ax_role in _TEXT_ROLES and q.ax_settable(el, "AXValue")
        from ..platforms.macos import frame_of
        return Element(ref=ref, role=role, name=node_name(vals)[:300], value=node_value(vals),
                       actions=node_actions(ax_role, q.ax_actions(el), settable, role, enabled),
                       bounds=frame_of(vals.get("AXPosition"), vals.get("AXSize")),
                       focused=vals.get("AXFocused") is True, enabled=enabled, depth=depth)

    # ------------------------------------------------------------ ağaç
    def tree(self, window_ref: str) -> Element:
        q = self._require()
        if not window_ref:
            w = self._desk().active_window()
            if w is None:
                ws = self._desk().windows()
                if not ws:
                    raise RuntimeError("ekranda pencere yok")
                w = ws[0]
            window_ref = self._ref_for(w)
        pid, win, path = parse_ref(window_ref)
        if pid not in self._manual:
            # Electron/Chromium ağacını yalnızca istenince kurar; diğer uygulamalar
            # bu özniteliği desteklemez (AttributeUnsupported) — beklenen, sonuç önemsiz.
            app = q.ax_app(pid)
            try:
                q.ax_set_bool(app, "AXManualAccessibility", True)
            finally:
                q.CFRelease(app)
            self._manual.add(pid)
        el = self._resolve(window_ref)
        if el is None:
            raise RuntimeError(f"pencere/öğe bulunamadı: {window_ref}")
        try:
            node = self._walk(q, el, pid, win, path, 0, [_MAX_NODES])
        finally:
            q.CFRelease(el)
        if node is None:
            raise RuntimeError(f"öğe artık yok: {window_ref}")
        return node

    def _walk(self, q, el, pid: int, win: Optional[str], path: tuple[int, ...], depth: int,
              budget: list[int]) -> Optional[Element]:
        fetched = self._fetch(q, el)
        if fetched is None:
            return None
        vals, owned, children = fetched
        try:
            node = self._element(q, el, vals, make_ref(pid, win, path), depth)
            budget[0] -= 1
            if children and win is not None and depth < _MAX_DEPTH:
                n = min(q.CFArrayGetCount(children), _MAX_CHILDREN)
                for i in range(n):
                    if budget[0] <= 0:
                        break
                    ch = q.CFArrayGetValueAtIndex(children, i)     # ödünç: dizi yaşadıkça geçerli
                    if not ch:
                        continue
                    try:
                        child = self._walk(q, ch, pid, win, path + (i,), depth + 1, budget)
                    except RuntimeError:
                        break        # uygulama gezinti sırasında yanıtsız kaldı: okunanlar korunur
                    if child and (child.name or child.actions or child.children):
                        node.children.append(child)            # boş dekoratif düğümü atla
            elif children and win is None:
                # uygulama kökü: çocuklar pencereler/menü çubuğu — yolu pencere belirteci olmadan
                # ifade edilemez; pencereleri ayrı düğüm olarak ver
                for i, a in enumerate(q.ax_windows(pid)):
                    try:
                        child = self._walk(q, a.el, pid, self._win_token(a, i), (), depth + 1,
                                           budget)
                    finally:
                        q.CFRelease(a.el)
                    if child:
                        node.children.append(child)
            return node
        finally:
            q.release_all(owned)

    # ------------------------------------------------------------ bul
    def find(self, query: str, window_ref: Optional[str] = None) -> list[Element]:
        q = (query or "").lower()
        if not q:
            return []
        trees = [self.tree(window_ref)] if window_ref else []
        if not window_ref:
            for w in self.windows():
                try:
                    trees.append(self.tree(w.ref))
                except RuntimeError:
                    continue                     # listeleme ile gezinti arasında kapandı
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
        from ..platforms.macos import AX_INVALID_ELEMENT
        q = self._require()
        el = self._resolve(ref)
        if el is None:
            return False
        try:
            if "AXPress" in q.ax_actions(el):
                err = q.ax_perform(el, "AXPress")
                if err == AX_INVALID_ELEMENT:
                    return False
                q.ax_check(err, "AXPress")
                return True
            b = q.ax_frame(el)
        finally:
            q.CFRelease(el)
        if b is None or self._desktop is None:
            return False
        self._desktop.click(b[0] + b[2] / 2, b[1] + b[3] / 2)   # eylemsiz öğe: ortasına gerçek tık
        return True

    def set_text(self, ref: str, text: str) -> bool:
        q = self._require()
        el = self._resolve(ref)
        if el is None:
            return False
        try:
            if q.ax_settable(el, "AXValue"):
                s = q.new_cfstr(text)
                try:
                    q.ax_check(q.ax_set(el, "AXValue", s), "AXValue yazma")
                finally:
                    q.CFRelease(s)
                return True
        finally:
            q.CFRelease(el)
        # AXValue yazılamıyor (ör. web içerik düzenleyicileri): odakla, tümünü seç, yaz
        if self._desktop is None or not self.focus(ref):
            return False
        self._desktop.key("cmd+a")
        self._desktop.type_text(text)
        return True

    def focus(self, ref: str) -> bool:
        q = self._require()
        pid, _, _ = parse_ref(ref)
        el = self._resolve(ref)
        if el is None:
            return False
        try:
            # klavye odağı yalnızca öndeki uygulamada anlamlı
            app = q.ax_app(pid)
            try:
                q.ax_check(q.ax_set_bool(app, "AXFrontmost", True), "AXFrontmost")
            finally:
                q.CFRelease(app)
            if q.ax_get(el, "AXRole") == "AXWindow":
                q.ax_check(q.ax_perform(el, "AXRaise"), "AXRaise")
                q.ax_set_bool(el, "AXMain", True)    # desteklemeyen pencere olabilir: AXRaise yeter
                return True
            if not q.ax_settable(el, "AXFocused"):
                return False
            q.ax_check(q.ax_set_bool(el, "AXFocused", True), "AXFocused")
            return True
        finally:
            q.CFRelease(el)

    def bounds(self, ref: str) -> Optional[tuple[int, int, int, int]]:
        q = self._require()
        el = self._resolve(ref)
        if el is None:
            return None
        try:
            return q.ax_frame(el)
        finally:
            q.CFRelease(el)

    def close(self) -> None:
        if self._names is not None and self._q is not None:
            self._q.CFRelease(self._names)
            self._names = None
