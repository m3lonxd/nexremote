#!/usr/bin/env python3
"""Ajan imleci daemon'ı — taşıma ve iş kuyruğu (pencere yok, sahte imleç).

Unix soketi (Linux/macOS) ve TCP + belirteç (Windows'un yolu; burada
NEXREMOTE_CURSOR_TCP=1 ile zorlanır) aynı testten geçer: komutlar pencere
iş parçacığında sırayla çalışır, PING kuyruğu beklemez, yanlış belirteç
reddedilir, QUIT temizler.
"""
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

from nexremote import agent_cursor as ac  # noqa: E402

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))


class Dummy(ac.AgentCursorBase):
    name = "sahte"

    def __init__(self):
        self.log, self.threads, self.closed, self.visible = [], set(), False, True
        self.busy = 0
        self.overlap = False

    def _place(self, x, y):
        self.x, self.y = x, y

    def _draw(self, ring=0):
        self.log.append(("draw", ring))

    def move_to(self, x, y, duration=None):
        self.busy += 1
        self.overlap |= self.busy > 1
        self.threads.add(threading.get_ident())
        if x < 0:
            self.busy -= 1
            raise ValueError("ekran dışı")
        super().move_to(x, y, duration)
        self.log.append(("move", int(x), int(y)))
        self.busy -= 1

    def set_visible(self, v):
        self.visible = v

    def close(self):
        self.closed = True


def run(kind: str) -> None:
    print(f"— {kind}")
    tmp = tempfile.mkdtemp()
    os.environ["NEXREMOTE_CURSOR_SOCK"] = os.path.join(tmp, "c.sock")
    os.environ["NEXREMOTE_CURSOR_ENDPOINT"] = os.path.join(tmp, "c.json")
    os.environ["NEXREMOTE_CURSOR_TCP"] = "1" if kind == "tcp" else "0"
    cur = Dummy()
    ready = threading.Event()
    th = threading.Thread(target=ac.serve, args=(cur,), kwargs={"ready": ready}, daemon=True)
    th.start()
    ready.wait(5)
    path = ac.endpoint_file() if kind == "tcp" else ac.sock_path()
    check("uç dosyası yalnızca kullanıcıya açık (0600)",
          stat.S_IMODE(os.stat(path).st_mode) == 0o600, oct(stat.S_IMODE(os.stat(path).st_mode)))
    check("PING", ac.daemon_running())
    check("MOVE yanıtı hareket bitince", ac.send("MOVE 120 80 0.05") == "OK" and cur.log[-1] == ("move", 120, 80))

    out = []
    ts = [threading.Thread(target=lambda i=i: out.append(ac.send(f"MOVE {200 + i} 50 0.03")))
          for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    check("eşzamanlı istemciler sırayla işlendi (çakışma yok)", out == ["OK"] * 4 and not cur.overlap)
    check("pencere işleri tek (serve) iş parçacığında", cur.threads == {th.ident})

    t0 = time.monotonic()
    slow = threading.Thread(target=ac.send, args=("MOVE 900 900 0.6",))
    slow.start()
    time.sleep(0.05)
    check("PING uzun hareketi beklemez", ac.daemon_running() and time.monotonic() - t0 < 0.4)
    slow.join(5)

    check("CLICK → halka kareleri", ac.send("CLICK") == "OK"
          and [e[1] for e in cur.log if e[0] == "draw"][-len(ac.RINGS) - 1:] == [*ac.RINGS, 0])
    check("HIDE / SHOW", ac.send("HIDE") == "OK" and not cur.visible
          and ac.send("SHOW") == "OK" and cur.visible)
    check("bilinmeyen komut ERR", ac.send("ZIPLA") == "ERR")
    check("arka uç hatası ERR döner, daemon yaşar", ac.send("MOVE -5 0") == "ERR" and ac.daemon_running())

    if kind == "tcp":
        import json
        with open(path) as f:
            port = json.load(f)["port"]
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.sendall(b"0000\nMOVE 1 1\n")
        try:
            got = s.recv(16)
        except OSError:
            got = b""
        s.close()
        check("yanlış belirteç: bağlantı kapatılır, komut çalışmaz", got == b"" and cur.x != 1, repr(got))
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.sendall(b"PING\n")
        try:
            got = s.recv(16)
        except OSError:
            got = b""
        s.close()
        check("belirteçsiz PING bile yanıtlanmaz", got == b"", repr(got))

    check("QUIT", ac.send("QUIT") == "BYE")
    th.join(3)
    check("serve döndü, pencere kapandı, uç dosyası silindi",
          not th.is_alive() and cur.closed and not os.path.exists(path))
    check("kapandıktan sonra daemon_running False", not ac.daemon_running())
    shutil.rmtree(tmp, ignore_errors=True)


run("unix")
run("tcp")

# kare çizimi saf Python: cairo olmadan da imleç + halka
img = ac.builtin_arrow()
f0 = ac.compose_frame(img, ac.BOX)
f1 = ac.compose_frame(img, ac.BOX, ring=11)
tip = (ac.HOT * ac.BOX + ac.HOT) * 4
check("yerleşik ok: ucu HOT noktasında opak", f0[tip + 3] == 255)
ring_px = ((ac.HOT) * ac.BOX + ac.HOT - 11) * 4               # halkanın sol noktası
check("halka uçtan 11 px uzakta çizildi", f1[ring_px + 3] > 100 and f0[ring_px + 3] == 0,
      f"alfa {f1[ring_px + 3]}")
check("köşe saydam", f1[-1] == 0 and f0[3] == 0)

print(f"İMLEÇ DAEMON: {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
