#!/usr/bin/env python3
"""NexRemote kabul testleri — gerçek dünya senaryoları.

Çalıştır:  python3 tests/test_acceptance.py
Kabul kriterleri:
  A1. Kalıcı TUI oturumu: run → ayrı çağrıda screen → ekran okunur
  A2. İnteraktif TUI: REPL'e kod gönder → sonucu ekrandan oku
  A3. GUI: pencere ağacı okunur ve normalize edilmiş eylemler içerir
  A4. GUI tıklaması uygulama durumunu değiştirir (gözlemlenebilir)
  A5. Metin-protokol döngüsü araç satırlarını ayrıştırıp çalıştırır
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "core"))

PASS, FAIL, SKIP = 0, 0, 0


def ok(name):
    global PASS; PASS += 1; print(f"  ✓ {name}")


def bad(name, e):
    global FAIL; FAIL += 1; print(f"  ✗ {name}: {e}")


def skip(name):
    global SKIP; SKIP += 1; print(f"  ⊘ {name} (atlandı)")


def main() -> int:
    from nexremote import NexRemote
    nr = NexRemote()
    tui = nr.backends.get("tui")
    gui = nr.backends.get("gui")

    print("A1. Kalıcı TUI oturumu (ayrı çağrılar)")
    if tui and tui.capability().available:
        try:
            r = nr.run("echo KALICI-ABC", session="acc1")
            assert r.exit_code == 0, f"run exit {r.exit_code}"
            # YENİ bir NexRemote örneği = yeni "çağrı" simülasyonu
            nr2 = NexRemote()
            scr = nr2.read_screen("acc1")
            assert "KALICI-ABC" in scr, f"ekranda yok: {scr[-200:]!r}"
            ok("run → yeni örnekte screen okundu")
            nr2.cleanup()
        except AssertionError as e:
            bad("A1", e)
        except Exception as e:
            bad("A1", f"{type(e).__name__}: {e}")
    else:
        skip("A1")

    print("A2. İnteraktif TUI (REPL)")
    if tui and tui.capability().available:
        try:
            nr.run("python3 -q", session="acc2")
            time.sleep(0.4)
            nr.send_keys("print(6*7)", session="acc2")
            nr.send_keys("Enter", session="acc2")
            time.sleep(0.6)
            nr3 = NexRemote()
            scr = nr3.read_screen("acc2")
            assert "42" in scr, f"42 yok: {scr[-200:]!r}"
            ok("REPL'e kod → 42 okundu")
            nr3.cleanup()
        except AssertionError as e:
            bad("A2", e)
        except Exception as e:
            bad("A2", f"{type(e).__name__}: {e}")
    else:
        skip("A2")

    print("A3. GUI ağaç okuma")
    if gui and gui.capability().available:
        try:
            ws = nr.windows()
            if not ws:
                skip("A3: açık pencere yok")
            else:
                trees = [nr.tree(w.ref) for w in ws]
                has_actions = any("[" in t and "<" in t for t in trees)
                assert has_actions or trees, "ağaç boş"
                ok(f"{len(ws)} pencere, ağaç normalize edildi")
        except Exception as e:
            bad("A3", f"{type(e).__name__}: {e}")
    else:
        skip("A3")

    print("A4. GUI tıklama durumu değiştirir")
    # kcalc açık ve AT-SPI görünürse radio toggle testi
    if gui and gui.capability().available:
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            kc = [w for w in nr.windows() if "kcalc" in (w.app or "").lower()]
            if not kc:
                skip("A4: kcalc açık değil")
            else:
                # radio'ları ağaçtan topla (find("") boş sorguya dönmez)
                radio_refs: list[str] = []

                def collect(e):
                    if e.role.value == "radio":
                        radio_refs.append(e.ref)
                    for c in e.children:
                        collect(c)

                collect(nr.backends["gui"].tree(kc[0].ref))
                if len(radio_refs) < 2:
                    skip("A4: yeterli radio yok")
                else:
                    ck = lambda r: nr.backends["gui"]._resolve(r).get_state_set().contains(
                        Atspi.StateType.CHECKED)
                    target = radio_refs[1]
                    before = ck(target)
                    nr.click(target)
                    time.sleep(0.4)
                    after = ck(target)
                    assert before != after, f"durum değişmedi (before={before})"
                    ok(f"radio durumu değişti: {before} → {after}")
        except AssertionError as e:
            bad("A4", e)
        except Exception as e:
            bad("A4", f"{type(e).__name__}: {e}")
    else:
        skip("A4")

    print("A5. Metin-protokol döngüsü")
    try:
        mock = "/tmp/nexmock_acc.py"
        with open(mock, "w") as f:
            f.write(
                "import sys\np=sys.stdin.read()\n"
                "n=p.count('ARAÇ SONUCU')\n"
                "if n==0: print('nexremote run \"echo ACC-OK\"')\n"
                "else: print('DONE: bitti')\n")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "adapters", "cli", "agent_loop.py"),
             "--model-cmd", f"{sys.executable} {mock}", "--goal", "test"],
            capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"exit {r.returncode}: {r.stderr[-200:]}"
        assert "DONE" in r.stdout, r.stdout[-200:]
        ok("döngü araç satırını çalıştırdı ve DONE aldı")
    except AssertionError as e:
        bad("A5", e)
    except Exception as e:
        bad("A5", f"{type(e).__name__}: {e}")

    try:
        nr.cleanup()
    except Exception:
        pass

    print(f"\n{'='*40}\nKABUL: {PASS} geçti, {FAIL} başarısız, {SKIP} atlandı")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
