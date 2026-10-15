#!/usr/bin/env python3
"""Portal giriş enjeksiyonunun uçtan uca testi — İZOLE sandbox'ta.

    tests/sandbox.sh python3 tests/test_input_sandbox.py

Sanal KWin'de tam ekran bir sonda penceresi açar, NexRemote giriş servisini
başlatır, izin diyaloğunu NexRemote'un kendi AT-SPI okumasıyla onaylar, sonra
gerçek olayların pencereye ulaştığını sondanın kaydından doğrular.
"""
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

if os.environ.get("NEXR_SANDBOX") != "1":
    sys.exit("bu test yalnızca tests/sandbox.sh içinde çalışır (gerçek masaüstüne dokunmaz)")

from nexremote import portal_input as pi          # noqa: E402
from nexremote.backends.gui import GuiBackend     # noqa: E402

LOG = os.path.join(os.environ["XDG_RUNTIME_DIR"], "probe.jsonl")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
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
        hit = pred()
        if hit:
            return hit
        time.sleep(0.05)
    return None


def approve_dialog(gui: GuiBackend, timeout: float = 20.0) -> bool:
    """İzin diyaloğunu bul → 'geleceğe izin ver' işaretle → Share."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for w in gui.windows():
            if "remote control" not in w.title.lower():
                continue
            restore = [e for e in gui.find("Allow restoring", w.ref) if "toggle" in e.actions]
            if restore and restore[0].value != "1":
                gui.click(restore[0].ref)
            share = [e for e in gui.find("Share", w.ref) if e.role.value == "button"]
            if share:
                return gui.click(share[0].ref)
        time.sleep(0.2)
    return False


def dialog_visible(gui: GuiBackend) -> bool:
    return any("remote control" in w.title.lower() for w in gui.windows())


def main() -> int:
    probe = subprocess.Popen([sys.executable, os.path.join(HERE, "input_probe.py"), LOG],
                             stderr=subprocess.DEVNULL)
    try:
        if not wait_for(lambda: any(e["type"] == "ready" for e in events()), 10):
            print("sonda penceresi açılmadı")
            return 1
        time.sleep(0.5)
        gui = GuiBackend()

        print("\n1. İlk izin")
        starter = threading.Thread(target=pi.ensure, kwargs={"wait": 30}, daemon=True)
        starter.start()
        check("izin diyaloğu AT-SPI ile bulundu ve onaylandı", approve_dialog(gui))
        starter.join(35)
        check("giriş servisi çalışıyor", pi.running())
        status = json.loads(pi.call("STATUS"))
        scr = status["screens"]
        check("ekran yayını alındı", len(scr) == 1 and scr[0]["w"] == 1280,
              json.dumps(scr))
        check("restore_token kaydedildi", bool(pi._load_token()))

        print("\n2. Fare")
        n0 = len(events())
        pi.call("CLICK 400 300")
        ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "button"])
        check("sol tık doğru koordinata ulaştı",
              bool(ev) and ev[0]["button"] == 1 and (ev[0]["x"], ev[0]["y"]) == (400, 300),
              str((ev or [])[:1]))
        n0 = len(events())
        pi.call("CLICK 1000 650 right")
        ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "button"])
        check("sağ tık", bool(ev) and ev[0]["button"] == 3 and (ev[0]["x"], ev[0]["y"]) == (1000, 650),
              str((ev or [])[:1]))
        n0 = len(events())
        pi.call("CLICK 640 400 left 2")
        ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "double"])
        check("çift tık", bool(ev))
        n0 = len(events())
        pi.call("MOVE 640 400")
        pi.call("SCROLL 3")
        ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "scroll"])
        check("tekerlek kaydırma", bool(ev), f"{len(ev or [])} olay")

        print("\n3. Klavye")
        target = "Merhaba Dünya ÇĞİÖŞÜ çğıöşü 123 !@#"
        pi.type_text(target)
        ok = wait_for(lambda: [e for e in events() if e["type"] == "text"
                               and e["value"] == target], 8)
        last = [e for e in events() if e["type"] == "text"]
        check("Türkçe karakterli metin birebir yazıldı", bool(ok),
              repr(last[-1]["value"]) if last else "metin yok")
        n0 = len(events())
        pi.call("KEY ctrl+a")
        ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "key"
                               and e["name"] == "a" and e["ctrl"]])
        check("ctrl+a kısayolu", bool(ev))
        n0 = len(events())
        pi.call("KEY BackSpace")
        ok = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "text" and e["value"] == ""])
        check("seçili metin BackSpace ile silindi", bool(ok))

        print("\n4. Kalıcı izin (servis yeniden başlar, diyalog çıkmamalı)")
        pi.send("QUIT")
        wait_for(lambda: not pi.running(), 5)
        starter = threading.Thread(target=pi.ensure, kwargs={"wait": 15}, daemon=True)
        starter.start()
        saw_dialog = wait_for(lambda: dialog_visible(gui), 4)
        if saw_dialog:
            approve_dialog(gui, 5)
        starter.join(20)
        check("ikinci başlatmada diyalog çıkmadı", not saw_dialog)
        n0 = len(events())
        try:
            pi.call("CLICK 200 200")
            ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "button"])
        except Exception as e:  # noqa: BLE001
            ev, err = None, str(e)
        check("yeniden başlatılan servis tıklıyor", bool(ev))
    finally:
        try:
            pi.send("QUIT", timeout=3)
        except OSError:
            pass
        probe.terminate()

    failed = [r for r in results if not r[1]]
    print(f"\n{'=' * 40}\nSANDBOX GİRİŞ: {len(results) - len(failed)} geçti, {len(failed)} başarısız")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
