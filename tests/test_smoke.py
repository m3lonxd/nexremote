#!/usr/bin/env python3
"""NexRemote duman testleri — bağımlılıksız (pytest gerekmez).

Çalıştır:  python3 tests/test_smoke.py
Herhangi bir ortamda çalışır; eksik backend'i atlar (skip), hata vermez.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "core")))

PASS, FAIL, SKIP = 0, 0, 0


def check(name: str, fn):
    global PASS, FAIL, SKIP
    try:
        r = fn()
        if r == "skip":
            SKIP += 1
            print(f"  ⊘ {name} (atlandı)")
        else:
            PASS += 1
            print(f"  ✓ {name}")
    except AssertionError as e:
        FAIL += 1
        print(f"  ✗ {name}: {e}")
    except Exception as e:
        FAIL += 1
        print(f"  ✗ {name}: {type(e).__name__}: {e}")


def main() -> int:
    print("NexRemote duman testleri\n")

    from nexremote import NexRemote
    from nexremote.models import Element, Role, Window

    nr = NexRemote()

    # --- modeller ---
    print("modeller:")
    check("Element.render()", lambda: (
        "[button] Kaydet" in Element(ref="x", role=Role.BUTTON, name="Kaydet",
                                     actions=["click"]).render()
        or (_ for _ in ()).throw(AssertionError("render hatalı"))))
    check("Element.to_dict()", lambda: (
        Element(ref="x", role=Role.BUTTON, name="A").to_dict().get("role") == "button"
        or (_ for _ in ()).throw(AssertionError("rol yanlış"))))
    check("Window.to_dict() boşları eler", lambda: (
        "title" not in Window(ref="x").to_dict()
        or (_ for _ in ()).throw(AssertionError())))

    # --- yetenekler ---
    print("\nyetenekler:")
    caps = nr.capabilities()
    check("capabilities() sözlük döner", lambda: (
        isinstance(caps, dict) and "backends" in caps
        or (_ for _ in ()).throw(AssertionError())))

    gui = nr.backends.get("gui")
    tui = nr.backends.get("tui")

    # --- GUI ---
    print("\nGUI backend:")
    if gui and gui.capability().available:
        check("windows() liste döner", lambda: (
            isinstance(nr.windows(), list)
            or (_ for _ in ()).throw(AssertionError())))
        check("tree() metin döner", lambda: (
            isinstance(nr.tree(), str))
            or (_ for _ in ()).throw(AssertionError()))
    else:
        print("  ⊘ GUI atlandı (AT-SPI yok)")

    # --- TUI ---
    print("\nTUI backend:")
    if tui and tui.capability().available:
        def _run():
            r = nr.run("echo nexremote-smoke", session="smoke")
            assert r.exit_code == 0, f"exit {r.exit_code}"
            assert "nexremote-smoke" in r.output, f"çıktı: {r.output!r}"
        check("run() komut çalıştırır", _run)
        check("sessions() liste döner", lambda: (
            isinstance(nr.sessions(), list)
            or (_ for _ in ()).throw(AssertionError())))
        try:
            tui.close()
        except Exception:
            pass
    else:
        print("  ⊘ TUI atlandı")

    # --- input ---
    print("\ngiriş:")
    from nexremote import input as inp
    check("available_tools() sözlük", lambda: (
        isinstance(inp.available_tools(), dict)
        or (_ for _ in ()).throw(AssertionError())))

    print(f"\n{'='*40}\nSONUÇ: {PASS} geçti, {FAIL} başarısız, {SKIP} atlandı")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
