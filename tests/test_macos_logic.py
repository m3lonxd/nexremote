#!/usr/bin/env python3
"""macOS katmanı — saf mantık + sahte Quartz kütüphanesiyle olay sırası.

Bu makinede macOS yok: Quartz/AX/AppleScript çağrıları unittest.mock ile
taklit edilir; sınanan şey bizim ürettiğimiz çağrı dizisi ve argümanlardır.
Gerçek Apple API davranışı (izin pencereleri, olayların uygulamaya ulaşması)
burada doğrulanamaz.
"""
import os
import stat
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail and not ok else ""))


def raises(exc, fn, *a, **kw) -> bool:
    try:
        fn(*a, **kw)
    except exc:
        return True
    except Exception as e:                      # yanlış tür istisna = başarısız
        print(f"    beklenmeyen istisna: {type(e).__name__}: {e}")
        return False
    return False


# ------------------------------------------------------------ içe aktarma
try:
    from nexremote.platforms import macos as M
    from nexremote.backends import ax as AX
    from nexremote.models import Role
    from nexremote.platforms.base import Unsupported
    check("Linux'ta import: platforms.macos + backends.ax", True)
except Exception as e:                          # içe aktarma çökerse kalan testler anlamsız
    check("Linux'ta import: platforms.macos + backends.ax", False, f"{type(e).__name__}: {e}")
    print(f"MACOS: {sum(results)}/{len(results)}")
    sys.exit(1)

check("Linux'ta MacDesktop() → Unsupported (çerçeve yüklenmeden)",
      raises(Unsupported, M.MacDesktop))
check("Linux'ta AxBackend.capability() → kullanılamaz, çökme yok",
      AX.AxBackend().capability().available is False)

# ------------------------------------------------------------ tuş eşleme
CMD, SHIFT, CTRL, ALT = M.FLAG_COMMAND, M.FLAG_SHIFT, M.FLAG_CONTROL, M.FLAG_ALTERNATE
FN, NUMPAD = M.FLAG_FN, M.FLAG_NUMPAD
ev = M.combo_events("cmd+shift+t")
check("cmd+shift+t → ⌘↓ ⇧↓ T↓ T↑ ⇧↑ ⌘↑ (birikimli bayraklar)", ev == [
    (55, True, CMD), (56, True, CMD | SHIFT), (17, True, CMD | SHIFT), (17, False, CMD | SHIFT),
    (56, False, CMD), (55, False, 0)], str(ev))
ev = M.combo_events("ctrl+c")
check("ctrl+c → kVK_Control(59) + kVK_ANSI_C(8)",
      ev == [(59, True, CTRL), (8, True, CTRL), (8, False, CTRL), (59, False, 0)], str(ev))
check("Return → kVK_Return(36), bayraksız",
      M.combo_events("Return") == [(36, True, 0), (36, False, 0)])
check("f5 → kVK_F5(96) + Fn bayrağı", M.combo_events("f5") == [(96, True, FN), (96, False, FN)])
ev = M.combo_events("ctrl++")
check("ctrl++ → ctrl + shift + '=' (24)", ev == [
    (59, True, CTRL), (56, True, CTRL | SHIFT), (24, True, CTRL | SHIFT),
    (24, False, CTRL | SHIFT), (56, False, CTRL), (59, False, 0)], str(ev))
check("up → 126 + Fn + NumericPad", M.combo_events("up")[0] == (126, True, FN | NUMPAD))
check("option+left → alt=option(58)", M.combo_events("option+left")[0] == (58, True, ALT))
check("'T' büyük harf → shift eklenir", M.combo_events("T")[0] == (56, True, SHIFT))
check("delete → ileri sil (117), backspace → 51",
      M.combo_events("delete")[0][0] == 117 and M.combo_events("backspace")[0][0] == 51)
check("tab/space/esc/home/end/pageup/pagedown kodları",
      [M.combo_events(k)[0][0] for k in ("tab", "space", "esc", "home", "end", "pageup",
                                         "pagedown")] == [48, 49, 53, 115, 119, 116, 121])
check("f1/f12/f20 kodları", [M.combo_events(k)[0][0] for k in ("f1", "f12", "f20")]
      == [122, 111, 90])
check("tek 'shift' basma/bırakma bayrağı",
      M.combo_events("shift") == [(56, True, SHIFT), (56, False, 0)])
check("bilinmeyen tuş → ValueError", raises(ValueError, M.combo_events, "ctrl+zıpla"))
check("ANSI'de olmayan 'ğ' → ValueError (type_text önerilir)",
      raises(ValueError, M.combo_events, "ctrl+ğ"))

# ------------------------------------------------------------ UTF-16 parçalama
ch = M.text_chunks("a" * 45)
check("45 ASCII karakter → 20+20+5", [len(c[1]) for c in ch] == [20, 20, 5], str(ch))
ch = M.text_chunks("a" * 19 + "😀b")
check("emoji vekil çifti bölünmüyor (19 + [😀b])",
      [c[1] for c in ch] == [tuple([97] * 19), (0xD83D, 0xDE00, 98)], str(ch))
ch = M.text_chunks("ğüşiöçĞÜŞİÖÇı")
check("Türkçe karakterler tek UTF-16 birimi, tek parça",
      len(ch) == 1 and len(ch[0][1]) == 13
      and "".join(map(chr, ch[0][1])) == "ğüşiöçĞÜŞİÖÇı", str(ch))
ch = M.text_chunks("a\r\nb\rc\nd\te")
check("\\r\\n/\\r/\\n → tek Enter, \\t → Tab", [k for k, _ in ch] ==
      ["text", "key", "text", "key", "text", "key", "text", "key", "text"]
      and [v for k, v in ch if k == "key"] == [36, 36, 36, 48], str(ch))
fam = "👨‍👩‍👧"
ch = M.text_chunks("x" * 15 + fam)
check("ZWJ aile emojisi (8 birim) bölünmeden sonraki parçaya", [len(c[1]) for c in ch] == [15, 8]
      and "".join(chr(u) for u in ch[1][1]).encode("utf-16-le", "surrogatepass")
      .decode("utf-16-le") == fam, str(ch))
units = [u for _, v in M.text_chunks("😀" * 30) for u in v]
check("30 emoji: hiçbir parça vekil yarısıyla bitmiyor",
      all(not (0xD800 <= c[1][-1] <= 0xDBFF) and len(c[1]) <= 20
          for c in M.text_chunks("😀" * 30)) and len(units) == 60)
check("boş metin → olay yok", M.text_chunks("") == [])

# ------------------------------------------------------------ CGWindow → WinInfo
CG = [
    {"kCGWindowNumber": 9, "kCGWindowLayer": 25, "kCGWindowOwnerName": "Dock",
     "kCGWindowOwnerPID": 90, "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1440, "Height": 900}},
    {"kCGWindowNumber": 4521, "kCGWindowLayer": 0, "kCGWindowOwnerName": "Terminal",
     "kCGWindowOwnerPID": 501, "kCGWindowName": "~ — zsh", "kCGWindowAlpha": 1.0,
     "kCGWindowBounds": {"X": 10.0, "Y": 38.0, "Width": 800.0, "Height": 500.5}},
    {"kCGWindowNumber": 4522, "kCGWindowLayer": 0, "kCGWindowOwnerName": "Safari",
     "kCGWindowOwnerPID": 777, "kCGWindowAlpha": 1.0,
     "kCGWindowBounds": {"X": -1280, "Y": 0, "Width": 1280, "Height": 800}},
    {"kCGWindowNumber": 4523, "kCGWindowLayer": 0, "kCGWindowOwnerName": "Terminal",
     "kCGWindowOwnerPID": 501, "kCGWindowName": "ikinci", "kCGWindowAlpha": 1.0,
     "kCGWindowBounds": {"X": 20, "Y": 60, "Width": 700, "Height": 400}},
    {"kCGWindowNumber": 4524, "kCGWindowLayer": 0, "kCGWindowOwnerName": "Gizli",
     "kCGWindowOwnerPID": 888, "kCGWindowAlpha": 0.0,
     "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 300, "Height": 300}},
    {"kCGWindowNumber": 4525, "kCGWindowLayer": 0, "kCGWindowOwnerName": "Ufak",
     "kCGWindowOwnerPID": 889, "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1, "Height": 1}},
]
ws = M.windows_from_cg(CG, M.front_pid_from_list(CG))
ids = [w.id for w in ws]
check("katman ≠ 0, saydam ve 1 noktalık pencereler elendi", ids == ["4521", "4522", "4523"],
      str(ids))
t = ws[0]
check("WinInfo alanları (id/başlık/uygulama/pid/yuvarlanmış sınırlar)",
      (t.title, t.app, t.pid, t.x, t.y, t.w, t.h) == ("~ — zsh", "Terminal", 501, 10, 38, 800, 500))
check("isimsiz pencere listede, başlık boş", ws[1].title == "" and ws[1].app == "Safari"
      and ws[1].x == -1280)
check("etkin = öndeki uygulamanın yalnızca ilk penceresi; minimized hep False",
      [w.active for w in ws] == [True, False, False] and not any(w.minimized for w in ws))
check("öndeki pid AX'tan gelirse ona göre", [w.active for w in M.windows_from_cg(CG, 777)]
      == [False, True, False])
check("bozuk kayıt (pencere no yok) atlanır", M.cg_window_entry({"kCGWindowLayer": 0}) is None)
check("pencere kimliği ayrıştırma", M.parse_window_id("win:4521") == 4521
      and raises(ValueError, M.parse_window_id, "abc") and raises(ValueError, M.parse_window_id, "0"))

cands = [(None, "Belge 1", (0, 0, 800, 600)), (4600, "Belge 2", (100, 100, 800, 600)),
         (None, "Belge 1", (50, 50, 400, 300))]
check("AX pencere eşleme: kesin CGWindowID", M.match_window(cands, 4600, "", None) == 1)
check("AX pencere eşleme: başlık + konum (±2)",
      M.match_window(cands, 1, "Belge 1", (51, 49, 400, 301)) == 2)
check("AX pencere eşleme: yalnız konum (başlık yok)",
      M.match_window(cands, 1, "", (0, 0, 800, 600)) == 0)
check("AX pencere eşleme: belirsiz başlık → None", M.match_window(cands, 1, "Belge 1", None) is None)
check("frame_of: AXPosition + AXSize", M.frame_of((10.4, 20.6), (300.0, 200.0)) == (10, 21, 300, 200)
      and M.frame_of((0, 0), (0, 10)) is None and M.frame_of(None, (1, 1)) is None)

# ------------------------------------------------------------ AppleScript / ps / lsof
US, RS = "\x1f", "\x1e"
term_out = (f"1234{US}1{US}/dev/ttys003{US}{US}~ — zsh{US}login,-zsh,claude{US}true{RS}"
            f"1234{US}2{US}/dev/ttys004{US}derleme{US}~ — zsh{US}login,-zsh{US}false{RS}")
recs = M.parse_records(term_out, 7)
check("AppleScript kayıt ayrıştırma (RS/US)", len(recs) == 2 and recs[1][3] == "derleme"
      and recs[0][5] == "login,-zsh,claude", str(recs))
check("eksik alanlar boş dizeyle tamamlanır", M.parse_records(f"a{US}b{RS}\n", 3) == [["a", "b", ""]])
ps_out = """  501 ttys003  Ss   login
  502 ttys003  S    -zsh
  610 ttys003  S+   /usr/local/bin/claude
  700 ttys004  Ss+  -zsh
  800 ??       Ss   /usr/sbin/cfprefsd
"""
fg = M.parse_ps(ps_out)
check("ps: tty → ön plan süreci ('+'), login atlanır", fg == {"ttys003": (610, "claude"),
                                                          "ttys004": (700, "zsh")}, str(fg))
cwd = M.parse_lsof_cwd("p610\nfcwd\nn/tmp/proje\np700\nfcwd\nn/\n")
check("lsof cwd ayrıştırma", cwd == {610: "/tmp/proje", 700: "/"}, str(cwd))
iterm = M.parse_records(f"ABCD-1234{US}python{US}/dev/ttys009{RS}", 3)
terms = M.terminal_entries(recs, iterm, fg, cwd)
check("terminal girdileri: ref/başlık/program/cwd",
      [(t.ref, t.title, t.program, t.cwd, t.kind) for t in terms] == [
          ("terminal:1234:1", "~ — zsh", "claude", "/tmp/proje", "terminal"),
          ("terminal:1234:2", "derleme", "zsh", "/", "terminal"),
          ("iterm:ABCD-1234", "python", "", "", "iterm")], str(terms))
check("ps eşleşmezse AppleScript süreç listesinin sonu program olur",
      M.terminal_entries([["5", "1", "/dev/ttys099", "", "w", "login,-bash", "true"]], [], {}, {})[0]
      .program == "bash")
check("Terminal ref ayrıştırma", M.parse_term_ref("terminal:1234:2") == ("terminal", (1234, 2))
      and M.parse_term_ref("iterm:w0t0p0:ABCD") == ("iterm", ("w0t0p0:ABCD",))
      and M.parse_term_ref("tmux:s:0.1") == ("tmux", ("s:0.1",)))
check("Terminal ref hataları → ValueError", all(raises(ValueError, M.parse_term_ref, r) for r in (
    "", "terminal:1234", "terminal:x:1", "terminal:1:0", "iterm:", "iterm:a b\"", "tmux:",
    "konsole:1:2")))
check("terminal tuş adı → kombinasyon", [M.term_key_combo(k) for k in
                                         ("C-c", "ctrl+d", "Return", "escape", "pgup", "up")]
      == ["ctrl+c", "ctrl+d", "enter", "esc", "pageup", "up"])
check("bilinmeyen terminal tuşu → ValueError", raises(ValueError, M.term_key_combo, "zıpla"))
check("ekran kuyruğu: sondaki boşluk atılır, son rows + history satır",
      M.screen_tail("a\nb\nc\nd\ne\n\n", 2, 1) == "c\nd\ne"
      and M.screen_tail("x\ny", 0, 5) == "x\ny")
e = M.osa_error("35:60: execution error: Not authorized to send Apple events to Terminal. (-1743)",
                "Terminal")
check("osascript -1743 → Unsupported (Otomasyon izni)", isinstance(e, Unsupported)
      and "Otomasyon" in str(e))
check("osascript -1728 → LookupError, -600 → çalışmıyor",
      isinstance(M.osa_error("hata (-1728)", "iTerm2"), LookupError)
      and "çalışmıyor" in str(M.osa_error("x (-600)", "Terminal")))

# ------------------------------------------------------------ AX ref + rol
check("AX ref ayrıştırma", AX.parse_ref("ax:501:w4521.0.3.1") == (501, "w4521", (0, 3, 1))
      and AX.parse_ref("ax:501") == (501, None, ()) and AX.parse_ref("ax:501:2") == (501, "2", ()))
check("AX ref gidiş-dönüş", all(AX.make_ref(*AX.parse_ref(r)) == r for r in
                                ("ax:1", "ax:501:w9", "ax:501:0.4.2", "ax:7:w12.0")))
check("AX ref hataları → ValueError", all(raises(ValueError, AX.parse_ref, r) for r in (
    "", "uia:0x1", "ax:", "ax:abc", "ax:0", "ax:5:w0", "ax:5:w1.", "ax:5:w1.x", "ax:5:.1",
    "ax:5:w1.-2")))
check("pencere belirteci olmadan yol → ValueError", raises(ValueError, AX.make_ref, 5, None, (1,)))
roles = {r: AX.role_for(r, None, 1) for r in (
    "AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXRadioButton", "AXMenuItem",
    "AXStaticText", "AXLink", "AXWindow", "AXGroup", "AXList", "AXTable", "AXRow", "AXCell",
    "AXTabGroup", "AXComboBox", "AXPopUpButton", "AXSlider", "AXImage", "AXScrollArea",
    "AXToolbar", "AXMenuBar", "AXOutline", "AXSheet", "AXBilinmeyen")}
check("AX rol eşlemesi", roles == {
    "AXButton": Role.BUTTON, "AXTextField": Role.ENTRY, "AXTextArea": Role.ENTRY,
    "AXCheckBox": Role.CHECKBOX, "AXRadioButton": Role.RADIO, "AXMenuItem": Role.MENU_ITEM,
    "AXStaticText": Role.TEXT, "AXLink": Role.LINK, "AXWindow": Role.WINDOW, "AXGroup": Role.PANEL,
    "AXList": Role.LIST, "AXTable": Role.LIST, "AXRow": Role.LIST_ITEM, "AXCell": Role.LIST_ITEM,
    "AXTabGroup": Role.TAB, "AXComboBox": Role.COMBOBOX, "AXPopUpButton": Role.COMBOBOX,
    "AXSlider": Role.SLIDER, "AXImage": Role.IMAGE, "AXScrollArea": Role.PANEL,
    "AXToolbar": Role.PANEL, "AXMenuBar": Role.MENU, "AXOutline": Role.TREE,
    "AXSheet": Role.DIALOG, "AXBilinmeyen": Role.UNKNOWN}, str(roles))
check("alt rol inceltmesi + kök pencere = frame",
      AX.role_for("AXRadioButton", "AXTabButton", 2) is Role.TAB
      and AX.role_for("AXRow", "AXOutlineRow", 3) is Role.TREE_ITEM
      and AX.role_for("AXWindow", "AXStandardWindow", 0) is Role.FRAME
      and AX.role_for("AXWindow", "AXDialog", 0) is Role.DIALOG
      and AX.role_for("AXCheckBox", "AXSwitch", 1) is Role.CHECKBOX)
check("öğe adı: başlık → açıklama → statik metin değeri → yer tutucu",
      AX.node_name({"AXTitle": " Kaydet ", "AXDescription": "x"}) == "Kaydet"
      and AX.node_name({"AXTitle": "", "AXDescription": "Kapat"}) == "Kapat"
      and AX.node_name({"AXRole": "AXStaticText", "AXValue": "Merhaba"}) == "Merhaba"
      and AX.node_name({"AXRole": "AXTextField", "AXValue": "gizli",
                        "AXPlaceholderValue": "Ara"}) == "Ara")
check("öğe değeri: onay kutusu 0/1, metin alanı, statik metinde boş",
      AX.node_value({"AXRole": "AXCheckBox", "AXValue": 1}) == "1"
      and AX.node_value({"AXRole": "AXTextField", "AXValue": "abc"}) == "abc"
      and AX.node_value({"AXRole": "AXStaticText", "AXValue": "abc"}) == ""
      and AX.node_value({"AXRole": "AXSlider", "AXValue": 5.0}) == "5")
check("eylemler: AXPress → click(+toggle), yazılabilir değer → set_text, odak",
      AX.node_actions("AXCheckBox", ["AXPress"], False, Role.CHECKBOX, True)
      == ["click", "toggle", "focus"]
      and AX.node_actions("AXTextField", ["AXConfirm"], True, Role.ENTRY, True)
      == ["set_text", "focus"]
      and AX.node_actions("AXPopUpButton", ["AXPress", "AXShowMenu"], False, Role.COMBOBOX, False)
      == ["click", "expand"]
      and AX.node_actions("AXGroup", [], False, Role.PANEL, True) == [])

# ------------------------------------------------------------ sahte Quartz: olaylar
WATCH = {"CGEventCreateMouseEvent", "CGEventCreateKeyboardEvent", "CGEventSetFlags",
         "CGEventSetIntegerValueField", "CGEventKeyboardSetUnicodeString", "CGEventPost",
         "CFRelease", "CGEventCreateScrollWheelEvent2", "CGEventCreate"}


def fake_quartz(post_ok: bool = True):
    lib = mock.MagicMock(name="quartz")
    seq = iter(range(1000, 100000))
    for f in ("CGEventCreateMouseEvent", "CGEventCreateKeyboardEvent",
              "CGEventCreateScrollWheelEvent2"):
        getattr(lib, f).side_effect = lambda *a, **k: next(seq)
    lib.CGEventCreate.return_value = 999
    lib.CGEventGetLocation.return_value = M.CGPoint(100.0, 200.0)
    lib.CGPreflightPostEventAccess.return_value = post_ok
    lib.CGPreflightScreenCaptureAccess.return_value = True
    lib.AXIsProcessTrusted.return_value = 0
    unicode_seen: list[list[int]] = []
    lib.CGEventKeyboardSetUnicodeString.side_effect = \
        lambda ev, n, arr: unicode_seen.append(list(arr[:n]))
    q = M._Quartz(loader=lambda name: lib)
    d = M.MacDesktop(quartz=q)
    d._displays = lambda: [(0.0, 0.0, 1440.0, 900.0), (-1280.0, 0.0, 1280.0, 800.0)]
    return lib, d, unicode_seen


def calls(lib, names=WATCH):
    out = []
    for c in lib.mock_calls:
        name = c[0]
        if name in names:
            out.append((name, tuple(c[1])))
    return out


def mouse_events(lib):
    return [(a[1], (a[2].x, a[2].y), a[3]) for n, a in calls(lib) if n == "CGEventCreateMouseEvent"]


with mock.patch.object(M.time, "sleep"):
    lib, d, uni = fake_quartz()
    check("argtypes/restype atandı (CGEventCreateMouseEvent, CGEventPost)",
          lib.CGEventCreateMouseEvent.argtypes[2] is M.CGPoint
          and lib.CGEventCreateMouseEvent.restype is M.CFTypeRef
          and lib.CGEventPost.argtypes == [M.ctypes.c_uint32, M.CFTypeRef]
          and lib.CGEventCreateKeyboardEvent.argtypes[1] is M.ctypes.c_uint16
          and lib.CGEventKeyboardSetUnicodeString.argtypes[1] is M.ctypes.c_ulong)

    d.move(10, 20)
    c = calls(lib)
    check("move: MouseMoved(5) olayı → CGEventPost(HID) → CFRelease",
          [n for n, _ in c] == ["CGEventCreateMouseEvent", "CGEventPost", "CFRelease"]
          and c[0][1][0] is None and c[0][1][1] == 5 and (c[0][1][2].x, c[0][1][2].y) == (10, 20)
          and c[1][1] == (0, 1000) and c[2][1] == (1000,), str(c))
    check("move: ekran dışı → ValueError (olay yok)", raises(ValueError, d.move, 5000, 5000))
    check("move: sol monitör (negatif x) kabul", (d.move(-100, 10) or True))

    lib.reset_mock()
    d.button("left", True)
    d.move(300, 400)
    d.button("left", False)
    d.move(310, 410)
    me = mouse_events(lib)
    check("sürükleme: Down(1) @imleç → LeftMouseDragged(6) → Up(2) → Moved(5)",
          me == [(1, (100.0, 200.0), 0), (6, (300.0, 400.0), 0), (2, (100.0, 200.0), 0),
                 (5, (310.0, 410.0), 0)], str(me))
    cs = [a for n, a in calls(lib) if n == "CGEventSetIntegerValueField"]
    check("button: kCGMouseEventClickState = 1", len(cs) == 2 and all(a[1:] == (1, 1) for a in cs))

    lib.reset_mock()
    d.button("right", True)
    d.move(50, 50)
    d.button("right", False)
    check("sağ sürükleme: Right Down(3)/Dragged(7)/Up(4), düğme 1",
          [(t, b) for t, _, b in mouse_events(lib)] == [(3, 1), (7, 1), (4, 1)])

    lib.reset_mock()
    d.click(500, 300, "left", 2)
    me = mouse_events(lib)
    cs = [a[2] for n, a in calls(lib) if n == "CGEventSetIntegerValueField"]
    check("çift tık: Moved + Down/Up ×2 aynı noktada", [t for t, _, _ in me] == [5, 1, 2, 1, 2]
          and all(p == (500.0, 300.0) for _, p, _ in me), str(me))
    check("çift tık: tık sayısı 1,1,2,2 (kCGMouseEventClickState)", cs == [1, 1, 2, 2], str(cs))

    lib.reset_mock()
    d.click(5, 5, "middle")
    check("orta tık: OtherMouseDown(25)/Up(26), kCGMouseButtonCenter(2)",
          [(t, b) for t, _, b in mouse_events(lib)][1:] == [(25, 2), (26, 2)])
    check("bilinmeyen düğme → ValueError", raises(ValueError, d.click, 1, 1, "dördüncü"))

    lib.reset_mock()
    d.key("cmd+shift+t")
    kb = [a for n, a in calls(lib) if n == "CGEventCreateKeyboardEvent"]
    fl = [a[1] for n, a in calls(lib) if n == "CGEventSetFlags"]
    posts = [a for n, a in calls(lib) if n == "CGEventPost"]
    check("key cmd+shift+t: CGEventCreateKeyboardEvent(None, kod, basma) sırası",
          kb == [(None, 55, True), (None, 56, True), (None, 17, True), (None, 17, False),
                 (None, 56, False), (None, 55, False)], str(kb))
    check("key: her olaya bayrak + HID'e gönderim",
          fl == [CMD, CMD | SHIFT, CMD | SHIFT, CMD | SHIFT, CMD, 0] and len(posts) == 6
          and all(p[0] == M.kCGHIDEventTap for p in posts), str(fl))
    seq = [n for n, _ in calls(lib, {"CGEventCreateKeyboardEvent", "CGEventSetFlags",
                                     "CGEventPost", "CFRelease"})]
    check("key: oluştur → bayrak → gönder → bırak düzeni",
          seq == ["CGEventCreateKeyboardEvent", "CGEventSetFlags", "CGEventPost", "CFRelease"] * 6)

    lib.reset_mock()
    uni.clear()
    d.type_text("Merhaba ğüş 😀\nson")
    kb = [a for n, a in calls(lib) if n == "CGEventCreateKeyboardEvent"]
    check("type_text: Unicode olayları tuş kodu 0, Enter 36 (basma/bırakma çiftleri)",
          kb == [(None, 0, True), (None, 0, False), (None, 36, True), (None, 36, False),
                 (None, 0, True), (None, 0, False)], str(kb))
    joined = "".join(chr(u) for u in uni[0]).encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    check("type_text: CGEventKeyboardSetUnicodeString UTF-16 + bırakmada da aynı dize",
          joined == "Merhaba ğüş 😀" and uni[0] == uni[1] and uni[2] == [ord(x) for x in "son"],
          str(uni))
    check("type_text: Unicode olaylarında bayrak 0 (basılı değiştirici karışmaz)",
          all(a[1] == 0 for n, a in calls(lib) if n == "CGEventSetFlags"))

    lib.reset_mock()
    uni.clear()
    d.key("ğ")
    check("key('ğ'): ANSI'de yok → Unicode olarak yazılır", uni == [[0x11F], [0x11F]], str(uni))

    lib.reset_mock()
    d.scroll(2, -1)
    sc = [a for n, a in calls(lib) if n == "CGEventCreateScrollWheelEvent2"]
    check("scroll(+2 aşağı, -1 sol): satır birimi, wheel1=-1 ×2, wheel2=+1",
          sc == [(None, 1, 2, -1, 0, 0), (None, 1, 2, -1, 0, 0), (None, 1, 2, 0, 1, 0)], str(sc))

    lib.reset_mock()
    pos = d.pointer_pos()
    check("pointer_pos: CGEventCreate(None) + CGEventGetLocation + CFRelease",
          pos == (100, 200) and ("CGEventCreate", (None,)) in calls(lib)
          and ("CFRelease", (999,)) in calls(lib))
    check("paste_combo → cmd+v", d.paste_combo() == "cmd+v")

    lib2, d2, _ = fake_quartz(post_ok=False)
    ok = raises(Unsupported, d2.move, 1, 1) and raises(Unsupported, d2.key, "a") \
        and raises(Unsupported, d2.type_text, "x")
    check("izin yok: move/key/type_text → Unsupported, hiç olay gönderilmez",
          ok and not [c for c in calls(lib2) if c[0] == "CGEventPost"])
    check("izin yok: caps input kullanılamaz + needs",
          (lambda c: not c.available and c.needs)(d2.caps()["input"]))

    # pencere listesi: copy_window_list taklit edilir, AX izni yok → sıra yedeği
    d.q.copy_window_list = lambda opt, rel=0: CG
    ws = d.windows()
    check("windows(): sahte CGWindowList → 3 pencere, etkin Terminal",
          [w.id for w in ws] == ["4521", "4522", "4523"] and ws[0].active)
    check("window_action: AX izni yok → Unsupported (Erişilebilirlik mesajı)",
          raises(Unsupported, d.window_action, "4521", "activate"))
    check("window_action: olmayan pencere → False", d.window_action("99999", "close") is False)
    check("window_action: bilinmeyen eylem → ValueError",
          raises(ValueError, d.window_action, "4521", "zıpla"))

    # terminaller: osascript taklit edilir
    with mock.patch.object(M, "run_osascript") as osa:
        osa.return_value = f"3{RS}eski1\neski2\nsatır1\nsatır2\nsatır3\n\n"
        txt = d.terminal_read("terminal:1234:2", history=1)
        check("terminal_read history: Terminal history + rows → son rows+N satır",
              txt == "eski2\nsatır1\nsatır2\nsatır3" and osa.call_args[0][1] == (1234, 2, "history"), repr(txt))
        osa.reset_mock()
        d.terminal_send("iterm:ABCD-1", "ls -la", enter=True, keys=["C-c"])
        check("terminal_send iTerm2: write text argv'de, betiğe gömülmez",
              osa.call_args[0][1] == ("ABCD-1", "write", "ls -la\x03\r")
              and "ls -la" not in osa.call_args[0][0])
        osa.reset_mock()
        lib.reset_mock()
        uni.clear()
        with mock.patch.object(d, "_wait_front_window") as wf:
            d.terminal_send("terminal:1234:1", "echo ä", enter=True, keys=["up"])
        kb = [a[1] for n, a in calls(lib) if n == "CGEventCreateKeyboardEvent"]
        check("terminal_send Terminal.app: öne al → pencereyi bekle → yaz + up + Enter",
              osa.call_args[0][1] == (1234, 1) and wf.call_args[0][0] == 1234
              and kb == [0, 0, 126, 126, 36, 36]
              and "".join(map(chr, uni[0])) == "echo ä", f"{kb} {uni}")
        check("terminal_send: boş gönderim → ValueError",
              raises(ValueError, d.terminal_send, "terminal:1:1"))
        check("terminal_send: bilinmeyen tuş yazmadan önce reddedilir",
              raises(ValueError, d.terminal_send, "terminal:1:1", "x", False, ["zıpla"]))

    # ekran görüntüsü: sahte screencapture (800x600 piksel, ana ekran 400x300 nokta)
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "args.txt")
    fake = os.path.join(tmp, "screencapture")
    with open(fake, "w") as f:
        f.write(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {os.path.join(HERE, '..', 'core')!r})\n"
                f"open({log!r}, 'w').write(' '.join(sys.argv[1:]))\n"
                "from nexremote.platforms._png import write_png\n"
                "write_png(sys.argv[-1], 800, 600, bytes(800 * 600 * 3))\n")
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
    lib.CGDisplayBounds.return_value = M.CGRect(M.CGPoint(0, 0), M.CGSize(400, 300))
    with mock.patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ.get("PATH", "")}):
        shot = d.screenshot(os.path.join(tmp, "ekran.png"))
        with open(log) as f:
            args = f.read().split()
        check("screenshot: screencapture -x -m, PNG boyutu, scale = 2.0 (Retina)",
              args[:2] == ["-x", "-m"] and (shot.width, shot.height) == (800, 600)
              and shot.scale == 2.0 and shot.origin == (0, 0) and shot.backend == "screencapture",
              f"{args} {shot}")
        lib.CGPreflightScreenCaptureAccess.return_value = False
        check("screenshot: Ekran Kaydı izni yok → Unsupported",
              raises(Unsupported, d.screenshot, os.path.join(tmp, "x.png")))

    check("AxBackend: AX izni yok → capability kullanılamaz + needs",
          (lambda c: not c.available and c.needs)(AX.AxBackend(d).capability()))
    check("gui_backend → AxBackend", isinstance(d.gui_backend(), AX.AxBackend))

ok = sum(results)
print(f"MACOS: {ok}/{len(results)}")
sys.exit(0 if ok == len(results) else 1)
