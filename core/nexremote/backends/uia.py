"""GUI backend — UI Automation (Windows erişilebilirlik ağacı), `comtypes` ile.

Ref biçimi:
    uia:0x1a2b            üst düzey pencere (HWND; WindowsDesktop.windows() kimliğiyle aynı)
    uia:0x1a2b:42.7.3     o penceredeki öğe (UIA RuntimeId, '.' ile birleştirilmiş)

Öğe, pencerenin HWND'sinden (ElementFromHandle) başlayıp RuntimeId koşuluyla
alt ağaçta aranır — kökten tüm masaüstünü taramaktan çok daha hızlı.

Ağaç, denetim görünümü (control view) yürüyücüsüyle gezilir; her düğümün
özellikleri bir CacheRequest ile tek çağrıda alınır (aksi halde düğüm başına
~12 süreçler arası çağrı gerekir). Süreç DPI-farkında olduğundan (windows.py)
BoundingRectangle fiziksel pikseldir — ekran görüntüsüyle aynı uzay.

Tür kitaplığı: Windows'ta System32\\UIAutomationCore.dll içinden üretilir.
Wine'ın uiautomationcore.dll'i genel tür kitaplığı taşımaz; NEXREMOTE_UIA_TLB
ile derlenmiş bir uiautomationclient.tlb gösterilebilir (bkz. tests/wine/setup.sh).
"""
from __future__ import annotations

import array
import os
import threading
from typing import Optional

from ..base import Backend
from ..models import Capability, Element, Role, Window
from ..platforms.base import Unsupported

# UIA özellik / desen kimlikleri (UIAutomationClient.h)
P_RUNTIME_ID, P_BOUNDS, P_CONTROL_TYPE, P_NAME = 30000, 30001, 30003, 30005
P_HAS_FOCUS, P_FOCUSABLE, P_ENABLED = 30008, 30009, 30010
P_HAS_INVOKE, P_HAS_TOGGLE, P_HAS_VALUE = 30031, 30041, 30043
P_HAS_EXPAND, P_HAS_SELECT_ITEM, P_HAS_LEGACY = 30028, 30036, 30090
P_VALUE, P_VALUE_READONLY, P_TOGGLE_STATE, P_LEGACY_DEFAULT = 30045, 30046, 30086, 30100
_CACHED = (P_RUNTIME_ID, P_BOUNDS, P_CONTROL_TYPE, P_NAME, P_HAS_FOCUS, P_FOCUSABLE, P_ENABLED,
           P_HAS_INVOKE, P_HAS_TOGGLE, P_HAS_VALUE, P_HAS_EXPAND, P_HAS_SELECT_ITEM,
           P_HAS_LEGACY, P_VALUE, P_VALUE_READONLY, P_TOGGLE_STATE, P_LEGACY_DEFAULT)
PAT_INVOKE, PAT_VALUE, PAT_SELECT_ITEM, PAT_TOGGLE, PAT_LEGACY = 10000, 10002, 10010, 10015, 10018
TREE_SCOPE_SUBTREE = 7

# ControlType → Role
_ROLE_MAP = {
    50000: Role.BUTTON, 50001: Role.PANEL, 50002: Role.CHECKBOX, 50003: Role.COMBOBOX,
    50004: Role.ENTRY, 50005: Role.LINK, 50006: Role.IMAGE, 50007: Role.LIST_ITEM,
    50008: Role.LIST, 50009: Role.MENU, 50010: Role.MENU, 50011: Role.MENU_ITEM,
    50012: Role.SLIDER, 50013: Role.RADIO, 50014: Role.SLIDER, 50015: Role.SLIDER,
    50016: Role.SLIDER, 50017: Role.PANEL, 50018: Role.TAB, 50019: Role.TAB,
    50020: Role.TEXT, 50021: Role.PANEL, 50022: Role.TEXT, 50023: Role.TREE,
    50024: Role.TREE_ITEM, 50025: Role.UNKNOWN, 50026: Role.PANEL, 50027: Role.PANEL,
    50028: Role.LIST, 50029: Role.LIST_ITEM, 50030: Role.ENTRY, 50031: Role.BUTTON,
    50032: Role.WINDOW, 50033: Role.PANEL, 50034: Role.PANEL, 50035: Role.BUTTON,
    50036: Role.LIST, 50037: Role.PANEL, 50038: Role.PANEL, 50039: Role.PANEL,
    50040: Role.PANEL,
}
_INTERACTIVE = {Role.BUTTON, Role.ENTRY, Role.CHECKBOX, Role.RADIO, Role.COMBOBOX,
                Role.MENU_ITEM, Role.TAB, Role.LINK, Role.LIST_ITEM, Role.TREE_ITEM, Role.SLIDER}
_MAX_DEPTH, _MAX_CHILDREN, _MAX_NODES = 12, 400, 5000


def parse_ref(ref: str) -> tuple[int, Optional[tuple[int, ...]]]:
    """'uia:0x1a2b[:1.2.3]' (ya da win:0x1a2b / 0x1a2b) → (hwnd, runtime_id|None)."""
    body = (ref or "").strip()
    for pre in ("uia:", "win:"):
        if body.startswith(pre):
            body = body[len(pre):]
            break
    hw, _, rid = body.partition(":")
    try:
        hwnd = int(hw, 16) if hw.lower().startswith("0x") else int(hw)
        runtime = tuple(int(x) for x in rid.split(".")) if rid else None
    except ValueError:
        raise ValueError(f"geçersiz UIA ref: {ref!r} (uia:0xHWND[:RUNTIMEID])") from None
    if hwnd <= 0 or (runtime is not None and not runtime):
        raise ValueError(f"geçersiz UIA ref: {ref!r}")
    return hwnd, runtime


def make_ref(hwnd: int, runtime: Optional[tuple[int, ...]] = None) -> str:
    return f"uia:0x{hwnd:x}" + (":" + ".".join(str(int(i)) for i in runtime) if runtime else "")


def role_for(control_type: Optional[int], depth: int) -> Role:
    role = _ROLE_MAP.get(int(control_type or 0), Role.UNKNOWN)
    return Role.FRAME if role is Role.WINDOW and depth == 0 else role


_mod_lock = threading.Lock()
_mod = None


def _uia_module():
    """comtypes ile UIAutomationClient sarmalayıcısı (bir kez üretilir, önbelleğe alınır)."""
    global _mod
    with _mod_lock:
        if _mod is not None:
            return _mod
        import comtypes.client
        src = os.environ.get("NEXREMOTE_UIA_TLB") or os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "UIAutomationCore.dll")
        mod = comtypes.client.GetModule(src)
        if not hasattr(mod, "IUIAutomation"):
            raise Unsupported(f"{src} UIAutomationClient tür kitaplığını içermiyor (Wine?) — "
                              "NEXREMOTE_UIA_TLB ile uiautomationclient.tlb gösterin")
        _mod = mod
        return mod


class _Ctx(threading.local):
    """COM nesneleri iş parçacığına bağlıdır (STA): her iş parçacığına ayrı örnek."""
    uia = None
    cache = None
    walker = None
    walker_error: Optional[str] = None


class UiaBackend(Backend):
    name = "gui"
    kind = "gui"

    def __init__(self, desktop=None) -> None:
        self._desktop = desktop          # WindowsDesktop: pencere listesi + yazma yedeği
        self._ctx = _Ctx()

    # ------------------------------------------------------------ COM kurulumu
    def _uia(self):
        c = self._ctx
        if c.uia is not None:
            return c.uia
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except OSError:
            pass   # RPC_E_CHANGED_MODE: iş parçacığı başka kipte zaten başlatılmış — kullanılabilir
        mod = _uia_module()
        c.uia = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
        cr = c.uia.CreateCacheRequest()
        for pid in _CACHED:
            cr.AddProperty(pid)
        c.cache = cr
        try:
            c.walker = c.uia.ControlViewWalker
        except comtypes.COMError as e:
            c.walker, c.walker_error = None, str(e)    # Wine: E_NOTIMPL
        return c.uia

    def _desk(self):
        if self._desktop is None:
            from ..platforms.windows import WindowsDesktop
            self._desktop = WindowsDesktop()
        return self._desktop

    # ------------------------------------------------------------ capability
    def capability(self) -> Capability:
        try:
            import comtypes  # noqa: F401
        except ImportError:
            return Capability(self.name, False, "UI Automation için comtypes gerekli",
                              ["pip install comtypes"])
        try:
            self._uia()
        except Unsupported as e:
            return Capability(self.name, False, str(e))
        except (OSError, AttributeError) as e:
            return Capability(self.name, False, f"UI Automation başlatılamadı: {e}")
        if self._ctx.walker is None:
            return Capability(self.name, False, "UI Automation kısmi: denetim ağacı gezilemiyor "
                              f"({self._ctx.walker_error}) — yalnızca pencere düğümleri")
        return Capability(self.name, True, "UI Automation: isimle bul/tıkla (Win32, WPF, "
                          "WinUI, Qt, Chromium/Electron)")

    # ------------------------------------------------------------ özellik okuma
    @staticmethod
    def _prop(el, pid: int, default=None):
        """Önbellekteki özellik. Sağlayıcı desteklemiyorsa (COMError) varsayılan:
        özellikler zenginleştirmedir, tek eksik alan ağacı düşürmemeli."""
        import comtypes
        try:
            v = el.GetCachedPropertyValue(pid)
        except comtypes.COMError:
            return default
        return default if v is None else v

    def _cached(self, el):
        """Öğeyi özellikleri önbelleğe alınmış haliyle döndür."""
        self._uia()
        return el.BuildUpdatedCache(self._ctx.cache)

    @staticmethod
    def _bounds_of(v) -> Optional[tuple[int, int, int, int]]:
        try:
            x, y, w, h = (int(round(float(a))) for a in v)
        except (TypeError, ValueError):
            return None
        return (x, y, w, h) if w > 0 and h > 0 else None

    def _element(self, el, hwnd: int, depth: int, root: bool) -> Element:
        p = self._prop
        actions: list[str] = []
        if p(el, P_HAS_INVOKE, False):
            actions.append("click")
        if p(el, P_HAS_TOGGLE, False):
            actions.append("toggle")
        has_value = bool(p(el, P_HAS_VALUE, False))
        if has_value and not p(el, P_VALUE_READONLY, True):
            actions.append("set_text")
        if p(el, P_HAS_EXPAND, False):
            actions.append("expand")
        if p(el, P_HAS_SELECT_ITEM, False):
            actions.append("select")
        if "click" not in actions and p(el, P_HAS_LEGACY, False) and p(el, P_LEGACY_DEFAULT, ""):
            actions.append("click")
        if p(el, P_FOCUSABLE, False):
            actions.append("focus")
        value = ""
        if has_value:
            value = str(p(el, P_VALUE, ""))[:200]
        elif p(el, P_HAS_TOGGLE, False):
            value = str(p(el, P_TOGGLE_STATE, ""))     # 0 kapalı, 1 açık, 2 belirsiz
        rid = p(el, P_RUNTIME_ID)
        ref = make_ref(hwnd) if root or not rid else make_ref(hwnd, tuple(rid))
        return Element(ref=ref, role=role_for(p(el, P_CONTROL_TYPE, 0), depth),
                       name=str(p(el, P_NAME, "") or "").strip(), value=value, actions=actions,
                       bounds=self._bounds_of(p(el, P_BOUNDS)),
                       focused=bool(p(el, P_HAS_FOCUS, False)),
                       enabled=bool(p(el, P_ENABLED, True)), depth=depth)

    # ------------------------------------------------------------ pencere
    def windows(self) -> list[Window]:
        return [Window(ref=make_ref(int(w.id, 16)), title=w.title, app=w.app, pid=w.pid,
                       focused=w.active, bounds=(w.x, w.y, w.w, w.h))
                for w in self._desk().windows()]

    def _resolve(self, ref: str):
        """ref → önbellekli UIA öğesi; pencere/öğe artık yoksa None."""
        import comtypes
        hwnd, rid = parse_ref(ref)
        uia = self._uia()
        try:
            win = uia.ElementFromHandle(hwnd)
        except comtypes.COMError:
            return None                            # HWND geçersiz / pencere kapandı
        if not win:
            return None
        if rid is None:
            return self._cached(win)
        cond = uia.CreatePropertyCondition(P_RUNTIME_ID, array.array("l", rid))
        el = win.FindFirstBuildCache(TREE_SCOPE_SUBTREE, cond, self._ctx.cache)
        return el if el else None

    # ------------------------------------------------------------ ağaç
    def tree(self, window_ref: str) -> Element:
        if not window_ref:
            w = self._desk().active_window()
            if w is None:
                ws = self._desk().windows()
                if not ws:
                    raise RuntimeError("ekranda pencere yok")
                w = ws[0]
            window_ref = make_ref(int(w.id, 16))
        hwnd, _ = parse_ref(window_ref)
        el = self._resolve(window_ref)
        if el is None:
            raise RuntimeError(f"pencere/öğe bulunamadı: {window_ref}")
        budget = [_MAX_NODES]
        return self._walk(el, hwnd, 0, root=parse_ref(window_ref)[1] is None, budget=budget)

    def _walk(self, el, hwnd: int, depth: int, root: bool, budget: list[int]) -> Element:
        import comtypes
        node = self._element(el, hwnd, depth, root)
        budget[0] -= 1
        walker, cr = self._ctx.walker, self._ctx.cache
        if walker is None or depth >= _MAX_DEPTH:
            return node
        try:
            ch = walker.GetFirstChildElementBuildCache(el, cr)
            n = 0
            while ch and n < _MAX_CHILDREN and budget[0] > 0:
                child = self._walk(ch, hwnd, depth + 1, False, budget)
                if child.name or child.actions or child.children:   # boş dekoratif düğümü atla
                    node.children.append(child)
                n += 1
                ch = walker.GetNextSiblingElementBuildCache(ch, cr)
        except comtypes.COMError:
            # gezinti sırasında kapanan/yeniden oluşturulan öğe (UIA_E_ELEMENTNOTAVAILABLE):
            # o düğüme kadar okunan çocuklar korunur
            pass
        return node

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
                    continue                       # listeleme ile gezinti arasında kapandı
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
    def _pattern(self, el, pattern_id: int, iface_name: str):
        import comtypes
        try:
            unk = el.GetCurrentPattern(pattern_id)
        except comtypes.COMError:
            return None          # sağlayıcı bu deseni sorgulatmıyor → sonraki yönteme geç
        if not unk:
            return None
        return unk.QueryInterface(getattr(_uia_module(), iface_name))

    def click(self, ref: str) -> bool:
        import comtypes
        el = self._resolve(ref)
        if el is None:
            return False
        for pid, iface, method in ((PAT_INVOKE, "IUIAutomationInvokePattern", "Invoke"),
                                   (PAT_TOGGLE, "IUIAutomationTogglePattern", "Toggle"),
                                   (PAT_SELECT_ITEM, "IUIAutomationSelectionItemPattern", "Select"),
                                   (PAT_LEGACY, "IUIAutomationLegacyIAccessiblePattern",
                                    "DoDefaultAction")):
            pat = self._pattern(el, pid, iface)
            if pat is None:
                continue
            if pid == PAT_LEGACY and not pat.CurrentDefaultAction:
                continue
            try:
                getattr(pat, method)()
            except comtypes.COMError as e:
                raise RuntimeError(f"UIA {method} başarısız: {e}") from None
            return True
        return False          # desen yok: facade öğenin ortasına gerçek tık yapar

    def set_text(self, ref: str, text: str) -> bool:
        import comtypes
        el = self._resolve(ref)
        if el is None:
            return False
        pat = self._pattern(el, PAT_VALUE, "IUIAutomationValuePattern")
        if pat is not None and not pat.CurrentIsReadOnly:
            try:
                pat.SetValue(text)
            except comtypes.COMError as e:
                raise RuntimeError(f"UIA SetValue başarısız: {e}") from None
            return True
        # ValuePattern yok (ör. bazı zengin metin denetimleri): odakla, seç, yaz
        if self._desktop is None or not self.focus(ref):
            return False
        self._desktop.key("ctrl+a")
        self._desktop.type_text(text)
        return True

    def focus(self, ref: str) -> bool:
        import comtypes
        el = self._resolve(ref)
        if el is None:
            return False
        try:
            el.SetFocus()
        except comtypes.COMError as e:
            raise RuntimeError(f"UIA SetFocus başarısız: {e}") from None
        return True

    def bounds(self, ref: str) -> Optional[tuple[int, int, int, int]]:
        el = self._resolve(ref)
        if el is None:
            return None
        return self._bounds_of(self._prop(el, P_BOUNDS))

    def close(self) -> None:
        self._ctx = _Ctx()
