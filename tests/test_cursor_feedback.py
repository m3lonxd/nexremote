#!/usr/bin/env python3
"""Ref ile yapılan eylemler ajan imlecini gösteriyor mu — X gerektirmez.

Sahte GUI arka ucu ve sahte daemon kullanır; gerçek masaüstüne dokunmaz.
"""
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

from nexremote.nexremote import NexRemote  # noqa: E402
from nexremote import agent_cursor as ac  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("  ✓ " if ok else "  ✗ ") + name + (f" — {detail}" if detail else ""))


class FakeGui:
    def __init__(self, box=(100, 200, 40, 20)):
        self.box, self.calls = box, []

    def bounds(self, ref):
        self.calls.append("bounds")
        if isinstance(self.box, Exception):
            raise self.box
        return self.box

    def click(self, ref):
        self.calls.append("click")
        return True

    def set_text(self, ref, text):
        self.calls.append("set_text")
        return True

    def focus(self, ref):
        self.calls.append("focus")
        return True


REAL_BATCH = ac.batch
waits: list[int] = []


def make(gui, sent, available=True, send_error=False, missing=0):
    """missing: daemon'ın ilk kaç bağlantıda yok sayılacağı (başlatma yolu)."""
    nr = NexRemote.__new__(NexRemote)
    nr._gui = lambda: gui
    nr.pointer_available = lambda: (available, "")
    state = {"missing": missing, "started": 0}

    def batch(cmds, wait, timeout=5.0):
        if send_error:
            raise OSError("daemon yok")
        if state["missing"]:
            state["missing"] -= 1
            raise FileNotFoundError("soket yok")
        sent.extend(cmds)
        waits.append(wait)
        return ["OK"] * wait

    def spawn(display, initial=None):
        # daemon arka planda başlar ve komutları kendisi çalıştırır: çağıran beklemez
        state["started"] += 1
        sent.extend(initial or [])
    ac.batch = batch
    ac.spawn_daemon = spawn
    nr._fake = state
    return nr


FULL = ["SHOW", "MOVE 120 210 0.08", "CLICK"]

for name in ("click", "focus"):
    gui, sent = FakeGui(), []
    check(f"{name}(ref) sonucu doğru", getattr(make(gui, sent), name)("r") is True)
    check(f"{name}(ref) imleci öğe ortasına götürür", sent == FULL, str(sent))
    check(f"{name}(ref) imleç eylemden önce", gui.calls.index("bounds") < gui.calls.index(name))

gui, sent = FakeGui(), []
check("set_text sonucu doğru", make(gui, sent).set_text("r", "x") is True)
check("set_text imleci gösterir", sent == FULL, str(sent))

check("tek bağlantı; hiçbir yanıt beklenmez (tık imleci beklemez)",
      waits and all(w == 0 for w in waits), str(waits))

gui, sent = FakeGui(), []
nr = make(gui, sent, missing=1)
nr.click("r")
check("daemon yoksa arka planda başlatılır, komutları o çalıştırır",
      nr._fake["started"] == 1 and sent == FULL and "click" in gui.calls, str(sent))

with mock.patch.dict(os.environ, {"NEXREMOTE_CURSOR_MS": "0"}):
    gui, sent = FakeGui(), []
    make(gui, sent).click("r")
    check("NEXREMOTE_CURSOR_MS=0 → anında (sıfır değil, en küçük süre)",
          sent[1] == "MOVE 120 210 0.001", str(sent))

gui, sent = FakeGui(), []
nr = make(gui, sent)
nr.click("r")
check("daemon varsa başlatma/PING denemesi yok", nr._fake["started"] == 0)

gui, sent = FakeGui(), []
make(gui, sent, available=False).click("r")
check("imleç yoksa eylem yine çalışır", sent == [] and "click" in gui.calls)

gui, sent = FakeGui(), []
make(gui, sent, send_error=True).click("r")
check("daemon hatası eylemi bozmaz", "click" in gui.calls)

for label, box in (("konumsuz", None), ("sıfır boyut", (5, 5, 0, 0)),
                   ("bounds hatası", RuntimeError("yok"))):
    gui, sent = FakeGui(box), []
    make(gui, sent).focus("r")
    check(f"{label} öğede imleç atlanır, eylem çalışır", sent == [] and "focus" in gui.calls)

with mock.patch.dict(os.environ, {"NEXREMOTE_CURSOR": "0"}):
    gui, sent = FakeGui(), []
    make(gui, sent).click("r")
    check("NEXREMOTE_CURSOR=0 imleci kapatır", sent == [] and "click" in gui.calls)



# ------------------------------------------------- Wayland konum çevirisi
class Win:
    def __init__(self, ref, title, pid, bounds):
        self.ref, self.title, self.pid, self.bounds = ref, title, pid, bounds


def offset_nr(frames, wm):
    gui = FakeGui((8, 214, 100, 56))
    gui.windows = lambda: frames
    nr = make(gui, [])
    nr.windows = lambda: wm
    return nr


R = "atspi:23:0/0/9/15"
kcalc = Win("atspi:23:0", "", 7, (0, 0, 640, 480))
nr = offset_nr([kcalc], [Win("win:a", "K Hesap", 7, (639, 285, 642, 509))])
rect, proven = nr._screen_bounds(R)
check("Wayland: pencere + dekorasyon eklenir",
      rect == (8 + 640, 214 + 313, 100, 56), str(rect))
check("Wayland: pencere içindeyse kanıtlı", proven is True)
x11 = Win("atspi:23:0", "", 7, (640, 313, 640, 480))
nr = offset_nr([x11], [Win("win:a", "K", 7, (639, 285, 642, 509))])
check("X11: konum zaten ekranda, fark 0", nr.bounds(R) == (640 + 8, 313 + 214, 100, 56)
      or nr.bounds(R) == (8, 214, 100, 56), str(nr.bounds(R)))
nr = offset_nr([kcalc], [])
check("pencere yöneticisinde yoksa fark 0", nr.bounds(R) == (8, 214, 100, 56))
titled = Win("atspi:23:0", "~ : claude", 7, (0, 0, 911, 480))
nr = offset_nr([titled], [Win("win:b", "başka", 7, (0, 0, 911, 480)),
                          Win("win:a", "~ : claude — Konsole", 7, (100, 100, 913, 509))])
check("aynı pid: başlığa göre seçer", nr.bounds(R)[0] == 8 + 101, str(nr.bounds(R)))
gui = FakeGui((8, 214, 100, 56))
nr = make(gui, [])
check("atspi olmayan ref'e dokunmaz", nr.bounds("uia:1") == (8, 214, 100, 56))

# KANITLANABİLİRLİK: pencere dışına düşen konumda imleç gösterilmez
far = Win("atspi:23:0", "", 7, (0, 0, 640, 480))
gui = FakeGui((5000, 5000, 40, 20)); gui.windows = lambda: [far]
nr = make(gui, []); nr.windows = lambda: [Win("win:a", "K Hesap", 7, (639, 285, 642, 509))]
_, proven = nr._screen_bounds(R)
check("pencere dışı konum kanıtsız", proven is False)
sent = []; nr2 = make(gui, sent); nr2.windows = nr.windows
nr2.click(R)
check("kanıtsız konumda imleç gösterilmez, eylem çalışır",
      sent == [] and "click" in gui.calls)


# ------------------------------------------------------- boşta kapanma
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from nexremote import agent_cursor as ac  # noqa: E402


class FakeCursor:
    name = "sahte"
    closed = False

    def pump(self): pass
    def set_visible(self, v): pass
    def move_to(self, x, y, duration=None): pass
    def pulse(self): pass
    def close(self): self.closed = True


check("varsayılan süre", ac.idle_limit() == ac.IDLE_SEC)
with mock.patch.dict(os.environ, {"NEXREMOTE_CURSOR_IDLE": "x"}):
    check("bozuk süre varsayılana düşer", ac.idle_limit() == ac.IDLE_SEC)
if os.name != "nt":
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(ac, "sock_path", lambda: os.path.join(tmp, "c.sock")):
        cur, ready = FakeCursor(), threading.Event()
        th = threading.Thread(target=ac.serve, args=(cur, ready, 0.6), daemon=True)
        th.start()
        ready.wait(3)
        check("komut boşta sayacını sıfırlar",
              ac.send("SHOW") == "OK" and (time.sleep(0.4) or ac.send("SHOW")) == "OK"
              and (time.sleep(0.4) or th.is_alive()))
        ac.daemon_running()
        th.join(3)
        check("boşta kalınca kapanır", not th.is_alive() and cur.closed)
        check("soket temizlenir", not ac.daemon_running())

    class Recorder(FakeCursor):
        def __init__(self):
            self.log = []
        def set_visible(self, v): self.log.append(("vis", v))
        def move_to(self, x, y, duration=None): self.log.append(("move", x, y))
        def pulse(self):
            time.sleep(0.3)
            self.log.append(("pulse",))

    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(ac, "sock_path", lambda: os.path.join(tmp, "c.sock")):
        cur, ready = Recorder(), threading.Event()
        th = threading.Thread(target=ac.serve, args=(cur, ready, 0.0, 0.3), daemon=True)
        th.start()
        ready.wait(3)
        t0 = time.monotonic()
        out = REAL_BATCH(["SHOW", "MOVE 10 20", "CLICK"], wait=2)
        took = time.monotonic() - t0
        check("batch: iki yanıt döner, halkayı beklemez",
              out == ["OK", "OK"] and took < 0.25, f"{out} {took:.2f}s")
        time.sleep(0.5)
        check("batch: bekletilmeyen CLICK yine çalışır", ("pulse",) in cur.log, str(cur.log))
        cur.log.clear()
        t0 = time.monotonic()
        out = REAL_BATCH(["SHOW", "MOVE 50 60", "CLICK"], wait=0)
        took = time.monotonic() - t0
        check("batch wait=0: hemen döner", out == [] and took < 0.05, f"{took:.3f}s")
        time.sleep(0.6)
        check("batch wait=0: istemci kapansa da üç komut sırayla çalışır",
              cur.log[-2:] == [("move", 50.0, 60.0), ("pulse",)], str(cur.log))
        time.sleep(0.4)
        check("boşta imleç gizlenir, süreç yaşar",
              cur.log[-1] == ("vis", False) and th.is_alive() and not cur.closed, str(cur.log))
        cur.log.clear()
        ac.send("MOVE 30 40")
        check("gizliyken gelen MOVE imleci geri gösterir",
              cur.log[:2] == [("vis", True), ("move", 30.0, 40.0)], str(cur.log))
        ac.send("QUIT")
        th.join(3)
        check("QUIT ile kapanır", not th.is_alive() and cur.closed)

ok = sum(1 for _, o in results if o)
print(f"İMLEÇ GERİ BİLDİRİMİ: {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
