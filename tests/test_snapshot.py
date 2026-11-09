#!/usr/bin/env python3
"""Anlık görüntü ref'leri + katı yeniden tanıma + girdi kirası — sahte ağaçla.

Gerçek masaüstüne dokunmaz: arka uç ve masaüstü sahtedir. Ağacın değiştiği
(öğe eklendi, silindi, çoğaldı, uygulama indeksi kaydı) durumlarda ref'in ya
DOĞRU öğeye çözüldüğünü ya da reddedildiğini kanıtlar.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
os.environ["NEXREMOTE_HOME"] = tempfile.mkdtemp(prefix="nexremote-snap-test-")
os.environ["NEXREMOTE_CURSOR"] = "0"

from nexremote import snapshot as snap                      # noqa: E402
from nexremote.base import Backend                          # noqa: E402
from nexremote.lease import LeaseTimeout, input_lease       # noqa: E402
from nexremote.models import Capability, Element, Role, Window  # noqa: E402
from nexremote.nexremote import NexRemote                   # noqa: E402

fails = 0


def check(name: str, ok: bool, info: str = "") -> None:
    global fails
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {info}" if info and not ok else ""))
    if not ok:
        fails += 1


def raises(fn, exc) -> tuple[bool, str]:
    try:
        r = fn()
    except exc as e:
        return True, str(e)
    except Exception as e:                       # yanlış tür
        return False, f"{type(e).__name__}: {e}"
    return False, f"hata yok, döndü: {r!r}"


# ----------------------------------------------------------------- sahte ağaç
def btn(name, enabled=True):
    return {"role": Role.BUTTON, "name": name, "actions": ["click"], "enabled": enabled}


def build(win_ref: str, spec: list) -> Element:
    """spec: düğüm sözlükleri; 'kids' alt düğümler. Ref'ler konum yoludur —
    tıpkı AT-SPI gibi, öğe eklenince kayarlar."""
    def mk(d, path):
        e = Element(ref=f"{win_ref}/{'/'.join(map(str, path))}", role=d["role"],
                    name=d.get("name", ""), value=d.get("value", ""),
                    actions=list(d.get("actions", [])), enabled=d.get("enabled", True),
                    bounds=(10 * len(path), 10, 20, 20))
        e.children = [mk(c, path + [i]) for i, c in enumerate(d.get("kids", []))]
        return e
    root = Element(ref=win_ref, role=Role.FRAME, name="Hesap")
    root.children = [mk(d, [i]) for i, d in enumerate(spec)]
    return root


class FakeGui(Backend):
    name = "gui"
    kind = "gui"

    def __init__(self):
        self.wins = [Window(ref="atspi:0:0", title="Hesap", app="calc", pid=42)]
        self.spec: list = []
        self.clicked: list[str] = []

    def capability(self):
        return Capability("gui", True)

    def windows(self):
        return list(self.wins)

    def tree(self, window_ref: str) -> Element:
        ref = window_ref or self.wins[0].ref
        win = next((w for w in self.wins if ref.startswith(w.ref)), None)
        if win is None:
            return Element(ref=ref, role=Role.FRAME, name="pencere bulunamadı")
        root = build(win.ref, self.spec)
        if ref == win.ref:
            return root
        stack = [root]
        while stack:
            n = stack.pop()
            if n.ref == ref:
                return n
            stack.extend(n.children)
        return Element(ref=ref, role=Role.FRAME, name="yok")

    def click(self, ref: str) -> bool:
        node = self.tree(ref)
        self.clicked.append(f"{node.role.value}:{node.name}")
        return True

    def bounds(self, ref):
        return self.tree(ref).bounds

    def focus(self, ref):
        return True

    def set_text(self, ref, text):
        self.clicked.append(f"set:{self.tree(ref).name}={text}")
        return True


class FakeDesktop:
    name = "fake"

    def windows(self):
        return []

    def pointer_available(self):
        return (False, "sahte")


gui = FakeGui()
nr = NexRemote(backends=[gui], desktop=FakeDesktop())


def ref_of(text: str, name: str) -> str:
    line = next(ln for ln in text.splitlines() if f"] {name}" in ln and "ref=@" in ln)
    return line.split("ref=")[1].split()[0]


print("ANLIK GÖRÜNTÜ — ref ve kimlik")
gui.spec = [{"role": Role.PANEL, "kids": [btn("7"), btn("8"), btn("Temizle")]},
            {"role": Role.ENTRY, "name": "", "value": "123", "actions": ["click"]},
            {"role": Role.LABEL if hasattr(Role, "LABEL") else Role.TEXT, "name": "Sonuç"}]
t = nr.tree()
check("ref'ler @sXXXXXXXX:eN biçiminde", "ref=@s" in t and ":e1" in t, t)
check("isimsiz panel ref almaz, ağaçta görünmez", "[panel]" not in t, t)
check("etiket (eylemsiz metin) ref almaz",
      all("ref=" not in ln for ln in t.splitlines() if "Sonuç" in ln), t)
r8 = ref_of(t, "8")

nr.click(r8)
check("değişmeyen ağaçta doğru öğeye tıklar", gui.clicked[-1] == "button:8", str(gui.clicked))

gui.spec[0]["kids"].insert(0, btn("Yeni"))          # yol kaydı: 8 artık /0/2
nr.click(r8)
check("öğe eklenip yol kayınca yine '8'e tıklar (eski yol 'Yeni'yi gösterirdi)",
      gui.clicked[-1] == "button:8", str(gui.clicked))

gui.wins = [Window(ref="atspi:3:0", title="Hesap", app="calc", pid=42)]  # uygulama indeksi kaydı
nr.click(r8)
check("uygulama indeksi kayınca süreç+başlıkla pencereyi bulur",
      gui.clicked[-1] == "button:8", str(gui.clicked))

gui.spec[0]["kids"].append(btn("8"))
ok, msg = raises(lambda: nr.click(r8), snap.AmbiguousTarget)
check("aynı kimlikte ikinci öğe çıkınca AMBIGUOUS_TARGET (tahmin yok)", ok, msg)
gui.spec[0]["kids"].pop()

gui.spec[0]["kids"] = [k for k in gui.spec[0]["kids"] if k["name"] != "8"]
n_before = len(gui.clicked)
ok, msg = raises(lambda: nr.click(r8), snap.StaleRef)
check("öğe silinince STALE_REF", ok, msg)
check("bayat ref'te hiçbir şeye tıklanmadı", len(gui.clicked) == n_before, str(gui.clicked))

ok, msg = raises(lambda: nr.click("e3"), snap.InvalidRef)
check("çıplak 'e3' reddedilir", ok, msg)
ok, msg = raises(lambda: nr.click("@e3"), snap.InvalidRef)
check("çıplak '@e3' reddedilir", ok, msg)
ok, msg = raises(lambda: nr.click("@szzzzzzzz:e1"), snap.StaleRef)
check("bilinmeyen anlık görüntü STALE_REF", ok, msg)

print("ANLIK GÖRÜNTÜ — eşi olan öğeler")
gui.spec = [{"role": Role.LIST, "name": "Dosyalar",
             "kids": [btn("Sil"), btn("Sil"), btn("Sil")]}]
t = nr.tree()
dups = [ln.split("ref=")[1] for ln in t.splitlines() if "] Sil" in ln]
gui.spec[0]["kids"][1]["name"] = "Sil"            # değişiklik yok
nr.click(dups[2])
check("üç eş 'Sil' içinde üçüncüsü sırasıyla çözülür", gui.clicked[-1] == "button:Sil")
resolved = nr._resolve_el(dups[2]).ref
check("üçüncü 'Sil' gerçekten son konumdaki öğe", resolved.endswith("/0/2"), resolved)
gui.spec[0]["kids"].pop(0)
ok, msg = raises(lambda: nr.click(dups[2]), snap.StaleRef)
check("eş sayısı değişince (3→2) hangisi olduğu kanıtlanamaz → STALE_REF", ok, msg)

print("ANLIK GÖRÜNTÜ — değişken ad")
gui.spec = [{"role": Role.ENTRY, "name": "123", "value": "123", "actions": ["click"]}]
t = nr.tree()
re_ = [ln.split("ref=")[1] for ln in t.splitlines() if "[entry]" in ln][0]
gui.spec[0]["name"] = gui.spec[0]["value"] = "99999"
nr.set_text(re_, "5")
check("içeriği değişen giriş alanı yine çözülür (değer kimlik değildir)",
      gui.clicked[-1].startswith("set:"), str(gui.clicked[-1:]))

print("ANLIK GÖRÜNTÜ — otomatik bekleme")
gui.spec = [btn("Kaydet", enabled=False)]
t = nr.tree()
rk = ref_of(t, "Kaydet")
check("devre dışı öğe ağaçta işaretli", "(devre dışı)" in t, t)


def enable_later():
    time.sleep(0.4)
    gui.spec[0]["enabled"] = True


threading.Thread(target=enable_later, daemon=True).start()
t0 = time.monotonic()
nr.click(rk)
dt = time.monotonic() - t0
check("devre dışı düğme etkinleşince tıklanır (bekleme ≥0.4 sn)",
      gui.clicked[-1] == "button:Kaydet" and 0.35 <= dt < 3, f"{dt:.2f} sn")
gui.spec[0]["enabled"] = False
os.environ["NEXREMOTE_WAIT"] = "0.3"
ok, msg = raises(lambda: nr.click(rk), snap.ActionTimeout)
check("etkinleşmeyen öğe TIMEOUT ile reddedilir", ok, msg)
del os.environ["NEXREMOTE_WAIT"]

print("ANLIK GÖRÜNTÜ — bulma ve alt ağaç")
gui.spec = [{"role": Role.TAB, "name": "Genel", "actions": ["click"],
             "kids": [btn("Uygula"), btn("İptal")]},
            {"role": Role.TAB, "name": "Gelişmiş", "actions": ["click"],
             "kids": [btn("Uygula")]}]
hits = nr.find("uygula")
check("find iki 'Uygula' bulur, ref'leri anlık görüntü ref'i",
      len(hits) == 2 and all(snap.is_snapshot_ref(h.ref) for h in hits), str([h.ref for h in hits]))
nr.click(hits[1].ref)
check("find ref'iyle tıklama (atalar ayırt eder: Gelişmiş/Uygula)",
      nr._resolve_el(hits[1].ref).ref.endswith("/1/0"))
t = nr.tree()
rg = ref_of(t, "Genel")
sub = nr.tree(root=rg)
check("--root alt ağacı yalnız o sekmeyi gösterir", "İptal" in sub and "Gelişmiş" not in sub, sub)
ti = nr.tree(interactive=True)
check("-i (etkileşimli) kipinde ref'siz satır yok",
      all("ref=@" in ln for ln in ti.splitlines()[2:] if ln.strip()), ti)

print("ANLIK GÖRÜNTÜ — eski ham ref'ler (geriye uyum)")
nr.click("atspi:3:0/0/1")
check("ham arka uç ref'i hâlâ çalışır", gui.clicked[-1] == "button:İptal", str(gui.clicked[-1:]))

print("GİRDİ KİRASI")
with input_lease():
    with input_lease():
        nested = True
check("aynı süreçte iç içe kira kilitlenmez", nested)
code = ("import os,sys,time;sys.path.insert(0,%r);"
        "from nexremote.lease import input_lease\n"
        "with input_lease():\n print('TUT',flush=True);time.sleep(1.5)") % os.path.join(ROOT, "core")
p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                     env=dict(os.environ))
p.stdout.readline()
t0 = time.monotonic()
ok, msg = raises(lambda: input_lease(timeout=0.3).__enter__(), LeaseTimeout)
check("başka süreç kirayı tutarken LEASE_TIMEOUT", ok, msg)
with input_lease(timeout=5):
    waited = time.monotonic() - t0
check("kira bırakılınca alınır (sırayla)", 1.0 <= waited < 4, f"{waited:.2f} sn")
p.wait(5)

print(f"\nSNAPSHOT SONUÇ: {'GEÇTİ' if not fails else f'{fails} BAŞARISIZ'}")
sys.exit(1 if fails else 0)
