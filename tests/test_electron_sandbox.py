#!/usr/bin/env python3
"""Electron uygulamasının isimle kontrolü — İZOLE sandbox'ta.

    NEXR_ELECTRON=/yol/electron tests/sandbox.sh python3 tests/test_electron_sandbox.py

Bayraksız açılan Electron ağaç yayınlamaz (hata ve ipucu beklenir);
`nexremote launch` ile açılan aynı uygulamanın butonu ağaçtaki ref'iyle
tıklanır ve sayfadaki değişiklik ağaçtan geri okunur. Electron yoksa atlanır.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

if os.environ.get("NEXR_SANDBOX") != "1":
    sys.exit("bu test yalnızca tests/sandbox.sh içinde çalışır (gerçek masaüstüne dokunmaz)")

from test_input_sandbox import check, results  # noqa: E402

ENV = {**os.environ, "PYTHONPATH": f"{ROOT}/core:{ROOT}/adapters/cli", "NEXREMOTE_CURSOR": "0"}

MAIN_JS = r"""
const {app, BrowserWindow} = require('electron');
app.whenReady().then(() => {
  const w = new BrowserWindow({width: 600, height: 400});
  w.setMenuBarVisibility(false);
  w.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
    '<title>' + process.argv.at(-1) + '</title>' +
    '<button onclick="this.textContent=\'Kaydedildi\'">Kaydet</button>' +
    '<input aria-label="Ad Soyad">'));
});
"""


def nx(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, f"{ROOT}/adapters/cli/nexremote_cli.py", *args],
                          capture_output=True, text=True, timeout=timeout, env=ENV)


def tree_of(ref: str, want: str, timeout: float = 10) -> str:
    """Chromium ağacı sayfa yüklendikçe dolar — beklenen metin gelene dek oku."""
    end = time.monotonic() + timeout
    out = ""
    while time.monotonic() < end:
        out = nx("tree", "--window", ref).stdout
        if want in out:
            break
        time.sleep(0.5)
    return out


def main() -> int:
    electron = os.environ.get("NEXR_ELECTRON") or shutil.which("electron")
    if not electron:
        print("  ⊘ electron yok — NEXR_ELECTRON=/yol/electron ile çalıştırın")
        print("ELECTRON: atlandı")
        return 0
    app = tempfile.mkdtemp(prefix="nexr-electron-")
    with open(os.path.join(app, "main.js"), "w") as f:
        f.write(MAIN_JS)
    with open(os.path.join(app, "package.json"), "w") as f:
        json.dump({"name": "nexr-electron-probe", "main": "main.js"}, f)
    base = [electron, "--ozone-platform=wayland", "--no-sandbox", app]
    pids: list[int] = []
    try:
        # 1) Bayraksız: ağaç boş, ipucu ve launch önerisi
        plain = subprocess.Popen([*base, "duz-pencere"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        pids.append(plain.pid)
        ref = None
        for _ in range(40):
            ws = json.loads(nx("windows", "--json").stdout or "[]")
            ref = next((w["ref"] for w in ws if w["title"] == "duz-pencere"), None)
            if ref:
                break
            time.sleep(0.5)
        check("bayraksız Electron penceresi açıldı", bool(ref))
        if ref:
            time.sleep(2)
            out = nx("tree", "--window", ref).stdout
            check("bayraksız: buton ağaçta yok", "Kaydet" not in out)
            check("bayraksız: Electron ipucu + launch önerisi",
                  "Electron" in out and "nexremote launch" in out, out.strip()[-90:])

        # 2) launch: bayrak eklenir, pencere ref'i döner
        r = nx("launch", "--wait", "25", *base, "bayrakli-pencere", "--json")
        try:
            info = json.loads(r.stdout)
        except ValueError:
            info = {}
        if info.get("pid"):
            pids.append(info["pid"])
        check("launch pencere ref'i döndürdü", r.returncode == 0 and
              str(info.get("window", "")).startswith("win:"), r.stdout.strip()[:120])
        check("launch Electron'u tanıdı ve bayrağı ekledi", info.get("electron") is True and
              "--force-renderer-accessibility" in info.get("argv", []))
        wref = info.get("window")
        if not wref:
            return 1

        out = tree_of(wref, "Kaydet")
        line = next((ln for ln in out.splitlines() if "[button] Kaydet" in ln), "")
        check("buton ağaçta ismiyle ve ref'iyle", "ref=atspi:" in line, line.strip())
        check("isimli alan ağaçta", "[entry] Ad Soyad" in out)
        check("isimsiz kaplar katlandı (boş [panel] satırı yok)",
              not any(ln.strip() in ("[panel]", "[panel]   <click>") for ln in out.splitlines())
              and "[panel]  " not in out)

        # 3) ağaçtaki ref doğrudan tıklanır, sonuç ağaçtan okunur
        if line:
            bref = line.split("ref=")[1].split()[0]
            c = nx("click", bref)
            check("ref ile tıklandı", c.returncode == 0, (c.stdout + c.stderr).strip())
            after = tree_of(wref, "Kaydedildi")
            check("sayfa değişti: buton 'Kaydedildi' oldu", "[button] Kaydedildi" in after)
            hits = nx("find", "Kaydedildi", "--window", wref).stdout
            check("find yeni ismi buluyor", "Kaydedildi" in hits, hits.strip()[:100])
    finally:
        for p in pids:
            try:
                os.killpg(p, 15)
            except OSError:
                pass
        shutil.rmtree(app, ignore_errors=True)

    bad = [n for n, ok, _ in results if not ok]
    print(f"ELECTRON: {len(results) - len(bad)} geçti, {len(bad)} başarısız")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
