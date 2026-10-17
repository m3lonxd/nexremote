"""Windows konsol yardımcısı — başka bir sürecin konsol tamponunu okur/yazar.

Neden ayrı süreç? `AttachConsole` yalnızca konsolu OLMAYAN bir süreçte çalışır;
NexRemote CLI'ı ise kendisi bir konsol uygulamasıdır. Bu yüzden windows.py bu
dosyayı `DETACHED_PROCESS` ile kısa ömürlü bir yardımcı olarak başlatır:

    python _wincon.py        (istek stdin'den JSON, yanıt stdout'a JSON)

    {"op": "list", "pids": [..]}                 → {"ok": true, "consoles": [..]}
    {"op": "read", "pid": N, "history": H}       → {"ok": true, "text": "..."}
    {"op": "send", "pid": N, "payload": "..."}   → {"ok": true, "written": K}

Hata: {"ok": false, "error": "...", "winerror": N}

Bu dosya kasıtlı olarak paket içe aktarması yapmaz (yalnızca stdlib): gömülü
(embeddable) Python'da ya da farklı bir çalışma dizininde de dosya yoluyla
çalıştırılabilir. Saf yardımcılar (`payload_to_key_events`) Linux'ta da test
edilebilir; ctypes.windll erişimi yalnızca fonksiyonların içindedir.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time

# Win32 tipleri — platformdan bağımsız sabit genişlikli (Linux'ta birim testi
# yapılabilsin diye wintypes yerine; Win64 ABI ile birebir aynı boyutlar)
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
SHORT = ctypes.c_int16
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p

# Sanal tuş kodları
VK_BACK, VK_TAB, VK_RETURN, VK_ESCAPE, VK_SPACE = 0x08, 0x09, 0x0D, 0x1B, 0x20
VK_PRIOR, VK_NEXT, VK_END, VK_HOME = 0x21, 0x22, 0x23, 0x24
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_DELETE = 0x25, 0x26, 0x27, 0x28, 0x2E

# dwControlKeyState bayrakları
RIGHT_ALT_PRESSED, LEFT_ALT_PRESSED = 0x1, 0x2
RIGHT_CTRL_PRESSED, LEFT_CTRL_PRESSED = 0x4, 0x8
SHIFT_PRESSED, ENHANCED_KEY = 0x10, 0x100

KEY_EVENT = 0x1
ENABLE_PROCESSED_INPUT = 0x1
CTRL_C_EVENT = 0

# term_payload() kaçış dizileri → konsol tuşu (konsol VT değil, tuş olayı bekler)
_ESC_SEQ = {
    "\x1b[A": VK_UP, "\x1b[B": VK_DOWN, "\x1b[C": VK_RIGHT, "\x1b[D": VK_LEFT,
    "\x1b[H": VK_HOME, "\x1b[F": VK_END, "\x1b[5~": VK_PRIOR, "\x1b[6~": VK_NEXT,
    "\x1b[3~": VK_DELETE,
}
# ctrl+\ ctrl+] ctrl+^ ctrl+_ (ABD düzeni: OEM_5, OEM_6, 6, OEM_MINUS)
_CTRL_PUNCT = {0x1C: (0xDC, 0), 0x1D: (0xDD, 0), 0x1E: (0x36, SHIFT_PRESSED),
               0x1F: (0xBD, SHIFT_PRESSED)}


def payload_to_key_events(payload: str) -> list[tuple[int, int, int]]:
    """Terminal yükünü konsol tuş olaylarına çevir: [(vk, utf16_birimi, ctrl_durumu)].

    Her olay için yardımcı bir basma + bir bırakma kaydı yazar. Kurallar:
      \\r, \\n      → Enter (karakter '\\r')
      \\t           → Tab;  \\x7f, \\x08 → Backspace (karakter '\\b')
      ESC [ A … ~  → ok/Home/End/PgUp/PgDn/Delete (ENHANCED_KEY, karakter yok)
      tek ESC      → Esc
      \\x01-\\x1a    → ctrl+harf (karakter = kontrol kodu; \\x03 = ctrl+c)
      diğerleri    → her UTF-16 birimi ayrı olay (emoji = vekil çifti → 2 olay)
    """
    out: list[tuple[int, int, int]] = []
    i, n = 0, len(payload)
    while i < n:
        ch = payload[i]
        if ch == "\x1b":
            for seq, vk in _ESC_SEQ.items():
                if payload.startswith(seq, i):
                    out.append((vk, 0, ENHANCED_KEY))
                    i += len(seq)
                    break
            else:
                out.append((VK_ESCAPE, 0x1B, 0))
                i += 1
            continue
        c = ord(ch)
        i += 1
        if ch in "\r\n":
            out.append((VK_RETURN, 0x0D, 0))
        elif ch == "\t":
            out.append((VK_TAB, 0x09, 0))
        elif c in (0x7F, 0x08):
            out.append((VK_BACK, 0x08, 0))
        elif 0x01 <= c <= 0x1A:
            out.append((0x40 + c, c, LEFT_CTRL_PRESSED))
        elif c in _CTRL_PUNCT:
            vk, extra = _CTRL_PUNCT[c]
            out.append((vk, c, LEFT_CTRL_PRESSED | extra))
        elif c == 0:
            out.append((VK_SPACE, 0, LEFT_CTRL_PRESSED))      # ctrl+@ / ctrl+space
        elif "a" <= ch <= "z":
            out.append((ord(ch.upper()), c, 0))
        elif "A" <= ch <= "Z":
            out.append((c, c, SHIFT_PRESSED))
        elif "0" <= ch <= "9" or ch == " ":
            out.append((c, c, 0))
        elif c > 0xFFFF:
            c -= 0x10000
            out.append((0, 0xD800 | (c >> 10), 0))
            out.append((0, 0xDC00 | (c & 0x3FF), 0))
        else:
            out.append((0, c, 0))
    return out


# --------------------------------------------------------------- yapılar
class COORD(ctypes.Structure):
    _fields_ = [("X", SHORT), ("Y", SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", SHORT), ("Top", SHORT), ("Right", SHORT), ("Bottom", SHORT)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", BOOL), ("wRepeatCount", WORD), ("wVirtualKeyCode", WORD),
                ("wVirtualScanCode", WORD), ("UnicodeChar", WORD),   # WCHAR (UTF-16 birimi)
                ("dwControlKeyState", DWORD)]


class _EVENT(ctypes.Union):
    # en büyük üye MOUSE_EVENT_RECORD (16 bayt) — birliğin boyutu onunla aynı olmalı
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", WORD), ("Event", _EVENT)]


# --------------------------------------------------------------- Win32
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class WinErr(Exception):
    def __init__(self, what: str, code: int) -> None:
        super().__init__(f"{what} başarısız (Win32 hata {code})")
        self.code = code


def _api():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u = ctypes.WinDLL("user32", use_last_error=True)

    def fn(dll, name, res, *args):
        f = getattr(dll, name)
        f.restype, f.argtypes = res, list(args)
        return f

    P = ctypes.POINTER
    return {
        "FreeConsole": fn(k, "FreeConsole", BOOL),
        "AttachConsole": fn(k, "AttachConsole", BOOL, DWORD),
        "GetConsoleProcessList": fn(k, "GetConsoleProcessList", DWORD, P(DWORD), DWORD),
        "GetConsoleWindow": fn(k, "GetConsoleWindow", ctypes.c_void_p),
        "GetConsoleTitleW": fn(k, "GetConsoleTitleW", DWORD, ctypes.c_wchar_p, DWORD),
        "CreateFileW": fn(k, "CreateFileW", HANDLE, ctypes.c_wchar_p, DWORD, DWORD,
                          ctypes.c_void_p, DWORD, DWORD, HANDLE),
        "CloseHandle": fn(k, "CloseHandle", BOOL, HANDLE),
        "GetConsoleScreenBufferInfo": fn(k, "GetConsoleScreenBufferInfo", BOOL, HANDLE,
                                         P(CONSOLE_SCREEN_BUFFER_INFO)),
        "ReadConsoleOutputCharacterW": fn(k, "ReadConsoleOutputCharacterW", BOOL, HANDLE,
                                          ctypes.c_wchar_p, DWORD, COORD, P(DWORD)),
        "WriteConsoleInputW": fn(k, "WriteConsoleInputW", BOOL, HANDLE, P(INPUT_RECORD),
                                 DWORD, P(DWORD)),
        "GetConsoleMode": fn(k, "GetConsoleMode", BOOL, HANDLE, P(DWORD)),
        "SetConsoleCtrlHandler": fn(k, "SetConsoleCtrlHandler", BOOL, ctypes.c_void_p, BOOL),
        "GenerateConsoleCtrlEvent": fn(k, "GenerateConsoleCtrlEvent", BOOL, DWORD, DWORD),
        "MapVirtualKeyW": fn(u, "MapVirtualKeyW", ctypes.c_uint, ctypes.c_uint, ctypes.c_uint),
    }


def _attach(api, pid: int) -> None:
    api["FreeConsole"]()            # konsolumuz yoksa da zararsız
    if not api["AttachConsole"](pid):
        raise WinErr(f"AttachConsole({pid})", ctypes.get_last_error())
    # Bu konsola gelen ctrl+c (bizim gönderdiğimiz dahil) yardımcıyı öldürmesin
    api["SetConsoleCtrlHandler"](None, True)


def _open(api, name: str):
    h = api["CreateFileW"](name, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           None, OPEN_EXISTING, 0, None)
    if h is None or h == INVALID_HANDLE_VALUE:
        raise WinErr(f"CreateFileW({name})", ctypes.get_last_error())
    return h


def op_list(api, pids: list[int]) -> dict:
    """Her aday süreç için konsolunu bul; aynı konsolu paylaşanları grupla."""
    me = os.getpid()
    seen: set[int] = set()
    consoles, errors = [], {}
    for pid in pids:
        if pid in seen:
            continue
        try:
            _attach(api, pid)
        except WinErr as e:
            errors[str(pid)] = e.code      # konsolsuz / erişim yok — listede yer almaz
            continue
        buf = (DWORD * 1024)()
        n = api["GetConsoleProcessList"](buf, 1024)
        members = [int(buf[i]) for i in range(min(n, 1024)) if int(buf[i]) != me]
        title = ctypes.create_unicode_buffer(1024)
        api["GetConsoleTitleW"](title, 1024)   # 0 = başlık yok; boş kalır
        consoles.append({"pid": pid, "members": members, "title": title.value,
                         "hwnd": api["GetConsoleWindow"]() or 0})
        seen.update(members)
        seen.add(pid)
    api["FreeConsole"]()
    return {"ok": True, "consoles": consoles, "errors": errors}


def op_read(api, pid: int, history: int) -> dict:
    _attach(api, pid)
    h = _open(api, "CONOUT$")
    try:
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not api["GetConsoleScreenBufferInfo"](h, ctypes.byref(info)):
            raise WinErr("GetConsoleScreenBufferInfo", ctypes.get_last_error())
        width = max(1, info.dwSize.X)
        top = max(0, info.srWindow.Top - max(0, int(history)))
        bottom = info.srWindow.Bottom
        buf = ctypes.create_unicode_buffer(width + 1)
        got = DWORD()
        lines = []
        for y in range(top, bottom + 1):
            if not api["ReadConsoleOutputCharacterW"](h, buf, width, COORD(0, y),
                                                      ctypes.byref(got)):
                raise WinErr("ReadConsoleOutputCharacterW", ctypes.get_last_error())
            lines.append(buf[:got.value].rstrip())
        return {"ok": True, "text": "\n".join(lines).rstrip(),
                "window": [info.srWindow.Top, info.srWindow.Bottom], "width": width}
    finally:
        api["CloseHandle"](h)


def op_send(api, pid: int, payload: str) -> dict:
    events = payload_to_key_events(payload)
    if not events:
        raise ValueError("gönderilecek tuş yok")
    _attach(api, pid)
    h = _open(api, "CONIN$")
    try:
        mode = DWORD()
        processed = bool(api["GetConsoleMode"](h, ctypes.byref(mode))
                         and mode.value & ENABLE_PROCESSED_INPUT)
        pending: list[tuple[int, int, int]] = []
        written = 0

        def flush() -> None:
            nonlocal written
            if not pending:
                return
            recs = (INPUT_RECORD * (2 * len(pending)))()
            for k, (vk, unit, state) in enumerate(pending):
                scan = api["MapVirtualKeyW"](vk, 0) if vk else 0
                for j, down in ((2 * k, 1), (2 * k + 1, 0)):
                    r = recs[j]
                    r.EventType = KEY_EVENT
                    ke = r.Event.KeyEvent
                    ke.bKeyDown, ke.wRepeatCount = down, 1
                    ke.wVirtualKeyCode, ke.wVirtualScanCode = vk, scan
                    ke.UnicodeChar, ke.dwControlKeyState = unit, state
            total, off = len(recs), 0
            while off < total:
                n = DWORD()
                ptr = ctypes.cast(ctypes.addressof(recs) + off * ctypes.sizeof(INPUT_RECORD),
                                  ctypes.POINTER(INPUT_RECORD))
                if not api["WriteConsoleInputW"](h, ptr, total - off, ctypes.byref(n)):
                    raise WinErr("WriteConsoleInputW", ctypes.get_last_error())
                if n.value == 0:
                    raise WinErr("WriteConsoleInputW (0 kayıt yazıldı)", 0)
                off += n.value
            written += total
            pending.clear()

        for ev in events:
            # İşlenmiş girdi kipinde gerçek ctrl+c bir sinyaldir, karakter değil:
            # konsol bunu tuş kaydından üretmez, biz üretiriz (klavyedeki gibi).
            if ev[1] == 0x03 and ev[2] & LEFT_CTRL_PRESSED and processed:
                flush()
                if not api["GenerateConsoleCtrlEvent"](CTRL_C_EVENT, 0):
                    raise WinErr("GenerateConsoleCtrlEvent", ctypes.get_last_error())
                time.sleep(0.05)       # sinyal iş parçacıkları başlamadan çıkmayalım
                continue
            pending.append(ev)
        flush()
        return {"ok": True, "written": written}
    finally:
        api["CloseHandle"](h)


def main() -> int:
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        api = _api()
        op = req.get("op")
        if op == "list":
            res = op_list(api, [int(p) for p in req["pids"]])
        elif op == "read":
            res = op_read(api, int(req["pid"]), int(req.get("history", 0)))
        elif op == "send":
            res = op_send(api, int(req["pid"]), str(req["payload"]))
        else:
            raise ValueError(f"bilinmeyen işlem: {op!r}")
    except WinErr as e:
        res = {"ok": False, "error": str(e), "winerror": e.code}
    except (ValueError, KeyError, TypeError, OSError) as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    sys.stdout.buffer.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
