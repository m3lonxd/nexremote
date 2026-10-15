#!/usr/bin/env python3
"""`nexremote` CLI'ın uçtan uca testi — İZOLE sandbox'ta, gerçek alt süreçlerle.

    tests/sandbox.sh python3 tests/test_cli_sandbox.py

Bir ajanın göreceği yüzeyi sınar: komut satırı → çekirdek → KWin/portal/Konsole
→ sanal masaüstündeki gerçek pencereler. Kullanıcının masaüstüne dokunmaz.
"""
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "core"))

if os.environ.get("NEXR_SANDBOX") != "1":
    sys.exit("bu test yalnızca tests/sandbox.sh içinde çalışır (gerçek masaüstüne dokunmaz)")

from nexremote import kwin, portal_input as pi   # noqa: E402
from nexremote.backends.gui import GuiBackend    # noqa: E402
from test_input_sandbox import approve_dialog, check, events, results, wait_for, LOG  # noqa: E402

ENV = {**os.environ, "PYTHONPATH": f"{ROOT}/core:{ROOT}/adapters/cli", "NEXREMOTE_CURSOR": "0"}


def nx(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, f"{ROOT}/adapters/cli/nexremote_cli.py", *args],
                          capture_output=True, text=True, timeout=timeout, env=ENV)


def nxj(*args: str):
    r = nx(*args, "--json")
    if r.returncode != 0:
        raise RuntimeError(f"nexremote {' '.join(args)} → {r.returncode}: {r.stderr or r.stdout}")
    return json.loads(r.stdout)


def main() -> int:
    procs = [subprocess.Popen([sys.executable, f"{HERE}/input_probe.py", LOG],
                              stderr=subprocess.DEVNULL)]
    try:
        wait_for(lambda: any(e["type"] == "ready" for e in events()), 10)
        time.sleep(0.5)

        print("\n1. Kurulum (izin diyaloğu sandbox'ta otomatik onaylanır)")
        gui = GuiBackend()
        t = threading.Thread(target=lambda: nx("setup", timeout=60), daemon=True)
        t.start()
        approve_dialog(gui)
        t.join(60)
        caps = nxj("caps")
        check("caps: fare+klavye hazır", caps["input"]["available"], caps["input"]["detail"])
        check("caps: KWin pencereleri", caps["windows"]["available"])

        print("\n2. Pencereler")
        ws = nxj("windows")
        probe = [w for w in ws if w.get("title") == "nexremote-probe"]
        check("windows: sonda penceresi KWin ref'iyle listelendi",
              bool(probe) and probe[0]["ref"].startswith("win:"), str(ws)[:120])
        ref = probe[0]["ref"]
        nxj("window", ref, "minimize")
        check("window minimize", wait_for(lambda: kwin.windows()[0]["minimized"], 3) is True)
        nxj("window", ref, "activate")
        check("window activate", wait_for(lambda: not kwin.windows()[0]["minimized"]
                                          and kwin.windows()[0]["active"], 3) is True)
        r = nx("window", "win:{yok}", "close")
        check("olmayan pencere → çıkış kodu 1", r.returncode == 1)

        print("\n3. Fare (koordinat) + imleç geri konur")
        pi.call("MOVE 100 700")
        n0 = len(events())
        nxj("click", "640", "300")
        ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "button"])
        check("click X Y gerçek tık", bool(ev) and (ev[0]["x"], ev[0]["y"]) == (640, 300),
              str((ev or [])[:1]))
        pos = kwin.cursor_pos()
        check("kullanıcı imleci eski yerine döndü", pos == (100, 700), str(pos))
        n0 = len(events())
        nxj("click", "700", "400", "--right")
        ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "button"])
        check("click --right", bool(ev) and ev[0]["button"] == 3)
        shot = nxj("screenshot", "--max-width", "640")
        check("screenshot küçültüldü ve ölçek kaydedildi",
              shot["width"] == 640 and abs(shot["scale"] - 0.5) < 1e-6, f"{shot['width']} {shot['scale']}")
        n0 = len(events())
        nxj("click", "320", "150", "--from-shot")
        ev = wait_for(lambda: [e for e in events()[n0:] if e["type"] == "button"])
        check("click --from-shot görüntü pikselini ekrana çevirdi",
              bool(ev) and (ev[0]["x"], ev[0]["y"]) == (640, 300), str((ev or [])[:1]))
        n0 = len(events())
        nxj("scroll", "2", "--at", "640", "400")
        check("scroll --at", bool(wait_for(lambda: [e for e in events()[n0:] if e["type"] == "scroll"])))

        print("\n4. Klavye")
        target = "İstanbul'da ışık: ğüşöç → ok"
        nxj("type", target)
        hit = wait_for(lambda: [e for e in events() if e["type"] == "text" and e["value"] == target], 8)
        last = [e for e in events() if e["type"] == "text"]
        check("type (Türkçe + ok işareti) birebir", bool(hit),
              repr(last[-1]["value"]) if last else "metin yok")
        nxj("key", "ctrl+a")
        nxj("key", "Delete")
        check("key ctrl+a / Delete", bool(wait_for(lambda: [e for e in events()[-3:]
                                                            if e["type"] == "text" and e["value"] == ""])))
        r = nx("key", "hyper+q")
        check("bilinmeyen değiştirici → hata", r.returncode != 0)

        print("\n5. Masaüstündeki terminaller")
        procs.append(subprocess.Popen(["konsole", "--separate", "--nofork", "-e", "bash",
                                       "--norc", "--noprofile"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        terms = wait_for(lambda: [t for t in nxj("terms") if t["kind"] == "konsole"], 10)
        check("terms: Konsole oturumu bulundu", bool(terms), str(terms)[:120])
        tref = terms[0]["ref"]
        nxj("term-send", tref, "echo NEXR_$((6*7))", "--enter")
        out = wait_for(lambda: "NEXR_42" in nxj("term-read", tref)["screen"], 5)
        check("term-send + term-read (komut çıktısı okundu)", bool(out))
        nxj("term-send", tref, "sleep 60", "--enter")
        busy = wait_for(lambda: [t for t in nxj("terms") if t["ref"] == tref and t["program"] == "sleep"], 5)
        check("terms: ön plandaki program görünüyor (sleep)", bool(busy))
        nxj("term-send", tref, "--key", "C-c")
        idle = wait_for(lambda: [t for t in nxj("terms") if t["ref"] == tref and t["program"] == "bash"], 5)
        check("term-send --key C-c programı durdurdu", bool(idle))
        r = nx("term-read", "konsole:1:99")
        check("olmayan terminal → hata", r.returncode != 0)

        print("\n6. Gözlem")
        snap = nxj("observe")
        check("observe pencere + terminal içeriyor",
              len(snap["windows"]) >= 2 and len(snap["terminal"]["desktop"]) >= 1)
    finally:
        try:
            pi.send("QUIT", timeout=3)
        except OSError:
            pass
        for p in procs:
            p.terminate()

    failed = [r for r in results if not r[1]]
    print(f"\n{'=' * 40}\nSANDBOX CLI: {len(results) - len(failed)} geçti, {len(failed)} başarısız")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
