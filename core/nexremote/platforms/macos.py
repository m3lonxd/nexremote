"""macOS masaüstü — yalnızca stdlib (ctypes + subprocess); pyobjc gerekmez.

    pencereler   Quartz CGWindowListCopyWindowInfo (katman 0 = normal pencereler);
                 eylemler Accessibility (AXUIElement: AXRaise / AXMinimized / kapat düğmesi)
    giriş        CGEventPost(kCGHIDEventTap): fare (sürüklemede *Dragged olayları,
                 çift tıkta kCGMouseEventClickState), klavye (sanal tuş + bayrak),
                 Unicode metin (CGEventKeyboardSetUnicodeString)
    ekran        `screencapture -x` (ana ekran, Retina'da piksel/nokta ölçeği)
    terminaller  Terminal.app + iTerm2 (AppleScript, argv ile — metin betiğe gömülmez)
                 + tmux bölmeleri (terminals.py taşınabilir API'si)
    öğe ağacı    Accessibility (backends/ax.py)

KOORDİNAT: Quartz genel ekran uzayı, nokta (point) cinsinden; ana ekranın sol üstü
(0, 0), y aşağı doğru artar. Pencere sınırları, imleç ve olaylar aynı uzaydadır.
Ekran görüntüsü pikseldir: Screenshot.scale = piksel genişliği / nokta genişliği.

İZİNLER (Sistem Ayarları → Gizlilik ve Güvenlik): NexRemote'u başlatan uygulamaya
(Terminal, iTerm2, VS Code…) verilir.
    Erişilebilirlik   fare/klavye olayları, pencere eylemleri, öğe ağacı
    Ekran Kaydı       ekran görüntüsü ve başkasına ait pencere başlıkları
    Otomasyon         Terminal.app / iTerm2 sekmelerini okuma-yazma (ilk kullanımda sorulur)

Modül Linux'ta da içe aktarılabilir (birim testleri): çerçeveler yalnızca `_quartz()`
ilk çağrıldığında, yani MacDesktop örneklenirken yüklenir.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from typing import Callable, NamedTuple, Optional

from ..models import Screenshot
from ._png import png_size
from .base import (BUTTONS, WINDOW_ACTIONS, Cap, Desktop, Terminal, Unsupported, WinInfo,
                   home_short, parse_combo, term_key_bytes, term_payload)

# ------------------------------------------------------------ C türleri
CFTypeRef = ctypes.c_void_p
CFIndex = ctypes.c_long             # signed long: macOS LP64'te 64 bit
CFTypeID = ctypes.c_ulong
Boolean = ctypes.c_ubyte            # CoreFoundation Boolean (unsigned char)
AXError = ctypes.c_int32
CGFloat = ctypes.c_double           # 64 bit macOS
UniChar = ctypes.c_uint16


class CGPoint(ctypes.Structure):
    _fields_ = [("x", CGFloat), ("y", CGFloat)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", CGFloat), ("height", CGFloat)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


class CFRange(ctypes.Structure):
    _fields_ = [("location", CFIndex), ("length", CFIndex)]


# ---------------------------------------------------------------- sabitler
kCFStringEncodingUTF8 = 0x08000100
kCFNumberSInt64Type, kCFNumberDoubleType = 4, 13

kCGWindowListOptionOnScreenOnly = 1 << 0
kCGWindowListOptionIncludingWindow = 1 << 3
kCGWindowListExcludeDesktopElements = 1 << 4
kCGNullWindowID = 0

kCGHIDEventTap = 0
kCGEventLeftMouseDown, kCGEventLeftMouseUp = 1, 2
kCGEventRightMouseDown, kCGEventRightMouseUp = 3, 4
kCGEventMouseMoved = 5
kCGEventLeftMouseDragged, kCGEventRightMouseDragged = 6, 7
kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGEventOtherMouseDragged = 25, 26, 27
kCGMouseButtonLeft, kCGMouseButtonRight, kCGMouseButtonCenter = 0, 1, 2
kCGMouseEventClickState = 1
kCGScrollEventUnitLine = 1

# düğme → (basma, bırakma, sürükleme olayı, CGMouseButton)
_MOUSE = {
    "left": (kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGEventLeftMouseDragged,
             kCGMouseButtonLeft),
    "right": (kCGEventRightMouseDown, kCGEventRightMouseUp, kCGEventRightMouseDragged,
              kCGMouseButtonRight),
    "middle": (kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGEventOtherMouseDragged,
               kCGMouseButtonCenter),
}

# CGEventFlags
FLAG_SHIFT = 1 << 17
FLAG_CONTROL = 1 << 18
FLAG_ALTERNATE = 1 << 19
FLAG_COMMAND = 1 << 20
FLAG_NUMPAD = 1 << 21
FLAG_FN = 1 << 23           # kCGEventFlagMaskSecondaryFn

# AXValueType
kAXValueCGPointType, kAXValueCGSizeType, kAXValueCGRectType = 1, 2, 3
kAXValueCFRangeType, kAXValueAXErrorType = 4, 5

# AXError
AX_SUCCESS = 0
AX_FAILURE = -25200
AX_ILLEGAL_ARGUMENT = -25201
AX_INVALID_ELEMENT = -25202
AX_CANNOT_COMPLETE = -25204
AX_ATTRIBUTE_UNSUPPORTED = -25205
AX_ACTION_UNSUPPORTED = -25206
AX_NOT_IMPLEMENTED = -25208
AX_API_DISABLED = -25211
AX_NO_VALUE = -25212
AX_ERROR_NAMES = {
    AX_FAILURE: "Failure", AX_ILLEGAL_ARGUMENT: "IllegalArgument",
    AX_INVALID_ELEMENT: "InvalidUIElement", AX_CANNOT_COMPLETE: "CannotComplete "
    "(uygulama yanıt vermiyor)", AX_ATTRIBUTE_UNSUPPORTED: "AttributeUnsupported",
    AX_ACTION_UNSUPPORTED: "ActionUnsupported", AX_NOT_IMPLEMENTED: "NotImplemented",
    AX_API_DISABLED: "APIDisabled", AX_NO_VALUE: "NoValue",
}

AX_PERMISSION = ("Erişilebilirlik izni yok — Sistem Ayarları → Gizlilik ve Güvenlik → "
                 "Erişilebilirlik'te terminal/python'a (NexRemote'u başlatan uygulamaya) "
                 "izin verin, ardından o uygulamayı yeniden başlatın")
SCREEN_PERMISSION = ("Ekran Kaydı izni yok — Sistem Ayarları → Gizlilik ve Güvenlik → "
                     "Ekran Kaydı (macOS 15+: 'Ekran ve Sistem Sesi Kaydı') bölümünde "
                     "terminal/python'a izin verin, ardından o uygulamayı yeniden başlatın")

# ------------------------------------------------- sanal tuş kodları (kVK_*)
# ANSI (ABD) konumları: tuş kodu fiziksel konumdur; harf dışı düzenlerde
# (AZERTY, Dvorak, Türkçe F) kısayol harfi konuma göre eşlenir.
_CHAR_KC = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "`": 50, " ": 49,
}
# ABD düzeninde Shift ile yazılan karakterler → temel tuş
_SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
            "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";",
            '"': "'", "<": ",", ">": ".", "?": "/", "~": "`"}
_NAMED_CHAR = {"plus": "+", "minus": "-", "comma": ",", "period": ".", "slash": "/",
               "backslash": "\\", "semicolon": ";", "equal": "=", "grave": "`",
               "apostrophe": "'", "bracketleft": "[", "bracketright": "]"}
KC_RETURN, KC_TAB = 36, 48
_NAMED_KC = {
    "enter": KC_RETURN, "tab": KC_TAB, "space": 49, "backspace": 51, "esc": 53,
    "delete": 117,                      # kVK_ForwardDelete
    "insert": 114, "help": 114,         # Mac klavyesinde Insert yerine Help
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "capslock": 57, "kpenter": 76,
    "super": 55, "shift": 56, "alt": 58, "ctrl": 59, "altgr": 61,
    "rsuper": 54, "rshift": 60, "ralt": 61, "rctrl": 62, "fn": 63,
    "volumeup": 72, "volumedown": 73, "volumemute": 74,
}
_FKEYS = {1: 122, 2: 120, 3: 99, 4: 118, 5: 96, 6: 97, 7: 98, 8: 100, 9: 101, 10: 109,
          11: 103, 12: 111, 13: 105, 14: 107, 15: 113, 16: 106, 17: 64, 18: 79, 19: 80,
          20: 90}
_NAMED_KC.update({f"f{i}": kc for i, kc in _FKEYS.items()})
# Gerçek klavyenin bu tuşlarda koyduğu bayraklar (NSEventModifierFlagFunction / NumericPad);
# bazı uygulamalar ok tuşu kısayollarını bu bayraklara bakarak ayırt eder.
_KEY_FLAGS = {k: FLAG_FN | FLAG_NUMPAD for k in ("left", "right", "down", "up")}
_KEY_FLAGS.update({k: FLAG_FN for k in ("delete", "insert", "help", "home", "end",
                                        "pageup", "pagedown", *(f"f{i}" for i in _FKEYS))})
_MOD_KC = {"super": 55, "shift": 56, "alt": 58, "ctrl": 59, "altgr": 61}
_MOD_FLAG = {"super": FLAG_COMMAND, "shift": FLAG_SHIFT, "alt": FLAG_ALTERNATE,
             "ctrl": FLAG_CONTROL, "altgr": FLAG_ALTERNATE}
_TYPE_CHUNK = 20            # CGEventKeyboardSetUnicodeString olay başına en çok 20 UTF-16 birimi


# ------------------------------------------------------- saf yardımcılar
KeyEvent = tuple[int, bool, int]        # (sanal tuş kodu, basma mı, CGEventFlags)


def key_code(main: str) -> tuple[int, bool, int]:
    """parse_combo'nun ana tuşu → (tuş kodu, Shift gerekli mi, ek bayraklar)."""
    if main in _NAMED_KC:
        return _NAMED_KC[main], False, _KEY_FLAGS.get(main, 0)
    main = _NAMED_CHAR.get(main, main)
    if len(main) != 1:
        raise ValueError(f"bilinmeyen tuş: {main!r}")
    if main in _CHAR_KC:
        return _CHAR_KC[main], False, 0
    if "A" <= main <= "Z":
        return _CHAR_KC[main.lower()], True, 0
    if main in _SHIFTED:
        return _CHAR_KC[_SHIFTED[main]], True, 0
    raise ValueError(f"'{main}' için sanal tuş yok (ANSI düzeni) — metin için type_text kullanın")


def combo_events(combo: str) -> list[KeyEvent]:
    """'cmd+shift+t' → [(tuş kodu, basma, bayraklar), …]: değiştiriciler sırayla
    basılır (her olay o ana kadar basılı değiştiricilerin bayraklarını taşır),
    ana tuş basılıp bırakılır, değiştiriciler ters sırada bırakılır."""
    mods, main = parse_combo(combo)
    kc, need_shift, extra = key_code(main)
    if need_shift and "shift" not in mods:
        mods.append("shift")
    seq = [m for m in mods if _MOD_KC[m] != kc]
    held: list[str] = []

    def flags() -> int:
        f = 0
        for m in held:
            f |= _MOD_FLAG[m]
        return f

    out: list[KeyEvent] = []
    for m in seq:
        held.append(m)
        out.append((_MOD_KC[m], True, flags()))
    main_flags = flags() | extra
    own = _MOD_FLAG.get(main, 0) if main in _MOD_KC else 0   # yalnızca 'shift' gibi
    out.append((kc, True, main_flags | own))
    out.append((kc, False, main_flags))
    for m in reversed(seq):
        held.remove(m)
        out.append((_MOD_KC[m], False, flags()))
    return out


def _utf16(ch: str) -> list[int]:
    raw = ch.encode("utf-16-le")
    return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]


def _joins_previous(ch: str, prev: str) -> bool:
    """ch önceki karakterle aynı görsel karakterin (grafem) parçası mı?"""
    cp = ord(ch)
    return (prev == "‍" or cp == 0x200D or 0xFE00 <= cp <= 0xFE0F
            or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F
            or unicodedata.combining(ch) != 0)


def text_chunks(text: str, limit: int = _TYPE_CHUNK) -> list[tuple[str, object]]:
    """Metni klavye olaylarına böl: ('key', KC_RETURN|KC_TAB) ya da ('text', utf16 birimleri).

    \\r\\n, \\r, \\n tek Enter olur. Her 'text' parçası en çok `limit` UTF-16 birimi
    taşır; vekil çiftleri (emoji) asla bölünmez, birleşen işaretler/ZWJ dizileri
    mümkünse aynı parçada tutulur (tek grafem limitten uzunsa kod noktasından bölünür)."""
    if limit < 2:
        raise ValueError("parça sınırı en az 2 UTF-16 birimi olmalı (vekil çifti)")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[tuple[str, object]] = []
    cur: list[int] = []
    cluster: list[int] = []
    prev = ""

    def push_cluster() -> None:
        nonlocal cluster
        if not cluster:
            return
        if len(cur) + len(cluster) > limit and cur:
            out.append(("text", tuple(cur)))
            cur.clear()
        if len(cluster) <= limit:
            cur.extend(cluster)
        else:                                   # aşırı uzun grafem: kod noktası sınırında böl
            i = 0
            while i < len(cluster):
                step = 2 if 0xD800 <= cluster[i] <= 0xDBFF else 1
                if len(cur) + step > limit:
                    out.append(("text", tuple(cur)))
                    cur.clear()
                cur.extend(cluster[i:i + step])
                i += step
        cluster = []

    for ch in text:
        if ch in "\n\t":
            push_cluster()
            if cur:
                out.append(("text", tuple(cur)))
                cur.clear()
            out.append(("key", KC_RETURN if ch == "\n" else KC_TAB))
            prev = ""
            continue
        if not (cluster and _joins_previous(ch, prev)):
            push_cluster()
        cluster.extend(_utf16(ch))
        prev = ch
    push_cluster()
    if cur:
        out.append(("text", tuple(cur)))
    return out


def parse_window_id(wid: str) -> int:
    s = (wid or "").strip()
    if s.startswith("win:"):
        s = s[4:]
    try:
        n = int(s)
    except ValueError:
        raise ValueError(f"geçersiz pencere kimliği: {wid!r} (CGWindowID, ör. 4521)") from None
    if n <= 0 or n > 0xFFFFFFFF:
        raise ValueError(f"geçersiz pencere kimliği: {wid!r}")
    return n


def cg_window_entry(d: dict) -> Optional[dict]:
    """CGWindowListCopyWindowInfo sözlüğü (Python'a çevrilmiş) → düz kayıt."""
    try:
        num = int(d.get("kCGWindowNumber") or 0)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    b = d.get("kCGWindowBounds") or {}

    def num_of(key: str, default: float = 0.0) -> float:
        try:
            return float(b.get(key, default))
        except (TypeError, ValueError):
            return default

    try:
        pid = int(d.get("kCGWindowOwnerPID") or 0) or None
        layer = int(d.get("kCGWindowLayer") or 0)
        alpha = float(d.get("kCGWindowAlpha", 1.0))
    except (TypeError, ValueError):
        return None
    return {"id": num, "pid": pid, "layer": layer, "alpha": alpha,
            "app": str(d.get("kCGWindowOwnerName") or ""),
            "title": str(d.get("kCGWindowName") or ""),
            "x": int(round(num_of("X"))), "y": int(round(num_of("Y"))),
            "w": int(round(num_of("Width"))), "h": int(round(num_of("Height"))),
            "onscreen": bool(d.get("kCGWindowIsOnscreen", True))}


def _normal(e: Optional[dict]) -> bool:
    return bool(e) and e["layer"] == 0 and e["alpha"] > 0 and e["w"] > 1 and e["h"] > 1


def front_pid_from_list(dicts: list[dict]) -> Optional[int]:
    """Liste önden arkaya sıralıdır: ilk normal pencerenin sahibi öndeki uygulamadır."""
    for d in dicts:
        e = cg_window_entry(d)
        if _normal(e):
            return e["pid"]
    return None


def windows_from_cg(dicts: list[dict], front_pid: Optional[int]) -> list[WinInfo]:
    """Ekrandaki (OnScreenOnly) pencere sözlükleri → WinInfo. Katman ≠ 0 (menü
    çubuğu, Dock, bildirim, kalkan pencereleri), saydam ve 1 noktalık pencereler
    elenir. Etkin pencere: öndeki uygulamanın en öndeki penceresi. Simge
    durumundaki pencereler ekranda olmadığından bu listede yer almaz."""
    out: list[WinInfo] = []
    active_done = False
    for d in dicts:
        e = cg_window_entry(d)
        if not _normal(e):
            continue
        active = not active_done and front_pid is not None and e["pid"] == front_pid
        active_done = active_done or active
        out.append(WinInfo(id=str(e["id"]), title=e["title"], app=e["app"], pid=e["pid"],
                           x=e["x"], y=e["y"], w=e["w"], h=e["h"], active=active,
                           minimized=False))
    return out


def _close(a: Optional[tuple], b: Optional[tuple], tol: int = 2) -> bool:
    return bool(a) and bool(b) and len(a) == len(b) and all(abs(p - q) <= tol
                                                            for p, q in zip(a, b))


def match_window(cands: list[tuple[Optional[int], str, Optional[tuple]]], cgid: Optional[int],
                 title: str, bounds: Optional[tuple]) -> Optional[int]:
    """CGWindow'u uygulamanın AX pencereleri arasında bul → aday indisi.
    cands: [(CGWindowID ya da None, AX başlığı, (x, y, w, h) ya da None)].
    Sıra: kesin kimlik → başlık + konum → yalnız konum (tek aday) → yalnız başlık (tek aday)."""
    if cgid:
        for i, (c, _, _) in enumerate(cands):
            if c == cgid:
                return i
    if title:
        for i, (_, t, b) in enumerate(cands):
            if t == title and _close(b, bounds):
                return i
    geo = [i for i, (_, _, b) in enumerate(cands) if _close(b, bounds)]
    if len(geo) == 1:
        return geo[0]
    if title:
        named = [i for i, (_, t, _) in enumerate(cands) if t == title]
        if len(named) == 1:
            return named[0]
    return None


# ----------------------------------------------- AppleScript / süreç ayrıştırma
_US, _RS = "\x1f", "\x1e"          # AppleScript: character id 31 / 30


def parse_records(out: str, nfields: int) -> list[list[str]]:
    """AppleScript çıktısı: kayıtlar RS (0x1e), alanlar US (0x1f) ile ayrılır."""
    recs: list[list[str]] = []
    for raw in out.split(_RS):
        raw = raw.strip("\r\n")
        if not raw:
            continue
        f = raw.split(_US)
        recs.append((f + [""] * nfields)[:nfields])
    return recs


def parse_ps(out: str) -> dict[str, tuple[int, str]]:
    """`ps -A -o pid=,tty=,stat=,comm=` → tty adı → (ön plan pid, program adı).
    Ön plan: durumunda '+' olan (terminalin ön plan süreç grubu) en yeni süreç."""
    by_tty: dict[str, list[tuple[int, bool, str]]] = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        pid, tty, stat, comm = int(parts[0]), parts[1], parts[2], parts[3].strip()
        if tty in ("??", "-", ""):
            continue
        name = os.path.basename(comm).lstrip("-")
        by_tty.setdefault(tty, []).append((pid, "+" in stat, name))
    res: dict[str, tuple[int, str]] = {}
    for tty, rows in by_tty.items():
        fg = [r for r in rows if r[1] and r[2] != "login"] or [r for r in rows if r[2] != "login"]
        best = max(fg or rows, key=lambda r: r[0])
        res[tty] = (best[0], best[2])
    return res


def parse_lsof_cwd(out: str) -> dict[int, str]:
    """`lsof -a -d cwd -Fn -p …` → pid → çalışma dizini (~ kısaltmalı)."""
    res: dict[int, str] = {}
    pid = None
    for line in out.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            res[pid] = home_short(line[1:])
    return res


def terminal_entries(term_recs: list[list[str]], iterm_recs: list[list[str]],
                     fg_by_tty: dict[str, tuple[int, str]],
                     cwd_by_pid: dict[int, str]) -> list[Terminal]:
    """Terminal.app kayıtları [pencere id, sekme no, tty, özel başlık, pencere adı,
    süreçler (virgüllü), seçili] ve iTerm2 kayıtları [oturum id, ad, tty] → Terminal."""
    out: list[Terminal] = []
    for wid, idx, tty, ctitle, wname, procs, _sel in term_recs:
        if not (wid.isdigit() and idx.isdigit()):
            continue
        pid, prog = fg_by_tty.get(os.path.basename(tty), (None, ""))
        if not prog and procs:
            prog = procs.split(",")[-1].strip().lstrip("-")
        out.append(Terminal(ref=f"terminal:{wid}:{idx}", title=ctitle or wname, program=prog,
                            cwd=cwd_by_pid.get(pid, "") if pid else "", kind="terminal",
                            pid=pid))
    for sid, name, tty in iterm_recs:
        if not sid:
            continue
        pid, prog = fg_by_tty.get(os.path.basename(tty), (None, ""))
        out.append(Terminal(ref=f"iterm:{sid}", title=name, program=prog,
                            cwd=cwd_by_pid.get(pid, "") if pid else "", kind="iterm", pid=pid))
    return out


_ITERM_SID = re.compile(r"^[A-Za-z0-9:_.\-]+$")


def parse_term_ref(ref: str) -> tuple[str, tuple]:
    """'terminal:<pencere id>:<sekme no>' | 'iterm:<oturum id>' | 'tmux:<hedef>'."""
    kind, _, rest = (ref or "").strip().partition(":")
    if kind == "terminal":
        wid, _, idx = rest.partition(":")
        if wid.isdigit() and idx.isdigit() and int(wid) > 0 and int(idx) > 0:
            return kind, (int(wid), int(idx))
        raise ValueError(f"geçersiz terminal ref: {ref!r} (terminal:PENCERE:SEKME)")
    if kind == "iterm":
        if rest and _ITERM_SID.match(rest):
            return kind, (rest,)
        raise ValueError(f"geçersiz terminal ref: {ref!r} (iterm:OTURUM-ID)")
    if kind == "tmux":
        if rest:
            return kind, (rest,)
        raise ValueError(f"geçersiz terminal ref: {ref!r} (tmux:OTURUM:P.B)")
    raise ValueError(f"geçersiz terminal ref: {ref!r} "
                     "(terminal:PENCERE:SEKME, iterm:OTURUM-ID veya tmux:OTURUM:P.B)")


def term_key_combo(name: str) -> str:
    """terminal_send keys adı ('C-c', 'ctrl+c', 'enter', 'up'…) → klavye kombinasyonu
    (Terminal.app'e gerçek tuş olarak basmak için)."""
    term_key_bytes(name)                    # bilinmeyen ad → ValueError (diğer platformlarla aynı)
    low = name.strip().lower()
    m = re.fullmatch(r"(?:c-|ctrl\+)(.)", low)
    if m:
        return f"ctrl+{m.group(1)}"
    return {"return": "enter", "escape": "esc", "pgup": "pageup", "pgdn": "pagedown"}.get(low, low)


def screen_tail(text: str, rows: int, history: int) -> str:
    """Tüm tampondan görünen ekran (son `rows` satır) + üstündeki `history` satır.
    Sondaki boş satırlar (imlecin altındaki boş ekran) sayılmaz: içerik asla
    kırpılmaz, gerekirse birkaç satır fazla geçmiş döner."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").rstrip().split("\n")
    if rows > 0:
        lines = lines[-(rows + max(0, history)):]
    return "\n".join(lines).rstrip()


def osa_error(stderr: str, app: str) -> Exception:
    """osascript hata çıktısı → anlamlı istisna."""
    msg = (stderr or "").strip()
    m = re.search(r"\((-?\d+)\)\s*$", msg)
    code = int(m.group(1)) if m else None
    if code == -1743:
        return Unsupported(f"{app} otomasyon izni yok — Sistem Ayarları → Gizlilik ve Güvenlik → "
                           f"Otomasyon'da terminal/python için '{app}' kutusunu açın")
    if code in (-1728, -1719, -10006):
        return LookupError(f"{app}: pencere/sekme/oturum bulunamadı (kapanmış olabilir)")
    if code in (-600, -609):
        return RuntimeError(f"{app} çalışmıyor")
    if code == -1712:
        return RuntimeError(f"{app} AppleScript isteğine zamanında yanıt vermedi")
    return RuntimeError(f"{app} AppleScript hatası: {msg or 'bilinmeyen hata'}")


# --------------------------------------------------------- AppleScript betikleri
# Metin ve kimlikler betiğe GÖMÜLMEZ; `on run argv` ile argüman olarak verilir.
_OSA_RUNNING = """
set r to ""
try
	if application id "com.apple.Terminal" is running then set r to r & "terminal "
end try
try
	if application id "com.googlecode.iterm2" is running then set r to r & "iterm"
end try
return r
"""

_OSA_TERMINAL_LIST = """
on run argv
	set US to character id 31
	set RS to character id 30
	set out to ""
	tell application id "com.apple.Terminal"
		repeat with w in windows
			set wid to (id of w) as text
			set wname to ""
			try
				set wname to (name of w) as text
			end try
			set i to 0
			repeat with t in tabs of w
				set i to i + 1
				set ctitle to ""
				try
					set ctitle to (custom title of t) as text
				end try
				set procs to ""
				try
					set AppleScript's text item delimiters to ","
					set procs to (processes of t) as text
				end try
				set AppleScript's text item delimiters to ""
				set out to out & wid & US & (i as text) & US & ((tty of t) as text) & US & ctitle & US & wname & US & procs & US & ((selected of t) as text) & RS
			end repeat
		end repeat
	end tell
	return out
end run
"""

_OSA_TERMINAL_READ = """
on run argv
	set wid to (item 1 of argv) as integer
	set ti to (item 2 of argv) as integer
	set mode to item 3 of argv
	set RS to character id 30
	if not (application id "com.apple.Terminal" is running) then error "Terminal çalışmıyor" number -600
	tell application id "com.apple.Terminal"
		set t to tab ti of window id wid
		if mode is "history" then
			return ((number of rows of t) as text) & RS & (history of t)
		end if
		return contents of t
	end tell
end run
"""

_OSA_TERMINAL_FOCUS = """
on run argv
	set wid to (item 1 of argv) as integer
	set ti to (item 2 of argv) as integer
	if not (application id "com.apple.Terminal" is running) then error "Terminal çalışmıyor" number -600
	tell application id "com.apple.Terminal"
		set w to window id wid
		try
			set miniaturized of w to false
		end try
		set selected of tab ti of w to true
		set index of w to 1
		activate
	end tell
	return "ok"
end run
"""

_OSA_ITERM_LIST = """
on run argv
	set US to character id 31
	set RS to character id 30
	set out to ""
	tell application id "com.googlecode.iterm2"
		repeat with w in windows
			repeat with t in tabs of w
				repeat with s in sessions of t
					set out to out & ((id of s) as text) & US & ((name of s) as text) & US & ((tty of s) as text) & RS
				end repeat
			end repeat
		end repeat
	end tell
	return out
end run
"""

_OSA_ITERM_SESSION = """
on run argv
	set sid to item 1 of argv
	set mode to item 2 of argv
	set RS to character id 30
	if not (application id "com.googlecode.iterm2" is running) then error "iTerm2 çalışmıyor" number -600
	tell application id "com.googlecode.iterm2"
		repeat with w in windows
			repeat with t in tabs of w
				repeat with s in sessions of t
					if ((id of s) as text) is sid then
						if mode is "write" then
							tell s to write text (item 3 of argv) newline false
							return "ok"
						end if
						return ((rows of s) as text) & RS & (contents of s)
					end if
				end repeat
			end repeat
		end repeat
	end tell
	error "oturum bulunamadı" number -1728
end run
"""


def run_osascript(script: str, args: tuple = (), app: str = "AppleScript",
                  timeout: float = 15.0) -> str:
    """Betiği argümanlarıyla çalıştır; çıktının sondaki tek satır sonu atılır."""
    if not shutil.which("osascript"):
        raise Unsupported("osascript bulunamadı (macOS dışı sistem?)")
    try:
        r = subprocess.run(["osascript", "-e", script, *[str(a) for a in args]],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{app}: AppleScript {timeout:.0f} sn içinde bitmedi") from None
    if r.returncode != 0:
        raise osa_error(r.stderr, app)
    out = r.stdout
    return out[:-1] if out.endswith("\n") else out


# ------------------------------------------------------------ Quartz erişimi
def _load_framework(name: str):
    path = f"/System/Library/Frameworks/{name}.framework/{name}"
    try:
        return ctypes.CDLL(path)        # macOS 11+: dosya diskte yok, dyld paylaşımlı önbelleğinden
    except OSError:
        found = ctypes.util.find_library(name)
        if not found:
            raise
        return ctypes.CDLL(found)


class AxWindow(NamedTuple):
    el: int                         # sahipli (retain edilmiş) AXUIElementRef — release_all ile bırak
    cgid: Optional[int]
    title: str
    bounds: Optional[tuple[int, int, int, int]]
    minimized: bool


class _Quartz:
    """Kullanılan her CoreFoundation / CoreGraphics / Accessibility fonksiyonu,
    argtypes/restype tanımlı olarak (64 bit işaretçiler kesilmesin diye)."""

    def __init__(self, loader: Callable[[str], object] = _load_framework) -> None:
        P = ctypes.POINTER
        self.cf = cf = loader("CoreFoundation")
        self.cg = cg = loader("CoreGraphics")
        self.axlib = ax = loader("ApplicationServices")     # HIServices: AX* fonksiyonları

        def fn(lib, name, res, *args, optional=False):
            try:
                f = getattr(lib, name)
            except AttributeError:
                if not optional:
                    raise
                setattr(self, name, None)               # bu macOS sürümünde yok
                return
            f.restype, f.argtypes = res, list(args)
            setattr(self, name, f)

        # CoreFoundation
        fn(cf, "CFRelease", None, CFTypeRef)
        fn(cf, "CFRetain", CFTypeRef, CFTypeRef)
        fn(cf, "CFGetTypeID", CFTypeID, CFTypeRef)
        fn(cf, "CFEqual", Boolean, CFTypeRef, CFTypeRef)
        for t in ("CFString", "CFNumber", "CFBoolean", "CFArray", "CFDictionary", "CFURL"):
            fn(cf, f"{t}GetTypeID", CFTypeID)
        fn(cf, "CFStringCreateWithCString", CFTypeRef, ctypes.c_void_p, ctypes.c_char_p,
           ctypes.c_uint32)
        fn(cf, "CFStringGetLength", CFIndex, CFTypeRef)
        fn(cf, "CFStringGetMaximumSizeForEncoding", CFIndex, CFIndex, ctypes.c_uint32)
        fn(cf, "CFStringGetCString", Boolean, CFTypeRef, ctypes.c_char_p, CFIndex,
           ctypes.c_uint32)
        fn(cf, "CFNumberGetValue", Boolean, CFTypeRef, CFIndex, ctypes.c_void_p)
        fn(cf, "CFNumberIsFloatType", Boolean, CFTypeRef)
        fn(cf, "CFBooleanGetValue", Boolean, CFTypeRef)
        fn(cf, "CFArrayGetCount", CFIndex, CFTypeRef)
        fn(cf, "CFArrayGetValueAtIndex", CFTypeRef, CFTypeRef, CFIndex)
        fn(cf, "CFArrayCreate", CFTypeRef, ctypes.c_void_p, P(ctypes.c_void_p), CFIndex,
           ctypes.c_void_p)
        fn(cf, "CFDictionaryGetCount", CFIndex, CFTypeRef)
        fn(cf, "CFDictionaryGetKeysAndValues", None, CFTypeRef, P(ctypes.c_void_p),
           P(ctypes.c_void_p))
        fn(cf, "CFDictionaryCreate", CFTypeRef, ctypes.c_void_p, P(ctypes.c_void_p),
           P(ctypes.c_void_p), CFIndex, ctypes.c_void_p, ctypes.c_void_p)
        fn(cf, "CFURLGetString", CFTypeRef, CFTypeRef)

        # CoreGraphics
        fn(cg, "CGWindowListCopyWindowInfo", CFTypeRef, ctypes.c_uint32, ctypes.c_uint32)
        fn(cg, "CGEventCreate", CFTypeRef, ctypes.c_void_p)
        fn(cg, "CGEventGetLocation", CGPoint, CFTypeRef)
        fn(cg, "CGEventCreateMouseEvent", CFTypeRef, ctypes.c_void_p, ctypes.c_uint32, CGPoint,
           ctypes.c_uint32)
        fn(cg, "CGEventCreateKeyboardEvent", CFTypeRef, ctypes.c_void_p, ctypes.c_uint16,
           ctypes.c_bool)
        fn(cg, "CGEventSetFlags", None, CFTypeRef, ctypes.c_uint64)
        fn(cg, "CGEventSetIntegerValueField", None, CFTypeRef, ctypes.c_uint32, ctypes.c_int64)
        fn(cg, "CGEventKeyboardSetUnicodeString", None, CFTypeRef, ctypes.c_ulong, P(UniChar))
        fn(cg, "CGEventPost", None, ctypes.c_uint32, CFTypeRef)
        # macOS 10.13+: değişken argümansız sürüm. Asıl fonksiyon variadic'tir; Apple
        # arm64 ABI'sinde variadic argümanlar yığından geçtiği için sabit argtypes ile
        # çağrılamaz — yedekte yalnızca sabit kısım tanımlanır, tekerlekler ek argümandır.
        fn(cg, "CGEventCreateScrollWheelEvent2", CFTypeRef, ctypes.c_void_p, ctypes.c_uint32,
           ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, optional=True)
        fn(cg, "CGEventCreateScrollWheelEvent", CFTypeRef, ctypes.c_void_p, ctypes.c_uint32,
           ctypes.c_uint32, optional=True)
        fn(cg, "CGMainDisplayID", ctypes.c_uint32)
        fn(cg, "CGDisplayBounds", CGRect, ctypes.c_uint32)
        fn(cg, "CGGetActiveDisplayList", ctypes.c_int32, ctypes.c_uint32, P(ctypes.c_uint32),
           P(ctypes.c_uint32))
        fn(cg, "CGPreflightScreenCaptureAccess", ctypes.c_bool, optional=True)    # 10.15+
        fn(cg, "CGRequestScreenCaptureAccess", ctypes.c_bool, optional=True)
        fn(cg, "CGPreflightPostEventAccess", ctypes.c_bool, optional=True)
        fn(cg, "CGRequestPostEventAccess", ctypes.c_bool, optional=True)

        # Accessibility (HIServices)
        fn(ax, "AXIsProcessTrusted", Boolean)
        fn(ax, "AXIsProcessTrustedWithOptions", Boolean, CFTypeRef)
        fn(ax, "AXUIElementCreateApplication", CFTypeRef, ctypes.c_int32)
        fn(ax, "AXUIElementCreateSystemWide", CFTypeRef)
        fn(ax, "AXUIElementCopyAttributeValue", AXError, CFTypeRef, CFTypeRef, P(CFTypeRef))
        fn(ax, "AXUIElementCopyMultipleAttributeValues", AXError, CFTypeRef, CFTypeRef,
           ctypes.c_uint32, P(CFTypeRef))
        fn(ax, "AXUIElementCopyActionNames", AXError, CFTypeRef, P(CFTypeRef))
        fn(ax, "AXUIElementSetAttributeValue", AXError, CFTypeRef, CFTypeRef, CFTypeRef)
        fn(ax, "AXUIElementIsAttributeSettable", AXError, CFTypeRef, CFTypeRef, P(Boolean))
        fn(ax, "AXUIElementPerformAction", AXError, CFTypeRef, CFTypeRef)
        fn(ax, "AXUIElementGetPid", AXError, CFTypeRef, P(ctypes.c_int32))
        fn(ax, "AXUIElementSetMessagingTimeout", AXError, CFTypeRef, ctypes.c_float)
        fn(ax, "AXUIElementGetTypeID", CFTypeID)
        fn(ax, "AXValueGetTypeID", CFTypeID)
        fn(ax, "AXValueGetType", ctypes.c_uint32, CFTypeRef)
        fn(ax, "AXValueGetValue", Boolean, CFTypeRef, ctypes.c_uint32, ctypes.c_void_p)
        # Belgelenmemiş ama 10.x'ten beri kararlı (yabai, Hammerspoon, Rectangle kullanır):
        # AX penceresinin CGWindowID'si. Yoksa başlık + konumla eşlenir.
        fn(ax, "_AXUIElementGetWindow", AXError, CFTypeRef, P(ctypes.c_uint32), optional=True)

        self._lock = threading.Lock()
        self._strs: dict[str, int] = {}
        self._tids: Optional[dict[str, int]] = None
        self._consts: dict[str, int] = {}
        self._ax_ready = False

    # ------------------------------------------------------------ CF yardımcıları
    def tids(self) -> dict[str, int]:
        if self._tids is None:
            self._tids = {
                "str": self.CFStringGetTypeID(), "num": self.CFNumberGetTypeID(),
                "bool": self.CFBooleanGetTypeID(), "arr": self.CFArrayGetTypeID(),
                "dict": self.CFDictionaryGetTypeID(), "url": self.CFURLGetTypeID(),
                "axval": self.AXValueGetTypeID(), "axel": self.AXUIElementGetTypeID(),
            }
        return self._tids

    def is_type(self, ref, kind: str) -> bool:
        return bool(ref) and self.CFGetTypeID(ref) == self.tids()[kind]

    def new_cfstr(self, s: str) -> int:
        """Sahipli CFString (çağıran CFRelease eder)."""
        ref = self.CFStringCreateWithCString(None, s.encode("utf-8"), kCFStringEncodingUTF8)
        if not ref:
            raise ValueError("metin CFString'e çevrilemedi (geçersiz UTF-8 / NUL karakter?)")
        return ref

    def cfstr(self, s: str) -> int:
        """Öznitelik/eylem adları için süreç boyunca saklanan CFString."""
        with self._lock:
            ref = self._strs.get(s)
            if ref is None:
                ref = self._strs[s] = self.new_cfstr(s)
            return ref

    def const(self, lib, name: str) -> int:
        """Çerçevedeki genel işaretçi değişkeni (kCFBooleanTrue, kAXTrustedCheckOptionPrompt…)."""
        if name not in self._consts:
            self._consts[name] = ctypes.c_void_p.in_dll(lib, name).value
        return self._consts[name]

    def const_addr(self, lib, name: str) -> int:
        """Genel yapı değişkeninin adresi (kCFTypeArrayCallBacks…)."""
        key = "&" + name
        if key not in self._consts:
            self._consts[key] = ctypes.addressof(ctypes.c_char.in_dll(lib, name))
        return self._consts[key]

    def cf_bool(self, value: bool) -> int:
        return self.const(self.cf, "kCFBooleanTrue" if value else "kCFBooleanFalse")

    def string(self, ref) -> str:
        n = self.CFStringGetLength(ref)
        if n <= 0:
            return ""
        size = self.CFStringGetMaximumSizeForEncoding(n, kCFStringEncodingUTF8) + 1
        buf = ctypes.create_string_buffer(size)
        if not self.CFStringGetCString(ref, buf, size, kCFStringEncodingUTF8):
            raise RuntimeError("CFStringGetCString başarısız")
        return buf.value.decode("utf-8", "replace")

    def to_py(self, ref, depth: int = 0):
        """CF nesnesi (ödünç) → Python değeri. AXUIElement ve bilinmeyen türler → None."""
        if not ref or depth > 8:
            return None
        t, k = self.CFGetTypeID(ref), self.tids()
        if t == k["str"]:
            return self.string(ref)
        if t == k["bool"]:
            return bool(self.CFBooleanGetValue(ref))
        if t == k["num"]:
            if self.CFNumberIsFloatType(ref):
                d = ctypes.c_double()
                self.CFNumberGetValue(ref, kCFNumberDoubleType, ctypes.byref(d))
                return d.value
            i = ctypes.c_int64()
            self.CFNumberGetValue(ref, kCFNumberSInt64Type, ctypes.byref(i))
            return i.value
        if t == k["arr"]:
            return [self.to_py(self.CFArrayGetValueAtIndex(ref, i), depth + 1)
                    for i in range(self.CFArrayGetCount(ref))]
        if t == k["dict"]:
            n = self.CFDictionaryGetCount(ref)
            keys, vals = (ctypes.c_void_p * n)(), (ctypes.c_void_p * n)()
            if n:
                self.CFDictionaryGetKeysAndValues(ref, keys, vals)
            return {self.to_py(keys[i], depth + 1): self.to_py(vals[i], depth + 1)
                    for i in range(n)}
        if t == k["axval"]:
            return self.axvalue(ref)
        if t == k["url"]:
            s = self.CFURLGetString(ref)                # Get kuralı: ödünç
            return self.string(s) if s else ""
        return None

    def axvalue(self, ref):
        vt = self.AXValueGetType(ref)
        if vt == kAXValueCGPointType:
            p = CGPoint()
            return (p.x, p.y) if self.AXValueGetValue(ref, vt, ctypes.byref(p)) else None
        if vt == kAXValueCGSizeType:
            s = CGSize()
            return (s.width, s.height) if self.AXValueGetValue(ref, vt, ctypes.byref(s)) else None
        if vt == kAXValueCGRectType:
            r = CGRect()
            if self.AXValueGetValue(ref, vt, ctypes.byref(r)):
                return (r.origin.x, r.origin.y, r.size.width, r.size.height)
            return None
        if vt == kAXValueCFRangeType:
            g = CFRange()
            return (g.location, g.length) if self.AXValueGetValue(ref, vt, ctypes.byref(g)) else None
        return None                                     # kAXValueAXErrorType: öznitelik yok

    def copy_window_list(self, option: int, relative: int = kCGNullWindowID) -> list[dict]:
        arr = self.CGWindowListCopyWindowInfo(option, relative)
        if not arr:
            raise Unsupported("pencere listesi alınamadı (grafik oturumu yok — SSH ile mi "
                              "bağlısınız? NexRemote'u oturum açmış kullanıcının masaüstünde çalıştırın)")
        try:
            data = self.to_py(arr)
        finally:
            self.CFRelease(arr)
        return [d for d in (data or []) if isinstance(d, dict)]

    # ------------------------------------------------------------ AX yardımcıları
    def ax_trusted(self) -> bool:
        return bool(self.AXIsProcessTrusted())

    def ax_prompt(self) -> bool:
        """İzin yoksa sistemin Erişilebilirlik iznini isteyen penceresini aç."""
        try:
            key = self.const(self.axlib, "kAXTrustedCheckOptionPrompt")
        except ValueError:
            key = self.cfstr("AXTrustedCheckOptionPrompt")     # sabitin değeri adıyla aynı
        keys = (ctypes.c_void_p * 1)(key)
        vals = (ctypes.c_void_p * 1)(self.cf_bool(True))
        d = self.CFDictionaryCreate(None, keys, vals, 1,
                                    self.const_addr(self.cf, "kCFTypeDictionaryKeyCallBacks"),
                                    self.const_addr(self.cf, "kCFTypeDictionaryValueCallBacks"))
        if not d:
            raise RuntimeError("CFDictionaryCreate başarısız")
        try:
            return bool(self.AXIsProcessTrustedWithOptions(d))
        finally:
            self.CFRelease(d)

    def _ax_setup(self) -> None:
        """Yanıt vermeyen uygulamalar NexRemote'u 6 sn (varsayılan) kilitlemesin."""
        if self._ax_ready:
            return
        sw = self.AXUIElementCreateSystemWide()
        if sw:
            try:
                self.AXUIElementSetMessagingTimeout(sw, 2.0)   # sistem geneli = tüm öğeler
            finally:
                self.CFRelease(sw)
        self._ax_ready = True

    def ax_check(self, err: int, what: str) -> None:
        if err == AX_SUCCESS:
            return
        if err == AX_API_DISABLED:
            raise Unsupported(AX_PERMISSION)
        name = AX_ERROR_NAMES.get(err)
        raise RuntimeError(f"{what} başarısız (AXError {err}" + (f" {name})" if name else ")"))

    def ax_copy(self, el, name: str) -> tuple[int, Optional[int]]:
        """(AXError, sahipli değer|None). İzin yoksa Unsupported."""
        self._ax_setup()
        out = CFTypeRef()
        err = self.AXUIElementCopyAttributeValue(el, self.cfstr(name), ctypes.byref(out))
        if err == AX_API_DISABLED:
            raise Unsupported(AX_PERMISSION)
        return err, (out.value if err == AX_SUCCESS else None)

    def ax_get(self, el, name: str, default=None):
        err, ref = self.ax_copy(el, name)
        if not ref:
            return default
        try:
            v = self.to_py(ref)
        finally:
            self.CFRelease(ref)
        return default if v is None else v

    def ax_elements(self, el, name: str) -> list[int]:
        """AXChildren / AXWindows gibi öğe dizisi → sahipli (retain) işaretçiler."""
        err, arr = self.ax_copy(el, name)
        if not arr:
            return []
        try:
            if not self.is_type(arr, "arr"):
                return []
            out = []
            for i in range(self.CFArrayGetCount(arr)):
                v = self.CFArrayGetValueAtIndex(arr, i)
                if v and self.is_type(v, "axel"):
                    out.append(self.CFRetain(v))
            return out
        finally:
            self.CFRelease(arr)

    def release_all(self, refs) -> None:
        for r in refs:
            if r:
                self.CFRelease(r)

    def ax_element(self, el, name: str) -> Optional[int]:
        """Tek öğe döndüren öznitelik (AXCloseButton, AXFocusedApplication…) → sahipli."""
        err, ref = self.ax_copy(el, name)
        if ref and not self.is_type(ref, "axel"):
            self.CFRelease(ref)
            return None
        return ref

    def ax_set(self, el, name: str, value_ref) -> int:
        self._ax_setup()
        err = self.AXUIElementSetAttributeValue(el, self.cfstr(name), value_ref)
        if err == AX_API_DISABLED:
            raise Unsupported(AX_PERMISSION)
        return err

    def ax_set_bool(self, el, name: str, value: bool) -> int:
        return self.ax_set(el, name, self.cf_bool(value))

    def ax_settable(self, el, name: str) -> bool:
        self._ax_setup()
        b = Boolean()
        err = self.AXUIElementIsAttributeSettable(el, self.cfstr(name), ctypes.byref(b))
        if err == AX_API_DISABLED:
            raise Unsupported(AX_PERMISSION)
        return err == AX_SUCCESS and bool(b.value)

    def ax_perform(self, el, action: str) -> int:
        self._ax_setup()
        err = self.AXUIElementPerformAction(el, self.cfstr(action))
        if err == AX_API_DISABLED:
            raise Unsupported(AX_PERMISSION)
        return err

    def ax_actions(self, el) -> list[str]:
        self._ax_setup()
        out = CFTypeRef()
        err = self.AXUIElementCopyActionNames(el, ctypes.byref(out))
        if err == AX_API_DISABLED:
            raise Unsupported(AX_PERMISSION)
        if err != AX_SUCCESS or not out.value:
            return []
        try:
            v = self.to_py(out.value)
        finally:
            self.CFRelease(out.value)
        return [a for a in (v or []) if isinstance(a, str)]

    def ax_pid(self, el) -> Optional[int]:
        pid = ctypes.c_int32()
        return pid.value if self.AXUIElementGetPid(el, ctypes.byref(pid)) == AX_SUCCESS else None

    def ax_window_id(self, el) -> Optional[int]:
        if self._AXUIElementGetWindow is None:
            return None
        wid = ctypes.c_uint32()
        if self._AXUIElementGetWindow(el, ctypes.byref(wid)) == AX_SUCCESS and wid.value:
            return wid.value
        return None

    def ax_frame(self, el) -> Optional[tuple[int, int, int, int]]:
        pos, size = self.ax_get(el, "AXPosition"), self.ax_get(el, "AXSize")
        return frame_of(pos, size)

    def ax_app(self, pid: int) -> int:
        """Sahipli uygulama öğesi."""
        self._ax_setup()
        app = self.AXUIElementCreateApplication(int(pid))
        if not app:
            raise RuntimeError(f"AXUIElementCreateApplication({pid}) başarısız")
        return app

    def ax_windows(self, pid: int) -> list[AxWindow]:
        """Uygulamanın AX pencereleri (simge durumundakiler dahil). Öğeler sahiplidir."""
        app = self.ax_app(pid)
        try:
            els = self.ax_elements(app, "AXWindows")
        finally:
            self.CFRelease(app)
        out = []
        for el in els:
            out.append(AxWindow(el, self.ax_window_id(el), str(self.ax_get(el, "AXTitle", "")),
                                self.ax_frame(el), bool(self.ax_get(el, "AXMinimized", False))))
        return out

    def ax_front_pid(self) -> Optional[int]:
        sw = self.AXUIElementCreateSystemWide()
        if not sw:
            return None
        try:
            app = self.ax_element(sw, "AXFocusedApplication")
        finally:
            self.CFRelease(sw)
        if not app:
            return None
        try:
            return self.ax_pid(app)
        finally:
            self.CFRelease(app)


def frame_of(pos, size) -> Optional[tuple[int, int, int, int]]:
    """AXPosition (x, y) + AXSize (w, h) → (x, y, w, h) tam sayı; boyut yoksa None."""
    try:
        x, y = (float(v) for v in pos)
        w, h = (float(v) for v in size)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (int(round(x)), int(round(y)), int(round(w)), int(round(h)))


_q_lock = threading.Lock()
_q: Optional[_Quartz] = None


def _quartz() -> _Quartz:
    global _q
    with _q_lock:
        if _q is None:
            if sys.platform != "darwin":
                raise Unsupported("Quartz/Accessibility yalnızca macOS'ta kullanılabilir")
            try:
                _q = _Quartz()
            except (OSError, AttributeError) as e:
                raise Unsupported(f"macOS çerçeveleri yüklenemedi: {e}") from None
        return _q


# ------------------------------------------------------------------ masaüstü
class MacDesktop(Desktop):
    name = "macos"

    def __init__(self, quartz: Optional[_Quartz] = None) -> None:
        if quartz is None and sys.platform != "darwin":
            raise Unsupported("MacDesktop yalnızca macOS'ta çalışır")
        self.q = quartz if quartz is not None else _quartz()
        self._held: list[str] = []          # basılı fare düğmeleri (sürükleme olayı türü için)
        self._input_ok = False
        self._term_errors: list[str] = []

    # ------------------------------------------------------------ izinler
    def _screen_ok(self) -> bool:
        f = self.q.CGPreflightScreenCaptureAccess
        return True if f is None else bool(f())          # 10.15 öncesi: izin gerekmez

    def _post_ok(self) -> bool:
        f = self.q.CGPreflightPostEventAccess
        return bool(f()) if f is not None else self.q.ax_trusted()

    def _require_input(self) -> None:
        if self._input_ok:
            return
        if not self._post_ok():
            # izin yokken CGEventPost olayları sessizce düşürür — burada açıkça durdur
            raise Unsupported(AX_PERMISSION + " (fare/klavye olayları için)")
        self._input_ok = True

    def _require_ax(self) -> None:
        if not self.q.ax_trusted():
            raise Unsupported(AX_PERMISSION)

    # ------------------------------------------------------------ yetenekler
    def caps(self) -> dict[str, Cap]:
        ax, screen, post = self.q.ax_trusted(), self._screen_ok(), self._post_ok()
        perm_ax = "Erişilebilirlik izni (Sistem Ayarları → Gizlilik ve Güvenlik → Erişilebilirlik)"
        perm_scr = "Ekran Kaydı izni (Sistem Ayarları → Gizlilik ve Güvenlik → Ekran Kaydı)"
        detail = "Quartz CGWindowList: ekrandaki normal pencereler (simge durumundakiler listelenmez)"
        wneeds: list[str] = []
        if not screen:
            if ax:
                detail += "; başlıklar Ekran Kaydı izni olmadığından Erişilebilirlik'ten okunuyor"
            else:
                detail += "; Ekran Kaydı izni olmadan başka uygulamaların pencere başlıkları boş gelir"
                wneeds.append(perm_scr)
        if not ax:
            detail += "; activate/close/minimize Erişilebilirlik izni ister"
            wneeds.append(perm_ax)
        if post:
            inp = Cap(True, "CGEventPost (HID): fare + klavye + Unicode metin; kısayollar ABD "
                            "(ANSI) tuş konumlarıyla gönderilir")
        else:
            inp = Cap(False, AX_PERMISSION, [perm_ax])
        tool = shutil.which("screencapture")
        if not tool:
            shot = Cap(False, "screencapture bulunamadı", ["screencapture (macOS ile gelir)"])
        elif not screen:
            shot = Cap(False, SCREEN_PERMISSION, [perm_scr])
        else:
            shot = Cap(True, "screencapture — ana ekran (Retina'da scale = piksel/nokta)")
        try:
            n = len(self.terminals())
            detail_t = f"{n} açık terminal (Terminal.app + iTerm2 AppleScript + tmux)"
            if self._term_errors:
                term = Cap(True, detail_t + "; " + "; ".join(self._term_errors),
                           ["Otomasyon izni (Sistem Ayarları → Gizlilik ve Güvenlik → Otomasyon)"])
            else:
                term = Cap(True, detail_t + ("" if post else "; Terminal.app'e yazmak "
                                             "Erişilebilirlik izni ister"))
        except Unsupported as e:
            term = Cap(False, str(e),
                       ["Otomasyon izni (Sistem Ayarları → Gizlilik ve Güvenlik → Otomasyon)"])
        except (OSError, RuntimeError) as e:
            term = Cap(False, f"terminaller listelenemedi: {e}")
        return {"windows": Cap(True, detail, wneeds), "input": inp, "screenshot": shot,
                "terminals": term}

    def gui_backend(self):
        from ..backends.ax import AxBackend
        return AxBackend(self)

    def setup(self) -> list[tuple[str, bool, str]]:
        steps: list[tuple[str, bool, str]] = []
        try:
            ok = self.q.ax_prompt()
            steps.append(("Erişilebilirlik izni", ok, "verildi" if ok else
                          AX_PERMISSION + " (sistem izin penceresi açıldı)"))
        except (ValueError, RuntimeError) as e:
            steps.append(("Erişilebilirlik izni", False, f"izin istemi açılamadı: {e}"))
        if not self._post_ok() and self.q.CGRequestPostEventAccess is not None:
            ok = bool(self.q.CGRequestPostEventAccess())
            steps.append(("fare/klavye olay izni", ok, "verildi" if ok else AX_PERMISSION))
        if self.q.CGPreflightScreenCaptureAccess is None:
            steps.append(("Ekran Kaydı izni", True, "bu macOS sürümünde gerekmez"))
        else:
            ok = self._screen_ok() or bool(self.q.CGRequestScreenCaptureAccess())
            steps.append(("Ekran Kaydı izni", ok, "verildi" if ok else
                          SCREEN_PERMISSION + " (sistem izin penceresi açıldı)"))
        try:
            n = len(self.terminals())
            steps.append(("terminaller (Otomasyon izni)", not self._term_errors,
                          "; ".join(self._term_errors) or f"{n} terminal görüldü"))
        except (Unsupported, OSError, RuntimeError) as e:
            steps.append(("terminaller (Otomasyon izni)", False, str(e)))
        from ..backends.ax import AxBackend
        cap = AxBackend(self).capability()
        steps.append(("Accessibility öğe ağacı", cap.available, cap.detail))
        return steps

    # --------------------------------------------------------------- pencere
    def _front_pid(self, dicts: list[dict]) -> Optional[int]:
        if self.q.ax_trusted():
            try:
                pid = self.q.ax_front_pid()
                if pid:
                    return pid
            except (Unsupported, RuntimeError):
                pass                    # AX yanıt vermedi: pencere sırasından tahmin et
        return front_pid_from_list(dicts)

    def windows(self) -> list[WinInfo]:
        dicts = self.q.copy_window_list(kCGWindowListOptionOnScreenOnly
                                        | kCGWindowListExcludeDesktopElements)
        wins = windows_from_cg(dicts, self._front_pid(dicts))
        if any(not w.title for w in wins) and self.q.ax_trusted():
            self._fill_titles(wins)
        return wins

    def _fill_titles(self, wins: list[WinInfo]) -> None:
        """Ekran Kaydı izni yokken boş gelen başlıkları AX'tan tamamla."""
        for pid in {w.pid for w in wins if not w.title and w.pid}:
            try:
                axw = self.q.ax_windows(pid)
            except (Unsupported, RuntimeError):
                continue
            try:
                cands = [(a.cgid, a.title, a.bounds) for a in axw]
                for w in wins:
                    if w.pid == pid and not w.title:
                        i = match_window(cands, int(w.id), "", (w.x, w.y, w.w, w.h))
                        if i is not None:
                            w.title = axw[i].title
            finally:
                self.q.release_all(a.el for a in axw)

    def _cg_window(self, cgid: int) -> Optional[dict]:
        for d in self.q.copy_window_list(kCGWindowListOptionIncludingWindow, cgid):
            e = cg_window_entry(d)
            if e and e["id"] == cgid:
                return e
        return None

    def ax_window(self, cgid: int, info: Optional[dict] = None) -> Optional[tuple[int, int]]:
        """CGWindowID → (sahipli AX pencere öğesi, pid) ya da None."""
        info = info or self._cg_window(cgid)
        if not info or not info["pid"]:
            return None
        axw = self.q.ax_windows(info["pid"])
        i = match_window([(a.cgid, a.title, a.bounds) for a in axw], cgid, info["title"],
                         (info["x"], info["y"], info["w"], info["h"]))
        keep = axw[i].el if i is not None else None
        self.q.release_all(a.el for a in axw if a.el != keep)
        return (keep, info["pid"]) if keep else None

    def window_action(self, wid: str, action: str) -> bool:
        if action not in WINDOW_ACTIONS:
            raise ValueError(f"bilinmeyen pencere eylemi: {action} ({', '.join(WINDOW_ACTIONS)})")
        cgid = parse_window_id(wid)
        info = self._cg_window(cgid)
        if info is None:
            return False
        self._require_ax()
        found = self.ax_window(cgid, info)
        if found is None:
            raise RuntimeError(f"pencere {cgid} ({info['app']}) erişilebilirlik ağacında "
                               "bulunamadı — başka bir Space'te ya da tam ekran olabilir")
        el, pid = found
        q = self.q
        try:
            if action == "minimize":
                q.ax_check(q.ax_set_bool(el, "AXMinimized", True), "AXMinimized")
            elif action == "close":
                btn = q.ax_element(el, "AXCloseButton")
                if not btn:
                    raise RuntimeError(f"pencerenin kapatma düğmesi yok: {cgid} ({info['app']})")
                try:
                    q.ax_check(q.ax_perform(btn, "AXPress"), "kapatma düğmesi AXPress")
                finally:
                    q.CFRelease(btn)
            else:
                if q.ax_get(el, "AXMinimized", False):
                    q.ax_check(q.ax_set_bool(el, "AXMinimized", False), "AXMinimized")
                q.ax_check(q.ax_perform(el, "AXRaise"), "AXRaise")
                q.ax_set_bool(el, "AXMain", True)    # desteklemeyen pencere olabilir: AXRaise yeter
                self.activate_app(pid)
        finally:
            q.CFRelease(el)
        return True

    def activate_app(self, pid: int) -> None:
        app = self.q.ax_app(pid)
        try:
            self.q.ax_check(self.q.ax_set_bool(app, "AXFrontmost", True), "AXFrontmost")
        finally:
            self.q.CFRelease(app)

    # ------------------------------------------------------- fare + klavye
    def _displays(self) -> list[tuple[float, float, float, float]]:
        ids = (ctypes.c_uint32 * 32)()
        n = ctypes.c_uint32()
        if self.q.CGGetActiveDisplayList(32, ids, ctypes.byref(n)) != 0:
            return []
        out = []
        for i in range(n.value):
            r = self.q.CGDisplayBounds(ids[i])
            out.append((r.origin.x, r.origin.y, r.size.width, r.size.height))
        return out

    def _check_on_screen(self, x: float, y: float) -> None:
        ds = self._displays()
        if ds and not any(dx <= x < dx + dw and dy <= y < dy + dh for dx, dy, dw, dh in ds):
            raise ValueError(f"({x:g}, {y:g}) hiçbir ekranın içinde değil: "
                             + ", ".join(f"({a:g}, {b:g}, {c:g}x{d:g})" for a, b, c, d in ds))

    def _post(self, ev, what: str) -> None:
        if not ev:
            raise RuntimeError(f"{what} olayı oluşturulamadı")
        try:
            self.q.CGEventPost(kCGHIDEventTap, ev)
        finally:
            self.q.CFRelease(ev)

    def _mouse(self, etype: int, x: float, y: float, button: int, click_state: int = 0) -> None:
        ev = self.q.CGEventCreateMouseEvent(None, etype, CGPoint(float(x), float(y)), button)
        if ev and click_state:
            self.q.CGEventSetIntegerValueField(ev, kCGMouseEventClickState, click_state)
        self._post(ev, "CGEventCreateMouseEvent")

    def pointer_pos(self) -> Optional[tuple[int, int]]:
        ev = self.q.CGEventCreate(None)
        if not ev:
            return None
        try:
            p = self.q.CGEventGetLocation(ev)
        finally:
            self.q.CFRelease(ev)
        return (int(round(p.x)), int(round(p.y)))

    def move(self, x: float, y: float) -> None:
        self._require_input()
        self._check_on_screen(x, y)
        if self._held:                       # basılı düğme varken taşıma = sürükleme
            b = self._held[0]
            self._mouse(_MOUSE[b][2], x, y, _MOUSE[b][3])
        else:
            self._mouse(kCGEventMouseMoved, x, y, kCGMouseButtonLeft)

    def button(self, button: str, pressed: bool) -> None:
        if button not in BUTTONS:
            raise ValueError(f"bilinmeyen fare düğmesi: {button} ({', '.join(BUTTONS)})")
        self._require_input()
        pos = self.pointer_pos()
        if pos is None:
            raise RuntimeError("imleç konumu okunamadı (CGEventCreate)")
        down, up, _, btn = _MOUSE[button]
        self._mouse(down if pressed else up, pos[0], pos[1], btn, click_state=1)
        if pressed and button not in self._held:
            self._held.append(button)
        elif not pressed and button in self._held:
            self._held.remove(button)

    def click(self, x: float, y: float, button: str = "left", count: int = 1) -> None:
        """Çift/üçlü tık: macOS tık sayısını olaydaki kCGMouseEventClickState'ten okur
        (ardışık tek tıklar çift tık sayılmaz)."""
        if button not in BUTTONS:
            raise ValueError(f"bilinmeyen fare düğmesi: {button} ({', '.join(BUTTONS)})")
        self.move(x, y)
        time.sleep(0.03)
        down, up, _, btn = _MOUSE[button]
        for i in range(1, max(1, min(int(count), 3)) + 1):
            self._mouse(down, x, y, btn, click_state=i)
            time.sleep(0.012)
            self._mouse(up, x, y, btn, click_state=i)
            time.sleep(0.05)

    def _scroll_event(self, vertical: int, horizontal: int):
        q = self.q
        if q.CGEventCreateScrollWheelEvent2 is not None:
            return q.CGEventCreateScrollWheelEvent2(None, kCGScrollEventUnitLine, 2,
                                                    vertical, horizontal, 0)
        if q.CGEventCreateScrollWheelEvent is not None:   # 10.13 öncesi: variadic
            return q.CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 2,
                                                   ctypes.c_int32(vertical),
                                                   ctypes.c_int32(horizontal))
        raise Unsupported("CGEventCreateScrollWheelEvent bu sistemde yok")

    def scroll(self, dy: int, dx: int = 0) -> None:
        # Quartz: wheel1 pozitif = yukarı, wheel2 pozitif = sola → +dy aşağı / +dx sağ için eksi
        self._require_input()
        for _ in range(abs(int(dy))):
            self._post(self._scroll_event(-1 if dy > 0 else 1, 0), "kaydırma")
            time.sleep(0.01)
        for _ in range(abs(int(dx))):
            self._post(self._scroll_event(0, -1 if dx > 0 else 1), "kaydırma")
            time.sleep(0.01)

    def _key_event(self, keycode: int, down: bool, flags: int,
                   units: Optional[tuple] = None) -> None:
        ev = self.q.CGEventCreateKeyboardEvent(None, keycode, down)
        if ev:
            self.q.CGEventSetFlags(ev, flags)
            if units:
                arr = (UniChar * len(units))(*units)
                self.q.CGEventKeyboardSetUnicodeString(ev, len(units), arr)
        self._post(ev, "CGEventCreateKeyboardEvent")

    def key(self, combo: str) -> None:
        try:
            events = combo_events(combo)
        except ValueError:
            mods, main = parse_combo(combo)
            if mods or len(main) != 1:
                raise
            self.type_text(main)            # ğ, é, € gibi tek karakter: Unicode olarak yaz
            return
        self._require_input()
        for kc, down, flags in events:
            self._key_event(kc, down, flags)
            time.sleep(0.002)

    def type_text(self, text: str) -> None:
        if not text:
            return
        self._require_input()
        for kind, val in text_chunks(text):
            if kind == "key":
                self._key_event(val, True, 0)
                self._key_event(val, False, 0)
            else:
                # tuş kodu 0 + Unicode dizesi: uygulama karakteri dizeden alır (düzenden bağımsız)
                self._key_event(0, True, 0, val)
                self._key_event(0, False, 0, val)
            time.sleep(0.004)

    def paste_combo(self) -> str:
        return "cmd+v"

    # ----------------------------------------------------------- ekran
    def screenshot(self, path: str) -> Screenshot:
        path = os.path.abspath(path)
        if not shutil.which("screencapture"):
            raise Unsupported("screencapture bulunamadı")
        if not self._screen_ok():
            # izin yokken screencapture yalnızca duvar kâğıdını/menü çubuğunu yakalar
            raise Unsupported(SCREEN_PERMISSION)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        try:
            r = subprocess.run(["screencapture", "-x", "-m", "-t", "png", path],
                               capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            raise RuntimeError("screencapture 20 sn içinde bitmedi") from None
        w, h = png_size(path)
        if r.returncode != 0 or w <= 0:
            err = (r.stderr or "").strip()
            raise Unsupported(f"screencapture başarısız (çıkış {r.returncode}): "
                              f"{err or 'görüntü oluşmadı'} — {SCREEN_PERMISSION}")
        b = self.q.CGDisplayBounds(self.q.CGMainDisplayID())
        pts = b.size.width
        scale = (w / pts) if pts > 0 else 1.0
        return Screenshot(path=path, width=w, height=h, backend="screencapture",
                          scale=round(scale, 4), origin=(int(b.origin.x), int(b.origin.y)),
                          display="main")

    # ------------------------------------------- masaüstündeki terminaller
    def _ps(self) -> dict[str, tuple[int, str]]:
        try:
            r = subprocess.run(["ps", "-A", "-o", "pid=,tty=,stat=,comm="], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return {}
        return parse_ps(r.stdout) if r.returncode == 0 else {}

    @staticmethod
    def _cwds(pids: list[int]) -> dict[int, str]:
        if not pids or not shutil.which("lsof"):
            return {}
        try:
            r = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fn", "-p",
                                ",".join(str(p) for p in sorted(set(pids)))],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return {}
        # çıkış 1: bazı pid'ler bu arada kapandı — kalanların çıktısı yine geçerli
        return parse_lsof_cwd(r.stdout)

    def terminals(self) -> list[Terminal]:
        from ..terminals import tmux_list
        self._term_errors = []
        term_recs: list[list[str]] = []
        iterm_recs: list[list[str]] = []
        running = ""
        if shutil.which("osascript"):
            try:
                running = run_osascript(_OSA_RUNNING, app="System")
            except (RuntimeError, LookupError, Unsupported) as e:
                self._term_errors.append(str(e))
        if "terminal" in running:
            try:
                term_recs = parse_records(run_osascript(_OSA_TERMINAL_LIST, app="Terminal"), 7)
            except (RuntimeError, LookupError, Unsupported) as e:
                self._term_errors.append(str(e))
        if "iterm" in running:
            try:
                iterm_recs = parse_records(run_osascript(_OSA_ITERM_LIST, app="iTerm2"), 3)
            except (RuntimeError, LookupError, Unsupported) as e:
                self._term_errors.append(str(e))
        out: list[Terminal] = []
        if term_recs or iterm_recs:
            fg = self._ps()
            pids = [fg[os.path.basename(r[2])][0] for r in term_recs + iterm_recs
                    if os.path.basename(r[2]) in fg]
            out = terminal_entries(term_recs, iterm_recs, fg, self._cwds(pids))
        out += tmux_list()
        if not out and self._term_errors:
            raise Unsupported("; ".join(self._term_errors))
        return out

    def terminal_read(self, ref: str, history: int = 0) -> str:
        kind, parts = parse_term_ref(ref)
        history = max(0, int(history))
        if kind == "tmux":
            from ..terminals import tmux_read
            return tmux_read(parts[0], history)
        if kind == "terminal":
            wid, idx = parts
            if history <= 0:
                return run_osascript(_OSA_TERMINAL_READ, (wid, idx, "contents"),
                                     app="Terminal").rstrip()
            out = run_osascript(_OSA_TERMINAL_READ, (wid, idx, "history"), app="Terminal",
                                timeout=30)
        else:
            out = run_osascript(_OSA_ITERM_SESSION, (parts[0], "read"), app="iTerm2", timeout=30)
        rows, _, text = out.partition(_RS)
        return screen_tail(text, int(rows) if rows.strip().isdigit() else 0, history)

    def terminal_send(self, ref: str, text: str = "", enter: bool = False,
                      keys: Optional[list[str]] = None) -> None:
        kind, parts = parse_term_ref(ref)
        if kind == "tmux":
            from ..terminals import tmux_send
            tmux_send(parts[0], term_payload(text, enter, keys))
            return
        if kind == "iterm":
            # write text baytları oturumun PTY'sine yazar: kontrol karakterleri ve \\r çalışır
            run_osascript(_OSA_ITERM_SESSION, (parts[0], "write", term_payload(text, enter, keys)),
                          app="iTerm2")
            return
        # Terminal.app'in AppleScript'i ham girdi yazamaz (`do script` komut çalıştırır,
        # tty'ye yazmak çıktıya gider): sekme öne alınır, metin klavyeden yazılır.
        combos = [term_key_combo(k) for k in (keys or [])]
        if not text and not combos and not enter:
            raise ValueError("gönderilecek metin veya tuş yok")
        self._require_input()
        wid, idx = parts
        run_osascript(_OSA_TERMINAL_FOCUS, (wid, idx), app="Terminal")
        self._wait_front_window(wid)
        if text:
            self.type_text(text)
        for c in combos:
            self.key(c)
        if enter:
            self.key("enter")

    def _wait_front_window(self, cgid: int, timeout: float = 1.5) -> None:
        """Terminal.app'in AppleScript pencere kimliği CGWindowID'dir: en öndeki
        normal pencere o olana dek bekle (yoksa yazılanlar başka pencereye gider)."""
        end = time.monotonic() + timeout
        while True:
            dicts = self.q.copy_window_list(kCGWindowListOptionOnScreenOnly
                                            | kCGWindowListExcludeDesktopElements)
            front = next((e for e in map(cg_window_entry, dicts) if _normal(e)), None)
            if front and front["id"] == cgid:
                return
            if time.monotonic() >= end:
                raise RuntimeError(f"Terminal penceresi {cgid} öne getirilemedi; "
                                   "yazma iptal edildi (metin başka pencereye gitmesin)")
            time.sleep(0.05)

    def terminal_history_supported(self, ref: str) -> bool:
        try:
            return parse_term_ref(ref)[0] in ("terminal", "iterm", "tmux")
        except ValueError:
            return False
