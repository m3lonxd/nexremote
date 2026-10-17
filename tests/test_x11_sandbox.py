#!/usr/bin/env python3
"""X11 sağlayıcısının uçtan uca testi — İZOLE Xvfb'de (pencere yöneticisi yok).

    tests/x11_sandbox.sh python3 tests/test_x11_sandbox.py

Sonda penceresine XTEST ile gerçek fare/klavye olayları gönderir, sondanın
kaydından doğrular; pencere listesi, ekran görüntüsü ve CLI'yı da sınar.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "..", "core")
sys.path.insert(0, CORE)

if os.environ.get("NEXR_SANDBOX_KIND") != "x11":
    sys.exit("bu test yalnızca tests/x11_sandbox.sh içinde çalışır (gerçek masaüstüne dokunmaz)")

from nexremote import NexRemote                    # noqa: E402
from nexremote.platforms import Unsupported, detect  # noqa: E402

LOG = os.path.join(os.environ["XDG_RUNTIME_DIR"], "probe.jsonl")
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def events() -> list[dict]:
    try:
        with open(LOG) as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    return None


def since(n: int) -> list[dict]:
    return events()[n:]


def last_text() -> str:
    t = [e["value"] for e in events() if e["type"] == "text"]
    return t[-1] if t else ""


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, os.path.join(HERE, "..", "adapters", "cli",
                                                        "nexremote_cli.py"), *args],
                          capture_output=True, text=True, timeout=30,
                          env={**os.environ, "PYTHONPATH": CORE})


probe = subprocess.Popen([sys.executable, os.path.join(HERE, "input_probe.py"), LOG],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    check("sonda penceresi açıldı", bool(wait_for(lambda: any(e["type"] == "ready" for e in events()))))
    time.sleep(0.5)

    d = detect()
    check("oturum x11 olarak algılandı", d.name == "x11", d.name)
    caps = {k: c.to_dict() for k, c in d.caps().items()}
    check("caps: pencereler + giriş + ekran", all(caps[k]["available"]
                                                 for k in ("windows", "input", "screenshot")),
          json.dumps({k: v["detail"] for k, v in caps.items()}, ensure_ascii=False))

    wins = d.windows()
    w = next((w for w in wins if w.title == "nexremote-probe"), None)
    check("windows: sonda listelendi (pid + boyut)",
          w is not None and w.pid == probe.pid and w.w >= 1000 and w.h >= 700, str(w))

    d.move(321, 234)
    check("move + pointer_pos", d.pointer_pos() == (321, 234), str(d.pointer_pos()))

    n = len(events())
    d.click(400, 300)
    ev = wait_for(lambda: [e for e in since(n) if e["type"] == "button"])
    check("sol tık doğru koordinata", bool(ev) and ev[0] == {"type": "button", "button": 1,
                                                              "x": 400, "y": 300}, str(ev))
    n = len(events())
    d.click(1000, 650, "right")
    ev = wait_for(lambda: [e for e in since(n) if e["type"] == "button"])
    check("sağ tık", bool(ev) and ev[0].get("button") == 3 and ev[0].get("x") == 1000, str(ev))
    n = len(events())
    d.click(500, 500, count=2)
    check("çift tık", bool(wait_for(lambda: [e for e in since(n) if e["type"] == "double"])))
    n = len(events())
    d.scroll(2)
    ev = wait_for(lambda: [e for e in since(n) if e["type"] == "scroll"])
    check("tekerlek aşağı", bool(ev) and ev[0]["direction"] == 1, str(ev))

    # odak: pencere yöneticisi yok → odak imleci izler; imleç sonda üzerinde
    d.window_action(w.id, "activate")
    text = "Merhaba Dünya ÇĞİÖŞÜ çğıöşü 123 !@# → €"
    d.type_text(text)
    got = wait_for(lambda: last_text() == text and last_text(), 5)
    check("Türkçe + Unicode metin birebir (geçici keysym eşlemesi)", got == text, repr(last_text()))
    d.key("ctrl+a")
    d.key("BackSpace")
    check("ctrl+a + BackSpace temizledi", wait_for(lambda: last_text() == "", 3) is not None
          and last_text() == "", repr(last_text()))
    try:
        d.key("hyper+x")
        check("bilinmeyen değiştirici → ValueError", False)
    except ValueError:
        check("bilinmeyen değiştirici → ValueError", True)

    try:
        d.window_action(w.id, "minimize")
        check("WM yokken minimize → Unsupported", False)
    except Unsupported:
        check("WM yokken minimize → Unsupported", True)
    check("olmayan pencere → False", d.window_action("0x7fffff", "activate") is False)

    tmp = tempfile.mkdtemp()
    os.environ["PATH"] = tmp  # spectacle/scrot görünmesin: Xlib yolu sınansın
    try:
        shot = d.screenshot(os.path.join(tmp, "s.png"))
    finally:
        os.environ["PATH"] = os.defpath
    from PIL import Image
    img = Image.open(shot.path)
    check("ekran görüntüsü (xlib) geçerli PNG, doğru boyut",
          shot.backend == "xlib" and img.size == (1280, 800) == (shot.width, shot.height),
          f"{shot.backend} {img.size}")
    check("ekran görüntüsü boş değil", len(set(img.convert("L").getdata())) > 2)

    # facade + CLI uçtan uca
    nr = NexRemote()
    check("facade windows win:<id>", any(x.ref == f"win:{w.id}" for x in nr.windows()))
    r = cli("--json", "caps")
    check("CLI caps platform=x11", r.returncode == 0 and json.loads(r.stdout)["platform"] == "x11",
          r.stderr.strip()[:200])
    n = len(events())
    r = cli("click", "640", "300")
    ev = wait_for(lambda: [e for e in since(n) if e["type"] == "button"])
    check("CLI click X Y", r.returncode == 0 and bool(ev) and ev[0]["x"] == 640, r.stderr.strip()[:200])
    r = cli("type", "İstanbul'da ışık")
    check("CLI type", r.returncode == 0 and wait_for(lambda: last_text() == "İstanbul'da ışık") is not None,
          repr(last_text()) + r.stderr.strip()[:200])
    r = cli("screenshot", "--max-width", "640")
    check("CLI screenshot küçültme ölçeği", r.returncode == 0
          and (nr.last_screenshot() or None) is not None
          and abs(nr.last_screenshot().scale - 0.5) < 1e-9, r.stdout.strip()[:200] + r.stderr.strip()[:200])

    d.window_action(w.id, "close")
    check("close (WM_DELETE_WINDOW) sondayı kapattı", wait_for(lambda: probe.poll() is not None, 5) is not None)
finally:
    if probe.poll() is None:
        probe.kill()

ok = sum(1 for _, o in results if o)
print(f"SANDBOX X11: {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
