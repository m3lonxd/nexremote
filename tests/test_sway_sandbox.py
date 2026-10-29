#!/usr/bin/env python3
"""Gerçek Sway'de uçtan uca giriş — İZOLE başsız bileşimcide.

    tests/sway_sandbox.sh python3 tests/test_sway_sandbox.py

test_wlroots_fake swaymsg'yi taklit eder; bu test gerçek Sway'i başsız
(WLR_BACKENDS=headless) açar ve CLI üzerinden pencere listesini, Sway IPC
faresini (tık, sağ tık, çift tık, kaydırma), wtype ile Türkçe yazmayı,
grim ile ekran görüntüsünü ve pencere eylemlerini olay günlüğünden doğrular.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

if os.environ.get("NEXR_SWAY") != "1":
    sys.exit("bu test yalnızca tests/sway_sandbox.sh içinde çalışır (gerçek masaüstüne dokunmaz)")

ENV = {**os.environ, "PYTHONPATH": f"{ROOT}/core:{ROOT}/adapters/cli", "NEXREMOTE_CURSOR": "0"}
LOG = os.path.join(os.environ["XDG_RUNTIME_DIR"], "probe.jsonl")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def nx(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, f"{ROOT}/adapters/cli/nexremote_cli.py", *args],
                          capture_output=True, text=True, timeout=timeout, env=ENV)


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


def since(n: int, kind: str) -> list[dict]:
    return [e for e in events()[n:] if e.get("type") == kind]


def main() -> int:
    if not shutil.which("swaymsg"):
        print("  ⊘ swaymsg yok\nSWAY: atlandı")
        return 0
    probe = subprocess.Popen([sys.executable, f"{HERE}/input_probe.py", LOG],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        check("deneme penceresi hazır", bool(wait_for(lambda: since(0, "ready"), 15)))
        time.sleep(0.5)

        caps = nx("caps").stdout
        check("caps: Sway IPC faresi", "Sway IPC" in caps,
              next((ln.strip() for ln in caps.splitlines() if "fare" in ln), ""))
        ws = json.loads(nx("windows", "--json").stdout or "[]")
        win = next((w for w in ws if w.get("title") == "nexremote-probe"), None)
        check("windows: pencere Sway ağacından geldi", bool(win), str(win))

        n = len(events())
        r = nx("click", "640", "400")
        hit = wait_for(lambda: since(n, "button"))
        check("sol tık doğru koordinatta", r.returncode == 0 and bool(hit) and hit[0]["button"] == 1
              and abs(hit[0]["x"] - 640) <= 1 and abs(hit[0]["y"] - 400) <= 1,
              str(hit[0] if hit else r.stderr.strip()))

        n = len(events())
        nx("click", "300", "500", "--right")
        hit = wait_for(lambda: since(n, "button"))
        check("sağ tık", bool(hit) and hit[0]["button"] == 3 and abs(hit[0]["x"] - 300) <= 1,
              str(hit[0] if hit else ""))

        n = len(events())
        nx("click", "500", "500", "--double")
        check("çift tık", bool(wait_for(lambda: since(n, "double"))))

        n = len(events())
        nx("scroll", "3", "--at", "700", "450")
        down = wait_for(lambda: since(n, "scroll"))
        n = len(events())
        nx("scroll", "-2")
        up = wait_for(lambda: since(n, "scroll"))
        check("kaydırma aşağı ve yukarı", bool(down) and bool(up)
              and (down[0]["direction"] == 1 or down[0]["dy"] > 0)
              and (up[0]["direction"] == 0 or up[0]["dy"] < 0), f"{down[:1]} {up[:1]}")

        nx("click", "640", "12")                # üstteki giriş alanı
        time.sleep(0.2)
        text = "Merhaba dünya ğüşıöç"
        r = nx("type", text)
        got = wait_for(lambda: [e for e in since(0, "text") if e["value"] == text], 8)
        check("wtype ile Türkçe yazı", r.returncode == 0 and bool(got),
              (since(0, "text") or [{}])[-1].get("value", r.stderr.strip()))

        n = len(events())
        nx("key", "ctrl+a")
        keys = wait_for(lambda: [e for e in since(n, "key") if e["name"] == "a" and e["ctrl"]])
        check("tuş kombinasyonu ctrl+a", bool(keys))

        shot = os.path.join(os.environ["XDG_RUNTIME_DIR"], "s.png")
        r = nx("screenshot", shot, "--json")
        try:
            info = json.loads(r.stdout)
        except ValueError:
            info = {}
        check("ekran görüntüsü grim ile", info.get("backend", "").endswith("grim")
              and info.get("width") == 1280 and os.path.getsize(shot) > 1000 if os.path.exists(shot)
              else False, f"{info.get('backend')} {info.get('width')}x{info.get('height')}")

        if win:
            r = nx("window", win["ref"], "close")
            gone = wait_for(lambda: probe.poll() is not None, 5)
            check("window close pencereyi kapattı", r.returncode == 0 and bool(gone),
                  (r.stdout + r.stderr).strip()[:80])
    finally:
        if probe.poll() is None:
            probe.terminate()
            probe.wait(5)

    bad = [n for n, ok, _ in results if not ok]
    print(f"SWAY: {len(results) - len(bad)} geçti, {len(bad)} başarısız")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
