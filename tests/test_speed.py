#!/usr/bin/env python3
"""v0.7 hız işleri ve onlarla gelen düzeltmeler — masaüstü gerektirmez.

  * kalıcı CLI servisi: ikinci çağrı servisten, ortam/çıkış kodu/iz aktarımı,
    durdurma; gecikme bütçesi (servisli çağrı servissizden hızlı)
  * do: adımlar tek çağrıda, ilk hatada durur
  * tmux: tam ad eşleşmesi, '-' ile başlayan metin, `&` / `# yorum` ile biten
    komut, geçmişten taşan çıktı, çıkış kodu, tuş adları
  * ajan döngüsü: bir turda birden çok satır, hatada erken DONE reddi
"""
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CORE = os.path.join(ROOT, "core")
CLI = os.path.join(ROOT, "adapters", "cli", "nexremote_cli.py")
sys.path.insert(0, CORE)
sys.path.insert(0, os.path.join(ROOT, "adapters", "cli"))

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("  ✓ " if ok else "  ✗ ") + name + (f" — {detail}" if detail else ""))


tmp = tempfile.mkdtemp(prefix="nexr-speed-")
os.chmod(tmp, 0o700)
ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("NEXREMOTE_")},
       "PYTHONPATH": CORE, "XDG_RUNTIME_DIR": tmp, "NEXREMOTE_HOME": tmp,
       "NEXREMOTE_SERVICE": "1", "NEXREMOTE_SERVICE_IDLE": "60", "TMUX_TMPDIR": tmp,
       "NEXREMOTE_CURSOR": "0"}


def cli(*args, env=None):
    t = time.perf_counter()
    r = subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                       timeout=60, env={**ENV, **(env or {})})
    return r, (time.perf_counter() - t) * 1000


# ------------------------------------------------------------- argümanlar
import nexremote_cli as nc  # noqa: E402

check("--json sona yazılınca öne alınır", nc._norm_argv(["click", "x", "--json"])
      == ["--json", "click", "x"])
check("launch'tan sonraki --json programa kalır",
      nc._norm_argv(["launch", "app", "--json"]) == ["launch", "app", "--json"])

# ------------------------------------------------------------- servis
try:
    r1, t1 = cli("sleep", "0")
    check("ilk çağrı yerelde çalışır ve servisi başlatır", r1.returncode == 0, r1.stderr[-200:])
    for _ in range(100):
        st, _t = cli("service", "status")
        if st.returncode == 0:
            break
        time.sleep(0.1)
    check("servis ayağa kalktı", st.returncode == 0 and '"pid"' in st.stdout, st.stdout + st.stderr)
    r2, _t = cli("sleep", "0")
    st2, _t = cli("service", "status")
    served = __import__("json").loads(st2.stdout).get("served", 0) if st2.returncode == 0 else 0
    check("sonraki çağrı servisten yanıtlanır", r2.returncode == 0 and served >= 1, st2.stdout)

    r, _t = cli("sleep", "0", env={"NEXREMOTE_TRACE": "1"})
    check("NEXREMOTE_TRACE servise iletilir, iz istemcinin stderr'ine gelir",
          "[trace]" in r.stderr and "(servis)" in r.stderr, r.stderr[-200:])
    r, _t = cli("do", "sleep 0", "click-text", "sleep 0")
    check("do: hatalı adımda durur, çıkış kodu iletilir",
          r.returncode != 0 and "[1] ✓ sleep 0" in r.stdout and "[3]" not in r.stdout, r.stdout)
    r, _t = cli("--json", "do", "sleep 0", "sleep 0")
    out = __import__("json").loads(r.stdout)
    check("do --json: adım sonuçları", r.returncode == 0 and out["ok"] and len(out["steps"]) == 2)

    with_svc = [cli("sleep", "0")[1] for _ in range(7)]
    without = [cli("sleep", "0", env={"NEXREMOTE_SERVICE": "0"})[1] for _ in range(7)]
    a, b = statistics.median(with_svc), statistics.median(without)
    check("gecikme bütçesi: servisli çağrı servissizden hızlı", a < b, f"{a:.0f} ms < {b:.0f} ms")

    r, _t = cli("service", "stop")
    r2, _t = cli("service", "status")
    check("service stop", r.returncode == 0 and r2.returncode != 0, r.stdout + r2.stdout)
finally:
    cli("service", "stop")

# ------------------------------------------------------------------ tmux
if shutil.which("tmux"):
    os.environ["TMUX_TMPDIR"] = tmp
    os.environ.setdefault("SHELL", "/bin/bash")
    from nexremote.backends.tui import TuiBackend, _key_name  # noqa: E402
    t = TuiBackend()
    try:
        r = t.run("echo bir", "hiz2", 10)
        r = t.run("echo iki", "hiz", 10)
        check("oturum adı önek eşleşmesi yok (hiz → hiz2'ye gitmez)",
              r.output == "iki" and "iki" not in t.read_screen("hiz2"), repr(r.output))
        r = t.run("false", "hiz", 10)
        check("çıkış kodu", r.exit_code == 1 and not r.timed_out)
        r = t.run("sleep 0.1 &", "hiz", 5)
        check("'&' ile biten komut sözdizimini bozmaz", r.exit_code == 0 and not r.timed_out, repr(r))
        r = t.run("true # yorum", "hiz", 5)
        check("'# yorum' ile biten komut bitişi yutmaz", r.exit_code == 0 and not r.timed_out)
        r = t.run("seq 4000", "hiz", 10)
        check("geçmişten taşan çıktıda da bitiş yakalanır",
              not r.timed_out and r.output.rstrip().endswith("4000"), r.output[-20:])
        t0 = time.monotonic()
        r = t.run("echo hızlı", "hiz", 10)
        check("kısa komut hızlı döner", time.monotonic() - t0 < 1.0 and r.output == "hızlı",
              f"{time.monotonic() - t0:.2f}s")
        check("'-la' metni bayrak sanılmaz", t.send_keys("-la", "hiz"))
        check("tuş adları büyük-küçük harf duyarlı ('end' metin, 'End' tuş)",
              _key_name("end") is None and _key_name("End") == "End"
              and _key_name("ctrl+c") == "C-c" and _key_name("C-\\") == "C-\\")
    finally:
        t.cleanup()
        subprocess.run(["tmux", "kill-server"], env={**os.environ}, capture_output=True)
else:
    print("  ⊘ tmux yok — tmux denetimleri atlandı")

# ------------------------------------------------------------ ajan döngüsü
mock = os.path.join(tmp, "mock.py")
with open(mock, "w") as f:
    f.write('import sys\np = sys.stdin.read()\n'
            'print("DONE: düzeldi" if "[HATA]" in p else '
            '"nexremote sleep 0\\nnexremote click-text\\nnexremote sleep 0\\nDONE: erken")\n')
r = subprocess.run([sys.executable, os.path.join(ROOT, "adapters", "cli", "agent_loop.py"),
                    "--model-cmd", f"{sys.executable} {mock}", "--goal", "test"],
                   capture_output=True, text=True, timeout=120,
                   env={**ENV, "NEXREMOTE_SERVICE": "0"})
lines = [ln for ln in r.stdout.splitlines() if ln.startswith("[1] $")]
check("ajan döngüsü: bir turda birden çok satır, hatada kalan atlanır",
      len(lines) == 2, r.stdout[-300:])
check("ajan döngüsü: hata varken erken DONE reddedilir",
      "düzeldi" in r.stdout and "2 turda" in r.stdout, r.stdout[-200:])

shutil.rmtree(tmp, ignore_errors=True)
ok = sum(1 for _, o in results if o)
print(f"HIZ: {ok} geçti, {len(results) - ok} başarısız")
sys.exit(0 if ok == len(results) else 1)
