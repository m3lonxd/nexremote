#!/usr/bin/env python3
"""AT-SPI hızlı yol + hafif okuma + eylem sonrası fark — gerçek GTK uygulamasıyla.

Yalnızca izole X11 sandbox'ında koşar (Xvfb + kendi D-Bus'ı):
    tests/x11_sandbox.sh python3 tests/test_atspi_fast.py
Python'da gi + Atspi + Gtk 3 yoksa 77 ile atlanır.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))

if os.environ.get("NEXR_SANDBOX") != "1":
    print("  ⊘ yalnızca sandbox'ta (tests/x11_sandbox.sh) — atlandı")
    sys.exit(77)
try:
    import gi
    gi.require_version("Atspi", "2.0")
    gi.require_version("Gtk", "3.0")
except (ImportError, ValueError) as e:
    print(f"  ⊘ gi/Atspi/Gtk yok ({e}) — atlandı")
    sys.exit(77)

from nexremote import snapshot as snap, trace  # noqa: E402
from nexremote.nexremote import NexRemote  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("  ✓ " if ok else "  ✗ ") + name + (f" — {detail}" if detail else ""))


def start_app(extra=0):
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "gtk_fixture.py"), str(extra)],
                         stdout=subprocess.PIPE, env={**os.environ, "GTK_A11Y": "atspi"})
    p.stdout.readline()
    time.sleep(1.0)
    return p


def refs_by_name(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "ref=@" in line and "] " in line:
            name = line.split("] ", 1)[1].split("  ")[0].strip()
            out.setdefault(name, []).append(line.rsplit("ref=", 1)[1].strip())
    return out


def label(nr) -> str:
    tree = nr._read_tree(win_ref)
    return next((n.name for n, _ in snap._walk(tree) if n.name.startswith(("sayaç", "girildi"))), "")


def mode_of(fn) -> list[str]:
    with trace.sink(True) as lines:
        fn()
    return [ln for ln in lines if "çözümleme" in ln]


app = start_app(extra=30)
try:
    nr = NexRemote()
    gui = nr._gui()
    win_ref = next(w.ref for w in gui.windows() if w.title == "NexTest")

    # --- hafif okuma kimlik açısından tam okumayla aynı
    full, light = gui.tree(win_ref), gui.tree(win_ref, light=True)
    kf = [k for _, k in snap._index(full)[0]]
    kl = [k for _, k in snap._index(light)[0]]
    check("hafif okuma aynı kimlik anahtarlarını üretir", kf == kl and len(kf) > 10, f"{len(kf)}")
    has_bounds = any(n.bounds for n, _ in snap._walk(light))
    check("hafif okuma konum okumaz", not has_bounds)
    entry = next((n for n, _ in snap._walk(full) if n.role.value == "entry"), None)
    check("adsız giriş alanı ağaçta (eskiden düşüyordu)", entry is not None)

    def timed(fn, n=5):
        best = 1e9
        for _ in range(n):
            t = time.perf_counter()
            fn()
            best = min(best, time.perf_counter() - t)
        return best * 1000
    t_full = timed(lambda: gui.tree(win_ref))
    t_light = timed(lambda: gui.tree(win_ref, light=True))
    check("hafif okuma tam okumadan hızlı", t_light < t_full, f"{t_light:.1f} ms < {t_full:.1f} ms")

    # --- hızlı yol
    text = nr.tree(win_ref)
    refs = refs_by_name(text)
    inc = refs["Arttır"][0]
    modes = mode_of(lambda: nr.click(inc))
    time.sleep(0.2)
    check("ref tıklaması hızlı yoldan çözülür", modes and "yol=hızlı" in modes[0], str(modes))
    check("tık uygulandı (sayaç 1)", label(nr) == "sayaç 1", label(nr))

    entry_line = [ln for ln in text.splitlines() if "[entry]" in ln and "ref=" in ln]
    check("giriş alanının ref'i var", bool(entry_line))

    t_res = timed(lambda: nr._resolve_el(inc), 5)
    os.environ["NEXREMOTE_STRICT"] = "1"
    t_strict = timed(lambda: nr._resolve_el(inc), 5)
    del os.environ["NEXREMOTE_STRICT"]
    check("hızlı yol tam okumadan hızlı", t_res < t_strict, f"{t_res:.1f} ms < {t_strict:.1f} ms")

    # --- sıra kayması: yol başka öğeyi gösterir → kimlik tutmaz → tam okuma, doğru öğe
    nr.click(refs["Önüne ekle"][0])
    time.sleep(0.3)
    modes = mode_of(lambda: nr.click(inc))
    time.sleep(0.2)
    check("yol kayınca tam okumaya düşer", modes and "yol=tam" in modes[0], str(modes))
    check("kayan ref yine doğru öğeye basar (sayaç 2)", label(nr) == "sayaç 2", label(nr))

    # --- kaybolan öğe
    text = nr.tree(win_ref)
    refs = refs_by_name(text)
    nr.click(refs["Sil"][0])
    time.sleep(0.3)
    try:
        nr.click(refs["Hedef"][0])
        check("kaybolan öğe → STALE_REF", False)
    except snap.StaleRef:
        check("kaybolan öğe → STALE_REF", True)

    # --- devre dışı öğe beklenir
    nr.click(refs["Etkinleştir"][0])
    t = time.monotonic()
    ok = nr.click(refs["Kapat"][0])
    check("devre dışı öğe etkinleşince basılır", ok and 0.2 < time.monotonic() - t < 3,
          f"{time.monotonic() - t:.2f}s")

    # --- ikiz: hızlı yol saklı yoldakini kanıtlar; katı kip belirsizliği yakalar
    text = nr.tree(win_ref)
    refs = refs_by_name(text)
    inc = refs["Arttır"][0]
    nr.click(refs["Çoğalt"][0])
    time.sleep(0.3)
    before = label(nr)
    modes = mode_of(lambda: nr.click(inc))
    time.sleep(0.2)
    check("ikiz sonradan çıkınca hızlı yol saklı yoldakine basar",
          modes and "yol=hızlı" in modes[0] and label(nr) != before, str(modes))
    os.environ["NEXREMOTE_STRICT"] = "1"
    try:
        nr.click(inc)
        check("NEXREMOTE_STRICT=1 ikizi AMBIGUOUS_TARGET ile reddeder", False)
    except snap.AmbiguousTarget:
        check("NEXREMOTE_STRICT=1 ikizi AMBIGUOUS_TARGET ile reddeder", True)
    del os.environ["NEXREMOTE_STRICT"]

    # --- click_text
    try:
        nr.click_text("Arttır", window_ref=win_ref)
        check("click_text: iki aday → AMBIGUOUS_TARGET", False)
    except snap.AmbiguousTarget as e:
        check("click_text: iki aday → AMBIGUOUS_TARGET", len(e.details["candidates"]) == 2)
    r = nr.click_text("ekle", window_ref=win_ref)
    check("click_text: tam ad (büyük-küçük harf duyarsız) tercih edilir",
          r["ok"] and r["name"] == "Ekle", str(r))

    # --- after: değişen öğeler
    out = nr.after(window_ref=win_ref)
    check("after: yeni düğme + ref ile yazılır", "+ [button] Tamam  ref=@" in out, out[:300])
    out = nr.after(window_ref=win_ref)
    check("after: değişiklik yoksa söyler", "değişiklik yok" in out, out[:200])

    # --- wait
    nr.click_text("Gecikmeli", window_ref=win_ref)
    t = time.monotonic()
    w = nr.wait_for(element="Geç geldi", window_ref=win_ref, timeout=5)
    check("wait --element görünene kadar bekler ve ref verir",
          w.get("ref", "").startswith("@s") and 0.3 < time.monotonic() - t < 3, str(w))
    try:
        nr.wait_for(element="hiç yok", window_ref=win_ref, timeout=0.4)
        check("wait süre dolunca TIMEOUT", False)
    except snap.ActionTimeout:
        check("wait süre dolunca TIMEOUT", True)
    text = nr.tree(win_ref)
    refs = refs_by_name(text)
    gone_ref = refs["Tamam"][0]
    w = nr.wait_for(window_title="NexTest", timeout=2)
    check("wait --window-title", w.get("title") == "NexTest", str(w))

    # --- set_text / fill
    eref = [ln for ln in text.splitlines() if "[entry]" in ln][0].rsplit("ref=", 1)[1].strip()
    ok = nr.fill(eref, "merhaba", enter=True)
    time.sleep(0.3)
    check("fill: metin yazılır ve Enter basılır", ok and label(nr) == "girildi: merhaba", label(nr))

    # --- find yalnızca sonuç çıkan pencere için anlık görüntü yazar
    d = snap.store_dir()
    n0 = len(os.listdir(d))
    hits = nr.find("hiçbir şeyle eşleşmez")
    check("sonuçsuz find anlık görüntü yazmaz", not hits and len(os.listdir(d)) == n0)

    # --- uzun ömürlü süreç: GLib kuyruğu boşaltılınca önbellek güncel kalır
    nr2 = NexRemote(long_lived=True)
    gui2 = nr2._gui()
    w2 = next(w.ref for w in gui2.windows() if w.title == "NexTest")
    def tamam(root):
        return sum(1 for n, _ in snap._walk(root) if n.name == "Tamam")
    n_before = tamam(gui2.tree(w2))
    nr2.click_text("Ekle", window_ref=w2)
    time.sleep(0.3)
    with nr2._scope():
        n_after = tamam(nr2._read_tree(w2))
    check("uzun ömürlü süreç yeni düğmeyi görür (önbellek bayatlamaz)",
          n_after == n_before + 1, f"{n_before} → {n_after}")
finally:
    app.kill()

ok = sum(1 for _, o in results if o)
print(f"ATSPI HIZLI YOL: {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
