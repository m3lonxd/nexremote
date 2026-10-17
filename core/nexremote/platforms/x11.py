"""X11 sağlayıcısı — yalnızca python-xlib (harici araç yok).

    pencereler   EWMH (_NET_CLIENT_LIST); pencere yöneticisi yoksa kök ağaç
    giriş        XTEST (gerçek fare + klavye; izin istemez)
    yazma        XTEST + boş tuş koduna geçici keysym eşlemesi (Unicode, Türkçe)
    ekran        kök pencerenin ZPixmap görüntüsü → PNG

Koordinatlar kök pencere koordinatıdır (çoklu monitör tek sanal ekrandır,
orijin 0,0). Wayland oturumunda XWayland yalnızca X uygulamalarını gösterdiği
için bu sağlayıcı yalnızca gerçek X11 oturumunda seçilir.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from ..models import Screenshot
from ._png import bgrx_to_rgb, write_png
from .base import Unsupported, WinInfo, parse_combo

# ortak tuş adı → X keysym adı
_KEYSYM_NAMES = {
    "ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "super": "Super_L",
    "altgr": "ISO_Level3_Shift",
    "enter": "Return", "tab": "Tab", "esc": "Escape", "backspace": "BackSpace",
    "delete": "Delete", "insert": "Insert", "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next", "up": "Up", "down": "Down",
    "left": "Left", "right": "Right", "space": "space", "menu": "Menu",
    "capslock": "Caps_Lock", "printscreen": "Print", "pause": "Pause",
    **{f"f{i}": f"F{i}" for i in range(1, 25)},
}
_BUTTONS = {"left": 1, "middle": 2, "right": 3}


def _char_keysym(ch: str) -> int:
    """Tek karakterin X keysym'i (Latin-1 doğrudan, diğerleri Unicode keysym)."""
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x01000000 | cp


def available() -> tuple[bool, str]:
    if not os.environ.get("DISPLAY"):
        return False, "DISPLAY yok"
    try:
        import Xlib.display  # noqa: F401
        from Xlib.ext import xtest  # noqa: F401
    except ImportError:
        return False, "python-xlib gerekli (pip install python-xlib)"
    try:
        d = _open()
    except Exception as e:
        return False, f"X sunucusuna bağlanılamadı: {e}"
    try:
        if not d.has_extension("XTEST"):
            return False, "X sunucusunda XTEST eklentisi yok"
    finally:
        d.close()
    return True, f"X11 ({os.environ['DISPLAY']}): XTEST fare+klavye"


def _open():
    import Xlib.display
    return Xlib.display.Display(os.environ.get("DISPLAY"))


class X11:
    """Tek X bağlantısı üzerinden pencere + giriş + ekran."""

    def __init__(self) -> None:
        self._d = None
        self._atoms: dict[str, int] = {}

    # ------------------------------------------------------------ altyapı
    @property
    def d(self):
        if self._d is None:
            try:
                self._d = _open()
            except Exception as e:
                raise Unsupported(f"X sunucusuna bağlanılamadı ({os.environ.get('DISPLAY')}): {e}")
        return self._d

    @property
    def root(self):
        return self.d.screen().root

    def atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.d.intern_atom(name)
        return self._atoms[name]

    def _prop(self, win, name: str, type_=0):
        try:
            p = win.get_full_property(self.atom(name), type_)
        except Exception:
            return None  # pencere bu arada kapanmış olabilir (BadWindow)
        return p.value if p is not None else None

    def _wm_supports(self, name: str) -> bool:
        sup = self._prop(self.root, "_NET_SUPPORTED")
        return sup is not None and self.atom(name) in list(sup)

    def _has_wm(self) -> bool:
        return self._prop(self.root, "_NET_SUPPORTING_WM_CHECK") is not None

    def close(self) -> None:
        if self._d is not None:
            try:
                self._d.close()
            finally:
                self._d = None

    # ------------------------------------------------------------ pencere
    def _title(self, win) -> str:
        v = self._prop(win, "_NET_WM_NAME", self.atom("UTF8_STRING"))
        if v:
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        try:
            name = win.get_wm_name()
        except Exception:
            name = None
        if isinstance(name, bytes):
            return name.decode("latin-1")
        return name or ""

    def _client_ids(self) -> list[int]:
        v = self._prop(self.root, "_NET_CLIENT_LIST")
        if v is not None:
            return [int(x) for x in v]
        # pencere yöneticisi yok: haritalanmış, adı olan üst düzey pencereler
        from Xlib import X
        out = []
        for ch in self.root.query_tree().children:
            try:
                if ch.get_attributes().map_state == X.IsViewable and self._title(ch):
                    out.append(ch.id)
            except Exception:
                continue
        return out

    def windows(self) -> list[WinInfo]:
        skip_types = {self.atom(f"_NET_WM_WINDOW_TYPE_{t}")
                      for t in ("DOCK", "DESKTOP", "TOOLBAR", "MENU", "SPLASH", "NOTIFICATION")}
        hidden = self.atom("_NET_WM_STATE_HIDDEN")
        act = self._prop(self.root, "_NET_ACTIVE_WINDOW")
        active_id = int(act[0]) if act is not None and len(act) else self.d.get_input_focus().focus
        active_id = getattr(active_id, "id", active_id)
        out = []
        for wid in self._client_ids():
            win = self.d.create_resource_object("window", wid)
            types = self._prop(win, "_NET_WM_WINDOW_TYPE")
            if types is not None and skip_types & set(types):
                continue
            try:
                geo = win.get_geometry()
                pos = win.translate_coords(self.root, 0, 0)
                cls = win.get_wm_class()
            except Exception:
                continue  # liste alınırken kapanan pencere
            pid = self._prop(win, "_NET_WM_PID")
            state = self._prop(win, "_NET_WM_STATE")
            out.append(WinInfo(
                id=hex(wid), title=self._title(win),
                app=(cls[1] if cls else "") or "",
                pid=int(pid[0]) if pid is not None and len(pid) else None,
                x=-pos.x, y=-pos.y, w=geo.width, h=geo.height,
                active=wid == active_id,
                minimized=state is not None and hidden in list(state)))
        return out

    def _client_message(self, win, type_name: str, data: list[int]) -> None:
        from Xlib import X
        from Xlib.protocol import event
        ev = event.ClientMessage(window=win, client_type=self.atom(type_name),
                                 data=(32, (data + [0] * 5)[:5]))
        self.root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)

    def act(self, wid: str, action: str) -> bool:
        from Xlib import X
        try:
            num = int(wid, 16) if wid.lower().startswith("0x") else int(wid)
        except ValueError:
            raise ValueError(f"geçersiz X pencere kimliği: {wid}")
        if num not in self._client_ids():
            return False
        win = self.d.create_resource_object("window", num)
        if action == "activate":
            if self._wm_supports("_NET_ACTIVE_WINDOW"):
                # kaynak=2: kullanıcı/pager isteği (odak çalma engeline takılmaz)
                self._client_message(win, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0])
            else:
                win.map()
                win.configure(stack_mode=X.Above)
                win.set_input_focus(X.RevertToParent, X.CurrentTime)
        elif action == "close":
            if self._wm_supports("_NET_CLOSE_WINDOW"):
                self._client_message(win, "_NET_CLOSE_WINDOW", [X.CurrentTime, 2])
            else:
                protos = win.get_wm_protocols() or []
                if self.atom("WM_DELETE_WINDOW") not in protos:
                    raise Unsupported("pencere WM_DELETE_WINDOW desteklemiyor; kapatılamaz")
                from Xlib.protocol import event
                win.send_event(event.ClientMessage(
                    window=win, client_type=self.atom("WM_PROTOCOLS"),
                    data=(32, [self.atom("WM_DELETE_WINDOW"), X.CurrentTime, 0, 0, 0])))
        elif action == "minimize":
            if not self._has_wm():
                # pencere yöneticisi olmadan simge durumu yok; haritadan kaldırmak
                # pencereyi listeden de düşürür ve geri getirilemez
                raise Unsupported("pencere yöneticisi yok — simge durumuna küçültme desteklenmiyor")
            self._client_message(win, "WM_CHANGE_STATE", [3])  # ICCCM IconicState
        else:
            raise ValueError(f"bilinmeyen pencere eylemi: {action}")
        self.d.sync()
        return True

    # ------------------------------------------------------------- giriş
    def pointer_pos(self) -> tuple[int, int]:
        p = self.root.query_pointer()
        return p.root_x, p.root_y

    def _fake(self, ev_type: int, detail: int = 0, x: int = 0, y: int = 0) -> None:
        from Xlib.ext import xtest
        if ev_type == 6:  # MotionNotify: mutlak kök koordinatı
            xtest.fake_input(self.d, ev_type, x=x, y=y, root=self.root)
        else:
            xtest.fake_input(self.d, ev_type, detail)

    def move(self, x: float, y: float) -> None:
        from Xlib import X
        self._fake(X.MotionNotify, x=int(round(x)), y=int(round(y)))
        self.d.sync()

    def button(self, button: str, pressed: bool) -> None:
        from Xlib import X
        if button not in _BUTTONS:
            raise ValueError(f"bilinmeyen fare düğmesi: {button}")
        self._fake(X.ButtonPress if pressed else X.ButtonRelease, _BUTTONS[button])
        self.d.sync()

    def scroll(self, dy: int, dx: int = 0) -> None:
        from Xlib import X
        for btn, n in ((5 if dy > 0 else 4, abs(int(dy))), (7 if dx > 0 else 6, abs(int(dx)))):
            for _ in range(n):
                self._fake(X.ButtonPress, btn)
                self._fake(X.ButtonRelease, btn)
        self.d.sync()

    def _keysym(self, name: str) -> int:
        from Xlib import XK
        if len(name) == 1:
            return _char_keysym(name)
        ks = XK.string_to_keysym(_KEYSYM_NAMES.get(name, name))
        if not ks:
            ks = XK.string_to_keysym(name.capitalize())
        if not ks:
            raise ValueError(f"bilinmeyen tuş: {name}")
        return ks

    def _spare_keycode(self) -> int:
        """Hiçbir keysym'e bağlı olmayan tuş kodu (geçici eşleme için)."""
        lo, hi = self.d.display.info.min_keycode, self.d.display.info.max_keycode
        mapping = self.d.get_keyboard_mapping(lo, hi - lo + 1)
        for i in range(len(mapping) - 1, -1, -1):
            if not any(mapping[i]):
                return lo + i
        raise Unsupported("klavye eşlemesinde boş tuş kodu yok; Unicode yazılamıyor")

    def _tap(self, keycode: int, shift: bool = False) -> None:
        from Xlib import X
        shift_kc = self.d.keysym_to_keycode(self._keysym("shift"))
        if shift:
            self._fake(X.KeyPress, shift_kc)
        self._fake(X.KeyPress, keycode)
        self._fake(X.KeyRelease, keycode)
        if shift:
            self._fake(X.KeyRelease, shift_kc)

    def key(self, combo: str) -> None:
        mods, main = parse_combo(combo)
        codes = []
        for m in mods:
            kc = self.d.keysym_to_keycode(self._keysym(m))
            if not kc:
                raise Unsupported(f"klavye düzeninde '{m}' tuşu yok")
            codes.append(kc)
        ks = self._keysym(main)
        kc = self.d.keysym_to_keycode(ks)
        if not kc and len(main) == 1:
            # düzende olmayan karakter (ör. ctrl+ğ): geçici eşlemeyle bas
            kc = self._spare_keycode()
            self.d.change_keyboard_mapping(kc, [(ks, ks)])
            self.d.sync()
            time.sleep(0.02)
            try:
                self._press(codes + [kc])
            finally:
                time.sleep(0.02)
                self.d.change_keyboard_mapping(kc, [(0, 0)])
                self.d.sync()
            return
        if not kc:
            raise Unsupported(f"klavye düzeninde '{main}' tuşu yok")
        self._press(codes + [kc])

    def _press(self, codes: list[int]) -> None:
        from Xlib import X
        for c in codes:
            self._fake(X.KeyPress, c)
        for c in reversed(codes):
            self._fake(X.KeyRelease, c)
        self.d.sync()

    def type_text(self, text: str) -> None:
        if not text:
            return
        spare: Optional[int] = None
        try:
            for ch in text:
                if ch in "\r\n":
                    self._tap(self.d.keysym_to_keycode(self._keysym("enter")))
                    continue
                if ch == "\t":
                    self._tap(self.d.keysym_to_keycode(self._keysym("tab")))
                    continue
                ks = _char_keysym(ch)
                # mevcut düzende (düz ya da shift'li) varsa doğrudan
                hit = next(((kc, idx) for kc, idx in self.d.keysym_to_keycodes(ks)
                            if idx in (0, 1)), None)
                if hit:
                    self._tap(hit[0], shift=hit[1] == 1)
                else:
                    if spare is None:
                        spare = self._spare_keycode()
                    self.d.change_keyboard_mapping(spare, [(ks, ks)])
                    self.d.sync()
                    time.sleep(0.015)  # istemciler MappingNotify'ı işlesin
                    self._tap(spare)
                self.d.sync()
                time.sleep(0.004)
        finally:
            if spare is not None:
                time.sleep(0.03)
                self.d.change_keyboard_mapping(spare, [(0, 0)])
                self.d.sync()

    # ------------------------------------------------------------- ekran
    def screenshot(self, path: str) -> Screenshot:
        from Xlib import X
        geo = self.root.get_geometry()
        img = self.root.get_image(0, 0, geo.width, geo.height, X.ZPixmap, 0xFFFFFFFF)
        data = img.data if isinstance(img.data, bytes) else bytes(img.data)
        if len(data) != geo.width * geo.height * 4:
            raise Unsupported(f"desteklenmeyen X görüntü biçimi (derinlik {img.depth}, "
                              f"{len(data)} bayt) — scrot kurun")
        write_png(path, geo.width, geo.height, bgrx_to_rgb(data))
        return Screenshot(path=path, width=geo.width, height=geo.height,
                          backend="xlib", display=os.environ.get("DISPLAY"))
