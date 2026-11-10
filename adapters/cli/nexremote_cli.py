#!/usr/bin/env python3
"""NexRemote CLI adaptörü — EVRENSEL TABAN.

Her harness (Claude Code, Cursor, Aider, düz kabuk, proxy'lenmiş CLI'lar)
kabuk komutu çalıştırabildiği sürece NexRemote'u kullanabilir. MCP gerekmez,
tool-calling gerekmez, özel SDK gerekmez. Sadece `nexremote ...` komutları.

Kullanım:
  nexremote setup                      ilk kurulum: erişilebilirlik + tek seferlik izin
  nexremote caps                       ne yapabildiğimizi anlatır
  nexremote observe                    pencereler + terminaller (modele besle)

  nexremote windows                    ekrandaki tüm pencereler (win:{...} ref)
  nexremote window REF activate|close|minimize
  nexremote launch [--wait N] PROG ... programı ağacı açık başlatır (Electron'a bayrak ekler)
  nexremote tree [--window REF] [-i]   erişilebilirlik ağacı (LLM dostu metin, ref'li)
  nexremote find "Kaydet" [--window R] öğe arar → ref
  nexremote click REF                  öğeye isimle tıklar (ajan imleci görünür, faren oynamaz)
  nexremote click X Y [--right|--middle] [--double]   koordinata gerçek tık
  nexremote click-text "Kaydet"        adıyla bul + tıkla (tek okuma; belirsizse reddeder)
  nexremote fill REF "metin" [--enter] alanı doldur (+ Enter)
  nexremote move X Y | scroll N [--at X Y] | drag X1 Y1 X2 Y2
  nexremote key "ctrl+s"               tuş kombinasyonu
  nexremote type "metin"               odaktaki alana yazar (Türkçe dahil)
  nexremote set-text REF "metin"       alanın içeriğini değiştirir
  nexremote screenshot [PATH]          ekran görüntüsü (koordinatlar = tık koordinatı)

  nexremote do 'click @s…:e4' 'type "7"' 'key Return' --after
                                       birden çok adımı TEK çağrıda çalıştırır
  nexremote wait --element "Tamam" | --window-title "Kaydet" | --gone REF [--timeout S]
  ... --after                          eylemden sonra neyin değiştiğini de yazar

  nexremote terms                      masaüstündeki açık terminaller
  nexremote term-read REF [--history N]
  nexremote term-send REF "metin" [--enter] [--key C-c ...]

  nexremote run "ls -la" [--session S] [--timeout T]   kendi tmux oturumumuz
  nexremote screen | keys "C-c" | sessions | cleanup
  nexremote bench [-n N] [--cli]       gecikme ölçümü (p50/p95)
  nexremote service status|stop        arka plan servisi (CLI çağrılarını hızlandırır)
  nexremote skill                      LLM'e verilecek yetenek talimatı

Global: --json  (makine-okunur çıktı)
Ortam: NEXREMOTE_TRACE=1 (adım süreleri), NEXREMOTE_SERVICE=0 (servisi kapat)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shlex
import socket
import sys
import time

# çekirdeği bul
_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = None
for cand in (os.path.join(_HERE, "..", "..", "core"),
             os.path.join(_HERE, "..", "core")):
    cand = os.path.abspath(cand)
    if os.path.isdir(os.path.join(cand, "nexremote")):
        _CORE = cand
        if cand not in sys.path:
            sys.path.insert(0, cand)
        break


def _get_nr(long_lived: bool = False):
    from nexremote import NexRemote
    return NexRemote(long_lived=long_lived)


def _out(obj, as_json: bool, text_render=None) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        if text_render is not None:
            print(text_render)
        elif isinstance(obj, (dict, list)):
            print(json.dumps(obj, ensure_ascii=False, indent=2))
        else:
            print(obj)


# --------------------------------------------------------------------- komutlar
def cmd_caps(nr, a) -> None:
    caps = nr.capabilities()
    if a.json:
        _out(caps, True)
        return
    print(f"NexRemote yetenekleri ({caps['platform']}):")
    rows = [("pencereler", caps["windows"]), ("fare+klavye", caps["input"]),
            ("ekran görüntüsü", caps["screenshot"]), ("terminaller", caps["terminals"])]
    rows += [(n, c) for n, c in caps["backends"].items()]
    rows.append(("ajan imleci", caps["pointer"]))
    for name, c in rows:
        print(f"  {'✓' if c['available'] else '✗'} {name}: {c['detail']}")
        if c.get("needs"):
            print(f"      eksik: {', '.join(c['needs'])}")


def cmd_observe(nr, a) -> None:
    """Tam durum anlık görüntüsü — bir modele tek seferde beslenecek özet."""
    snap = {"capabilities": nr.capabilities(), "windows": [], "terminal": {}}
    try:
        for w in nr.windows():
            snap["windows"].append(w.to_dict())
    except Exception as e:
        snap["windows_error"] = str(e)
    try:
        snap["terminal"]["desktop"] = nr.terminals()
    except Exception as e:
        snap["terminal"]["desktop_error"] = str(e)
    try:
        snap["terminal"]["sessions"] = nr.sessions()
    except Exception:
        pass
    if a.window:
        try:
            snap["tree"] = nr.tree(a.window)
        except Exception as e:
            snap["tree_error"] = str(e)
    if a.json:
        _out(snap, True)
        return
    caps = snap["capabilities"]
    print("=== NexRemote gözlem ===")
    print(f"fare+klavye: {caps['input']['detail']}")
    print(f"\nPencereler ({len(snap['windows'])}) — * etkin:")
    for w in snap["windows"]:
        b = w.get("bounds")
        geo = f"{b[2]}x{b[3]}+{b[0]}+{b[1]}" if b else ""
        print(f"  {'*' if w.get('focused') else ' '} {w.get('app','?')[:22]:<22} {geo:<20} "
              f"{w.get('title','')[:60]}\n      ref={w['ref']}")
    terms = snap["terminal"].get("desktop") or []
    if terms:
        print(f"\nAçık terminaller ({len(terms)}):")
        for tm in terms:
            print(f"  {tm['ref']:<20} {tm['program']:<14} {tm['cwd']:<22} {tm['title'][:40]}")
    if snap["terminal"].get("sessions"):
        print(f"\nNexRemote tmux oturumları: {', '.join(snap['terminal']['sessions'])}")
    if snap.get("tree"):
        print("\n--- Pencere ağacı ---")
        print(snap["tree"][:4000])


def cmd_windows(nr, a) -> None:
    ws = nr.windows()
    if a.json:
        _out([w.to_dict() for w in ws], True)
        return
    for w in ws:
        b = w.bounds
        geo = f"{b[2]}x{b[3]}+{b[0]}+{b[1]}" if b else ""
        print(f"{'*' if w.focused else ' '} {w.ref}\t{w.app}\t{geo}\t{w.title}")


def cmd_window(nr, a) -> None:
    ok = nr.window_action(a.ref, a.action)
    _out({"ok": ok, "ref": a.ref, "action": a.action}, a.json,
         f"{a.action}: {'tamam' if ok else 'pencere bulunamadı'}")
    if not ok:
        raise SystemExit(1)


def cmd_tree(nr, a) -> None:
    ref = a.window
    try:
        txt = nr.tree(ref, interactive=a.interactive, root=a.root)
    except Exception as e:
        code = getattr(e, "code", None)
        _out({"error": str(e), **({"code": code} if code else {})}, a.json, f"hata: {e}")
        raise SystemExit(1)
    if a.json:
        print(json.dumps({"window": ref, "tree": txt}, ensure_ascii=False, indent=2))
    else:
        print(txt)


def cmd_launch(nr, a) -> None:
    argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
    r = nr.launch(argv, a.wait)
    if r["window"]:
        txt = f"açıldı: {r['window']}\t{r['title']}" + ("  (Electron: ağaç açık)" if r["electron"] else "")
    else:
        txt = f"pid {r['pid']}: {r['note']}"
    _out(r, a.json, txt)
    if not r["window"]:
        raise SystemExit(1)


def cmd_find(nr, a) -> None:
    hits = nr.find(a.query, a.window)
    data = [{"ref": e.ref, "role": e.role.value, "name": e.name,
             "value": e.value, "actions": e.actions} for e in hits]
    if a.json:
        _out(data, True)
        return
    if not data:
        print(f"'{a.query}' için sonuç yok")
        return
    for d in data:
        act = f"  <{','.join(d['actions'])}>" if d["actions"] else ""
        print(f"{d['ref']}\t[{d['role']}] {d['name']}{act}")


def cmd_click(nr, a) -> None:
    btn = "right" if a.right else "middle" if a.middle else "left"
    if len(a.target) == 2 and all(x.lstrip("-").isdigit() for x in a.target):
        x, y = nr.screen_point(int(a.target[0]), int(a.target[1]), a.from_shot)
        nr.click_at(x, y, button=btn, count=2 if a.double else 1,
                    restore_pointer=not a.keep_pointer)
        _out({"ok": True, "x": x, "y": y, "button": btn}, a.json, f"tıklandı ({x},{y})")
        return
    if len(a.target) != 1:
        raise ValueError("kullanım: click REF  |  click X Y")
    ref = a.target[0]
    if btn != "left" or a.double:
        b = nr.bounds(ref)
        if not b:
            raise RuntimeError("öğenin ekran konumu yok; sağ/çift tık için koordinat gerekir")
        nr.click_at(b[0] + b[2] // 2, b[1] + b[3] // 2, button=btn,
                    count=2 if a.double else 1, restore_pointer=not a.keep_pointer)
        ok = True
    else:
        ok = nr.click(ref)
    _out({"ok": ok, "ref": ref}, a.json, "tıklandı" if ok else "tıklanamadı")
    if not ok:
        raise SystemExit(1)


def cmd_click_text(nr, a) -> None:
    r = nr.click_text(a.text, window_ref=a.window, role=a.role, exact=a.exact)
    _out(r, a.json, f"tıklandı: [{r['role']}] {r['name']}  ref={r['ref']}"
         if r["ok"] else "tıklanamadı")
    if not r["ok"]:
        raise SystemExit(1)


def cmd_fill(nr, a) -> None:
    ok = nr.fill(a.ref, a.text, enter=a.enter)
    _out({"ok": ok, "ref": a.ref}, a.json, "dolduruldu" if ok else "doldurulamadı")
    if not ok:
        raise SystemExit(1)


def cmd_move(nr, a) -> None:
    nr.move(a.x, a.y)
    _out({"ok": True, "x": a.x, "y": a.y}, a.json, f"imleç ({a.x},{a.y})")


def cmd_scroll(nr, a) -> None:
    nr.scroll(a.steps, a.horizontal, at=tuple(a.at) if a.at else None)
    _out({"ok": True, "steps": a.steps}, a.json, f"kaydırıldı ({a.steps})")


def cmd_drag(nr, a) -> None:
    nr.drag(a.x1, a.y1, a.x2, a.y2)
    _out({"ok": True}, a.json, f"sürüklendi ({a.x1},{a.y1}) → ({a.x2},{a.y2})")


def cmd_set_text(nr, a) -> None:
    ok = nr.set_text(a.ref, a.text)
    _out({"ok": ok, "ref": a.ref}, a.json, "yazıldı" if ok else "yazılamadı")
    if not ok:
        raise SystemExit(1)


def cmd_key(nr, a) -> None:
    ok = nr.key(a.combo)
    _out({"ok": ok, "combo": a.combo}, a.json, "gönderildi" if ok else "gönderilemedi")
    if not ok:
        raise SystemExit(1)


def cmd_type(nr, a) -> None:
    ok = nr.type_text(a.text)
    _out({"ok": ok}, a.json, "yazıldı" if ok else "yazılamadı")
    if not ok:
        raise SystemExit(1)


def cmd_wait(nr, a) -> None:
    r = nr.wait_for(element=a.element, window_title=a.window_title, gone=a.gone,
                    window_ref=a.window, timeout=a.timeout)
    if "ref" in r:
        txt = f"göründü: [{r['role']}] {r['name']}" + (f"  ref={r['ref']}" if r.get("ref") else "")
    elif "window" in r:
        txt = f"pencere: {r['window']}  {r['title']}"
    else:
        txt = f"kayboldu: {r['gone']}"
    _out(r, a.json, txt)


def cmd_sleep(nr, a) -> None:
    time.sleep(max(0.0, min(a.seconds, 60.0)))
    _out({"ok": True, "slept": a.seconds}, a.json, f"beklendi ({a.seconds:g} sn)")


def cmd_terms(nr, a) -> None:
    ts = nr.terminals()
    if a.json:
        _out(ts, True)
        return
    if not ts:
        print("(açık terminal bulunamadı — Konsole veya tmux gerekir)")
    for tm in ts:
        print(f"{tm['ref']:<20} {tm['program']:<14} {tm['cwd']:<24} {tm['title']}")


def cmd_term_read(nr, a) -> None:
    txt = nr.terminal_read(a.ref, a.history)
    note = ""
    if a.history and not nr.terminal_history_supported(a.ref):
        note = "bu terminal yalnızca görünen ekranı verir; geçmiş okunamadı"
    if a.json:
        _out({"ref": a.ref, "screen": txt, **({"note": note} if note else {})}, True)
        return
    print(txt)
    if note:
        print(f"\n[not: {note}]", file=sys.stderr)


def cmd_term_send(nr, a) -> None:
    nr.terminal_send(a.ref, a.text or "", enter=a.enter, keys=a.key)
    _out({"ok": True, "ref": a.ref}, a.json, "gönderildi")


def cmd_setup(nr, a) -> None:
    """İlk kurulum: erişilebilirlik + giriş izinleri (platforma göre)."""
    steps = nr.setup()
    if a.json:
        _out([{"step": s, "ok": o, "detail": d} for s, o, d in steps], True)
    else:
        for s, o, d in steps:
            print(f"  {'✓' if o else '✗'} {s}" + (f" — {d}" if d and not o else ""))
    if not all(o for _, o, _ in steps):
        raise SystemExit(1)


def _pil_ok() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def cmd_screenshot(nr, a) -> None:
    mw = a.max_width
    if mw is None:
        # varsayılan: modele giden görüntü küçük olsun (Pillow varsa)
        mw = int(os.environ.get("NEXREMOTE_SHOT_MAX_WIDTH", "1568")) if _pil_ok() else 0
    s = nr.screenshot(a.path, window_ref=a.window, max_width=mw)
    note = ""
    if s.scale != 1.0 or s.origin != (0, 0):
        note = (f"\ngörüntü ölçeği {s.scale:.4f}, orijin {s.origin}: görüntüdeki noktaya "
                f"tıklamak için `nexremote click X Y --from-shot`")
    _out(s.to_dict(), a.json, f"ekran görüntüsü: {s.path} ({s.width}x{s.height}){note}")


def cmd_run(nr, a) -> None:
    r = nr.run(a.command, a.session, a.timeout)
    _out(r.to_dict(), a.json, r.output)
    # Çıkış kodu çağırana iletilir: ajan `false` ile `true`'yu ayırt edebilsin.
    if r.timed_out:
        if not a.json:
            print(f"[zaman aşımı: {a.timeout:g} sn — komut oturumda sürüyor; "
                  f"`nexremote screen --session {a.session}` ile izleyin]", file=sys.stderr)
        raise SystemExit(124)
    if r.exit_code != 0:
        if not a.json:
            print(f"[çıkış kodu {r.exit_code}]", file=sys.stderr)
        raise SystemExit(r.exit_code if 0 < r.exit_code < 256 else 1)


def cmd_screen(nr, a) -> None:
    txt = nr.read_screen(a.session)
    if a.json:
        _out({"session": a.session, "screen": txt}, True)
    else:
        print(txt)


def cmd_keys(nr, a) -> None:
    ok = nr.send_keys(a.keys, a.session)
    _out({"ok": ok}, a.json, "gönderildi" if ok else "gönderilemedi")
    if not ok:
        raise SystemExit(1)


def cmd_sessions(nr, a) -> None:
    ss = nr.sessions()
    _out(ss, a.json, "\n".join(ss) if ss else "(oturum yok)")


def cmd_cleanup(nr, a) -> None:
    killed = nr.cleanup()
    _out(killed, a.json,
         ("kapatıldı: " + ", ".join(killed)) if killed else "(kapatılacak oturum yok)")


def cmd_cursor(nr, a) -> None:
    """Görünür ajan imlecini (x,y)'ye götür; --click yalnızca halka gösterir."""
    ok, msg = nr.pointer_available()
    if not ok:
        _out({"ok": False, "error": msg}, a.json, f"imleç kullanılamaz: {msg}")
        return
    if a.click:
        nr.cursor(a.x, a.y, click=True, button=a.button)
        _out({"ok": True, "x": a.x, "y": a.y, "ring": True}, a.json,
             f"imleç ({a.x},{a.y}) → tık halkası gösterildi (gerçek tık değil: click X Y)")
    else:
        nr.cursor_move(a.x, a.y, steps=a.steps, duration=a.duration)
        _out({"ok": True, "x": a.x, "y": a.y}, a.json,
             f"imleç ({a.x},{a.y}) → taşındı")


def cmd_cursor_hide(nr, a) -> None:
    ok = nr.cursor_hide()
    _out({"ok": bool(ok)}, a.json, "imleç gizlendi" if ok else "(görünür imleç yok)")


def cmd_point(nr, a) -> None:
    """Öğeyi (ref) bul ve gerçekten tıkla; ajan imleci varsa üzerine gider."""
    b = nr.bounds(a.ref)
    ok = nr.click(a.ref)
    x, y = (b[0] + b[2] // 2, b[1] + b[3] // 2) if b else (None, None)
    _out({"ok": ok, "ref": a.ref, "x": x, "y": y}, a.json,
         (f"imleç ({x},{y}) → öğe tıklandı" if b else "öğe tıklandı") if ok else "tıklanamadı")
    if not ok:
        raise SystemExit(1)


def cmd_skill(nr, a) -> None:
    """LLM'e verilecek yetenek talimatı (adapters/skill/nexremote.md)."""
    path = os.path.join(_HERE, "..", "skill", "nexremote.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.startswith("---"):            # YAML ön bilgisini atla
        text = text.split("---", 2)[2].lstrip()
    print(text)


# ------------------------------------------------------------------- do
_NO_STEP = {"do", "service", "setup", "__serve__", "skill"}


def cmd_do(nr, a) -> None:
    """Adımları sırayla, tek süreçte (servis varsa sıcak) çalıştır; ilk hatada dur."""
    parser = build_parser()
    results, code = [], 0
    for i, step in enumerate(a.steps, 1):
        try:
            argv = shlex.split(step)
        except ValueError as e:
            results.append({"step": step, "code": 2, "output": f"ayrıştırılamadı: {e}"})
            code = 2
            break
        if argv[:1] == ["nexremote"]:
            argv = argv[1:]
        if not argv or argv[0] in _NO_STEP:
            results.append({"step": step, "code": 2,
                            "output": f"do içinde kullanılamaz: {argv[0] if argv else '(boş)'}"})
            code = 2
            break
        if a.json and "--json" not in argv:
            argv = ["--json"] + argv
        rc, out, err = _run_captured(nr, parser, _norm_argv(argv))
        res = {"step": step, "code": rc, "output": out.rstrip("\n")}
        if err.strip():
            res["error"] = err.strip()
        if a.json:
            try:
                res["output"] = json.loads(out) if out.strip() else None
            except ValueError:
                pass
        results.append(res)
        if rc != 0:
            code = rc
            if not a.keep_going:
                break
    if a.json:
        _out({"ok": code == 0, "steps": results,
              "skipped": len(a.steps) - len(results)}, True)
    else:
        for i, r in enumerate(results, 1):
            mark = "✓" if r["code"] == 0 else "✗"
            body = r["output"] if isinstance(r["output"], str) else json.dumps(r["output"],
                                                                               ensure_ascii=False)
            print(f"[{i}] {mark} {r['step']}")
            for line in (body or "").splitlines():
                print(f"    {line}")
            if r.get("error"):
                for line in r["error"].splitlines():
                    print(f"    ! {line}")
        if len(results) < len(a.steps):
            print(f"(hata nedeniyle {len(a.steps) - len(results)} adım çalıştırılmadı)")
    if code:
        raise SystemExit(code)


def _run_captured(nr, parser, argv: list[str]) -> tuple[int, str, str]:
    """Bir komutu bu süreçte çalıştır; (çıkış kodu, stdout, stderr)."""
    from contextlib import redirect_stderr, redirect_stdout
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            args = parser.parse_args(argv)
        except SystemExit as e:
            return (e.code if isinstance(e.code, int) else 2), out.getvalue(), err.getvalue()
        rc = _execute(nr, args)
    return rc, out.getvalue(), err.getvalue()


# ----------------------------------------------------------------- bench
def _stats(xs: list[float]) -> dict:
    xs = sorted(xs)
    pick = lambda q: xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]  # noqa: E731
    return {"n": len(xs), "min": round(xs[0], 1), "p50": round(pick(0.5), 1),
            "p95": round(pick(0.95), 1)}


def cmd_bench(nr, a) -> None:
    """Gecikme ölçümü. Varsayılan: salt-okuma işlemleri. --click/--at gerçek tık
    yapar (bilerek). --cli: her biri ayrı süreç olan CLI çağrıları (servis
    açık/kapalı) — ajanın gördüğü uçtan uca süre."""
    rows: list[tuple[str, dict]] = []

    def measure(label: str, fn, n: int = a.n) -> None:
        xs = []
        for _ in range(n):
            t = time.perf_counter()
            try:
                fn()
            except Exception as e:
                rows.append((label, {"error": str(e)}))
                return
            xs.append((time.perf_counter() - t) * 1000)
        rows.append((label, _stats(xs)))

    if a.cli:
        exe = [sys.executable, os.path.abspath(__file__)]
        import subprocess
        for label, env in (("CLI windows (servis)", {}),
                           ("CLI windows (servissiz)", {"NEXREMOTE_SERVICE": "0"})):
            e = {**os.environ, **env}
            subprocess.run(exe + ["windows"], env=e, capture_output=True)   # ısınma
            measure(label, lambda e=e: subprocess.run(exe + ["windows"], env=e,
                                                       capture_output=True, check=True))
    elif a.click:
        measure(f"click {a.click}", lambda: nr.click(a.click))
    elif a.at:
        measure(f"click_at {a.at[0]},{a.at[1]}", lambda: nr.click_at(a.at[0], a.at[1]))
    else:
        measure("pencereler", nr.windows)
        measure("imleç konumu", nr._user_pointer)
        if "gui" in nr.backends:
            from nexremote import snapshot as snap
            measure("tree (anlık görüntü)", lambda: nr.snapshot(a.window))
            s, _ = nr.snapshot(a.window)
            first = next(iter(s.entries), None)
            if first:
                ref = f"@{s.sid}:{first}"
                measure("ref çözümleme (hızlı yol)", lambda: nr._resolve_el(ref, wait=0))
                os.environ["NEXREMOTE_STRICT"] = "1"
                try:
                    measure("ref çözümleme (katı)", lambda: nr._resolve_el(ref, wait=0))
                finally:
                    del os.environ["NEXREMOTE_STRICT"]
            del snap
        measure("ekran görüntüsü", lambda: nr.screenshot(None, max_width=1568), n=min(a.n, 5))
    if a.json:
        _out([{"op": k, **v} for k, v in rows], True)
        return
    print(f"{'işlem':<30} {'n':>3} {'en az':>8} {'p50':>8} {'p95':>8}  (ms)")
    for k, v in rows:
        if "error" in v:
            print(f"{k:<30} hata: {v['error']}")
        else:
            print(f"{k:<30} {v['n']:>3} {v['min']:>8} {v['p50']:>8} {v['p95']:>8}")


# --------------------------------------------------------------- argümanlar
def _add_after(p: argparse.ArgumentParser) -> None:
    p.add_argument("--after", action="store_true",
                   help="eylemden sonra arayüzde neyin değiştiğini de yaz")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nexremote", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="makine-okunur çıktı")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="ilk kurulum (erişilebilirlik + izin)").set_defaults(fn=cmd_setup)
    sub.add_parser("caps", help="yetenekleri göster").set_defaults(fn=cmd_caps)
    o = sub.add_parser("observe", help="tam durum anlık görüntüsü")
    o.add_argument("--window", default=None)
    o.set_defaults(fn=cmd_observe)
    sub.add_parser("windows", help="pencereleri listele").set_defaults(fn=cmd_windows)
    wa = sub.add_parser("window", help="pencere eylemi")
    wa.add_argument("ref"); wa.add_argument("action", choices=["activate", "close", "minimize"])
    _add_after(wa)
    wa.set_defaults(fn=cmd_window)
    la = sub.add_parser("launch", help="programı erişilebilirlik açık başlat (Electron dahil)")
    la.add_argument("--wait", type=float, default=15.0, help="pencere bekleme süresi (sn)")
    la.add_argument("argv", nargs=argparse.REMAINDER, help="program ve argümanları")
    la.set_defaults(fn=cmd_launch)
    t = sub.add_parser("tree", help="erişilebilirlik ağacı (anlık görüntü, @s…:eN ref'li)")
    t.add_argument("--window", default=None)
    t.add_argument("-i", "--interactive", action="store_true",
                   help="yalnız eyleme açık öğeler ve ataları")
    t.add_argument("--root", default=None,
                   help="yalnız bu ref'in alt ağacı (@s…:eN; yoğun uygulamada bölgeye in)")
    t.set_defaults(fn=cmd_tree)
    f = sub.add_parser("find", help="öğe ara")
    f.add_argument("query")
    f.add_argument("--window", default=None)
    f.set_defaults(fn=cmd_find)
    c = sub.add_parser("click", help="öğeye (REF) veya koordinata (X Y) tıkla")
    c.add_argument("target", nargs="+")
    c.add_argument("--right", action="store_true")
    c.add_argument("--middle", action="store_true")
    c.add_argument("--double", action="store_true")
    c.add_argument("--keep-pointer", action="store_true",
                   help="kullanıcının imlecini eski yerine geri koyma")
    c.add_argument("--from-shot", action="store_true",
                   help="X Y son ekran görüntüsündeki piksel (ölçek/kırpma otomatik çevrilir)")
    _add_after(c)
    c.set_defaults(fn=cmd_click)
    ct = sub.add_parser("click-text", help="adıyla bul ve tıkla (tek okuma)")
    ct.add_argument("text")
    ct.add_argument("--window", default=None)
    ct.add_argument("--role", default=None, help="yalnızca bu rol (button, link, menu_item…)")
    ct.add_argument("--exact", action="store_true", help="yalnızca tam ad eşleşmesi")
    _add_after(ct)
    ct.set_defaults(fn=cmd_click_text)
    fl = sub.add_parser("fill", help="alanı doldur (+ Enter)")
    fl.add_argument("ref"); fl.add_argument("text")
    fl.add_argument("--enter", action="store_true")
    _add_after(fl)
    fl.set_defaults(fn=cmd_fill)
    mv = sub.add_parser("move", help="imleci taşı")
    mv.add_argument("x", type=int); mv.add_argument("y", type=int)
    mv.set_defaults(fn=cmd_move)
    scr = sub.add_parser("scroll", help="tekerlek (+aşağı, -yukarı)")
    scr.add_argument("steps", type=int)
    scr.add_argument("--horizontal", type=int, default=0)
    scr.add_argument("--at", type=int, nargs=2, metavar=("X", "Y"))
    _add_after(scr)
    scr.set_defaults(fn=cmd_scroll)
    dr = sub.add_parser("drag", help="sürükle-bırak")
    for n in ("x1", "y1", "x2", "y2"):
        dr.add_argument(n, type=int)
    _add_after(dr)
    dr.set_defaults(fn=cmd_drag)
    st = sub.add_parser("set-text", help="alana yaz")
    st.add_argument("ref"); st.add_argument("text")
    _add_after(st)
    st.set_defaults(fn=cmd_set_text)
    k = sub.add_parser("key", help="tuş kombinasyonu")
    k.add_argument("combo")
    _add_after(k)
    k.set_defaults(fn=cmd_key)
    ty = sub.add_parser("type", help="aktif pencereye yaz")
    ty.add_argument("text")
    _add_after(ty)
    ty.set_defaults(fn=cmd_type)
    sc = sub.add_parser("screenshot", help="ekran görüntüsü")
    sc.add_argument("path", nargs="?", default=None)
    sc.add_argument("--window", default=None, help="yalnızca bu pencere (win:{...})")
    sc.add_argument("--max-width", type=int, default=None,
                    help="genişliği en fazla N piksele küçült (varsayılan 1568; 0 = tam boyut)")
    sc.set_defaults(fn=cmd_screenshot)
    d = sub.add_parser("do", help="birden çok adımı tek çağrıda çalıştır")
    d.add_argument("steps", nargs="+", help="her biri bir nexremote komutu: 'click @s…:e4'")
    d.add_argument("--keep-going", action="store_true", help="hatada durma")
    _add_after(d)
    d.set_defaults(fn=cmd_do)
    w = sub.add_parser("wait", help="koşul gerçekleşene kadar bekle")
    g = w.add_mutually_exclusive_group(required=True)
    g.add_argument("--element", help="adı/değeri bu metni içeren öğe görünsün")
    g.add_argument("--window-title", help="başlığı bu metni içeren pencere açılsın")
    g.add_argument("--gone", help="bu ref'in öğesi kaybolsun")
    w.add_argument("--window", default=None, help="--element için pencere (varsayılan: etkin)")
    w.add_argument("--timeout", type=float, default=10.0)
    w.set_defaults(fn=cmd_wait)
    sl = sub.add_parser("sleep", help="bekle (do içinde)")
    sl.add_argument("seconds", type=float)
    sl.set_defaults(fn=cmd_sleep)
    r = sub.add_parser("run", help="komut çalıştır")
    r.add_argument("command")
    r.add_argument("--session", default="default")
    r.add_argument("--timeout", type=float, default=30.0)
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("screen", help="terminal ekranını oku")
    s.add_argument("--session", default="default")
    s.set_defaults(fn=cmd_screen)
    ks = sub.add_parser("keys", help="terminale tuş gönder")
    ks.add_argument("keys"); ks.add_argument("--session", default="default")
    ks.set_defaults(fn=cmd_keys)
    sub.add_parser("terms", help="masaüstündeki açık terminaller").set_defaults(fn=cmd_terms)
    tr = sub.add_parser("term-read", help="terminal ekranını oku")
    tr.add_argument("ref"); tr.add_argument("--history", type=int, default=0)
    tr.set_defaults(fn=cmd_term_read)
    ts = sub.add_parser("term-send", help="terminale yaz")
    ts.add_argument("ref"); ts.add_argument("text", nargs="?", default="")
    ts.add_argument("--enter", action="store_true", help="sonuna Enter ekle")
    ts.add_argument("--key", action="append", help="özel tuş: Enter, C-c, Up, Esc... (tekrarlanabilir)")
    ts.set_defaults(fn=cmd_term_send)
    sub.add_parser("sessions", help="oturumları listele").set_defaults(fn=cmd_sessions)
    sub.add_parser("cleanup", help="kendi oturumlarını kapat").set_defaults(fn=cmd_cleanup)
    cur = sub.add_parser("cursor", help="görünür ajan imlecini taşı (--click: yalnız halka)")
    cur.add_argument("x", type=int); cur.add_argument("y", type=int)
    cur.add_argument("--click", action="store_true")
    cur.add_argument("--button", type=int, default=1)
    cur.add_argument("--steps", type=int, default=24)
    cur.add_argument("--duration", type=float, default=0.4)
    cur.set_defaults(fn=cmd_cursor)
    sub.add_parser("cursor-hide", help="görünür ajan imlecini gizle").set_defaults(fn=cmd_cursor_hide)
    pt = sub.add_parser("point", help="öğeyi bul ve tıkla (ajan imleciyle)")
    pt.add_argument("ref")
    pt.add_argument("--button", type=int, default=1)
    pt.set_defaults(fn=cmd_point)
    b = sub.add_parser("bench", help="gecikme ölçümü (p50/p95)")
    b.add_argument("-n", type=int, default=10)
    b.add_argument("--window", default=None)
    b.add_argument("--click", default=None, help="bu ref'e N kez GERÇEKTEN tıkla")
    b.add_argument("--at", type=int, nargs=2, metavar=("X", "Y"),
                   help="bu koordinata N kez GERÇEKTEN tıkla")
    b.add_argument("--cli", action="store_true", help="ayrı süreçli CLI çağrılarını ölç")
    b.set_defaults(fn=cmd_bench)
    sv = sub.add_parser("service", help="arka plan servisi: status | stop | start")
    sv.add_argument("action", choices=["status", "stop", "start"])
    sv.set_defaults(fn=None)
    sub.add_parser("skill", help="LLM yetenek talimatı").set_defaults(fn=cmd_skill)
    return p


def _norm_argv(argv: list[str]) -> list[str]:
    """Ajanlar --json'u genelde sona yazar: öne al. `launch` ve `do` sonrası
    argümanlar programa/adımlara aittir — onlara dokunma."""
    stop = len(argv)
    for i, x in enumerate(argv):
        if x in ("launch",):
            stop = i
            break
    head = [x for x in argv[:stop] if x != "--json"]
    tail = argv[stop:]
    if "--json" in argv[:stop] or (stop == len(argv) and "--json" in argv):
        return ["--json"] + head + tail
    return head + tail


def _execute(nr, args) -> int:
    """Komutu çalıştır; hata biçimi ve çıkış kodu tek yerde."""
    try:
        if getattr(args, "after", False):
            _with_after(nr, args)
        else:
            args.fn(nr, args)
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except NotImplementedError:
        print("bu özellik bu makinede mevcut değil", file=sys.stderr)
        return 3
    except Exception as e:
        code = getattr(e, "code", None)
        details = getattr(e, "details", None) or {}
        if args.json:
            print(json.dumps({"error": str(e), **({"code": code} if code else {}), **details},
                             ensure_ascii=False))
        else:
            print(f"hata{f' [{code}]' if code else ''}: {e}", file=sys.stderr)
            for c in details.get("candidates", []) or []:
                print(f"  aday: {c}", file=sys.stderr)
        return 1


def _with_after(nr, args) -> None:
    """Komutu çalıştır, ardından eylem sonrası farkı ekle (JSON'da 'after' alanı)."""
    from contextlib import redirect_stdout
    try:
        before = nr.windows()
    except Exception:
        before = None
    buf = io.StringIO()
    err = None
    with redirect_stdout(buf):
        try:
            args.fn(nr, args)
        except SystemExit as e:
            err = e
    text = buf.getvalue()
    after = nr.after(before_windows=before) if err is None else None
    if args.json:
        try:
            obj = json.loads(text) if text.strip() else {}
        except ValueError:
            obj = {"output": text}
        if not isinstance(obj, dict):
            obj = {"result": obj}
        if after is not None:
            obj["after"] = after
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(text)
        if after is not None:
            print(after)
    if err is not None:
        raise err


def _run_local(args, long_lived: bool = False) -> int:
    from nexremote import trace
    try:
        with trace.span("çekirdek yükleme"):
            nr = _get_nr(long_lived)
    except Exception as e:
        print(f"nexremote çekirdeği yüklenemedi: {e}", file=sys.stderr)
        print(f"İpucu: PYTHONPATH={os.path.join(_HERE,'..','..','core')} ayarlayın",
              file=sys.stderr)
        return 2
    try:
        with trace.span(f"komut {args.cmd}"):
            return _execute(nr, args)
    finally:
        try:
            nr.close()
        except Exception:
            pass


# ================================================================ servis
# Her CLI çağrısı yeni bir Python süreciydi: içe aktarmalar, D-Bus/AT-SPI/X
# bağlantıları, kanal yoklaması her seferinde baştan. Servis bunları sıcak tutar;
# CLI ince istemci olur (çekirdeği hiç içe aktarmaz). İlk çağrı yerelde çalışır
# ve servisi arka planda başlatır — kimse servis için beklemez.
SERVICE_CMDS = {"caps", "observe", "windows", "window", "tree", "find", "click", "click-text",
                "fill", "move", "scroll", "drag", "set-text", "key", "type", "screenshot",
                "cursor", "cursor-hide", "point", "do", "wait", "sleep", "bench"}
_ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP",
             "XDG_SESSION_TYPE", "SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE", "NEXREMOTE_HOME")


def _service_wanted() -> bool:
    v = os.environ.get("NEXREMOTE_SERVICE", "")
    if v in ("0", "1"):
        return v == "1"
    # varsayılan: Linux. macOS (TCC izinleri) ve Windows (AF_UNIX) gerçek
    # donanımda doğrulanana kadar isteğe bağlı: NEXREMOTE_SERVICE=1
    return sys.platform.startswith("linux") and hasattr(socket, "AF_UNIX")


def _runtime() -> str:
    d = os.environ.get("XDG_RUNTIME_DIR")
    if not d:
        import getpass
        import tempfile
        try:
            user = getpass.getuser()
        except Exception:
            user = "user"
        d = os.path.join(tempfile.gettempdir(), f"nexremote-{user}")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _code_stamp() -> str:
    """Kod değişince servis de değişsin (eski kodla çalışan servis kullanılmaz)."""
    h = hashlib.sha1()
    roots = [os.path.abspath(__file__)]
    if _CORE:
        roots.append(os.path.join(_CORE, "nexremote"))
    for r in roots:
        if os.path.isfile(r):
            st = os.stat(r)
            h.update(f"{r}{st.st_mtime_ns}{st.st_size}".encode())
            continue
        for dp, _dn, fn in os.walk(r):
            for name in sorted(fn):
                if name.endswith(".py"):
                    st = os.stat(os.path.join(dp, name))
                    h.update(f"{name}{st.st_mtime_ns}{st.st_size}".encode())
    return h.hexdigest()


def _service_path() -> str:
    sig = hashlib.sha1("\0".join([_code_stamp(), sys.executable]
                                 + [os.environ.get(k, "") for k in _ENV_KEYS]).encode())
    return os.path.join(_runtime(), f"nexremote-svc-{sig.hexdigest()[:12]}.sock")


def _request(path: str, payload: dict, timeout: float) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect(path)                         # yoksa FileNotFoundError / ConnectionRefused
        s.settimeout(timeout)
        s.sendall(json.dumps(payload).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    if not buf:
        raise ConnectionError("servis yanıt vermeden kapandı")
    return json.loads(buf)


def _spawn_service(path: str) -> None:
    import subprocess
    env = dict(os.environ)
    if _CORE:
        env["PYTHONPATH"] = _CORE + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    log = os.path.join(_runtime(), "nexremote-svc.log")
    try:
        with open(log, "ab") as lf:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "__serve__", path],
                             stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, env=env,
                             start_new_session=True, close_fds=True)
    except OSError:
        pass


def _via_service(argv: list[str]) -> "int | None":
    """Servis üzerinden çalıştır; servis yoksa None (çağıran yerelde çalıştırır)."""
    path = _service_path()
    payload = {"argv": argv, "cwd": os.getcwd(),
               "env": {k: v for k, v in os.environ.items() if k.startswith("NEXREMOTE_")}}
    try:
        timeout = float(os.environ.get("NEXREMOTE_SERVICE_TIMEOUT", "600"))
        r = _request(path, payload, timeout)
    except (FileNotFoundError, ConnectionRefusedError):
        _spawn_service(path)                    # bir sonraki çağrı için; bu çağrı beklemez
        return None
    except (OSError, ValueError) as e:
        if isinstance(e, socket.timeout):
            print("hata: servis yanıt vermedi (NEXREMOTE_SERVICE_TIMEOUT) — "
                  "`nexremote service stop` ile yeniden başlatın", file=sys.stderr)
            return 1
        return None                             # bağlantı istek gönderilmeden koptu: yerelde dene
    sys.stdout.write(r.get("out", ""))
    sys.stdout.flush()
    if r.get("err"):
        sys.stderr.write(r["err"])
    return int(r.get("code", 1))


def _serve(path: str) -> int:
    """Servis süreci: tek iş parçacığı, istekler sırayla (eşzamanlı istemciler
    sıraya girer; AT-SPI ve D-Bus iş parçacığı güvenli değil)."""
    import fcntl
    from contextlib import redirect_stderr, redirect_stdout
    lock = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                                # aynı imza için başka servis var
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    old = os.umask(0o177)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(path)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    srv.listen(16)
    try:
        idle = float(os.environ.get("NEXREMOTE_SERVICE_IDLE", "600"))
    except ValueError:
        idle = 600.0
    srv.settimeout(idle if idle > 0 else None)
    from nexremote import trace
    nr = _get_nr(long_lived=True)
    parser = build_parser()
    started, served = time.time(), 0
    me = os.getuid()
    print(f"[nexremote-svc] hazır {path} (pid {os.getpid()})", file=sys.stderr, flush=True)
    try:
        while True:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                break                           # boşta: kapan (sonraki çağrı yeniden başlatır)
            with conn:
                try:
                    if hasattr(socket, "SO_PEERCRED"):
                        import struct
                        cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                               struct.calcsize("3i"))
                        if struct.unpack("3i", cred)[1] != me:
                            continue            # başka kullanıcı: yanıt yok
                    conn.settimeout(30)
                    buf = b""
                    while not buf.endswith(b"\n"):
                        chunk = conn.recv(1 << 16)
                        if not chunk:
                            break
                        buf += chunk
                    req = json.loads(buf)
                    conn.settimeout(None)
                except (OSError, ValueError):
                    continue
                if req.get("op") == "status":
                    reply = {"out": json.dumps({"pid": os.getpid(), "served": served,
                                                "uptime": round(time.time() - started),
                                                "path": path}) + "\n", "code": 0}
                elif req.get("op") == "stop":
                    try:
                        conn.sendall(json.dumps({"out": "servis durduruldu\n", "code": 0})
                                     .encode() + b"\n")
                    except OSError:
                        pass
                    break
                else:
                    reply = _serve_one(nr, parser, req, trace, redirect_stdout, redirect_stderr)
                    served += 1
                try:
                    conn.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
                except OSError:
                    pass
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
        srv.close()
        try:
            nr.close()
        except Exception:
            pass
    return 0


def _serve_one(nr, parser, req, trace, redirect_stdout, redirect_stderr) -> dict:
    env = req.get("env") or {}
    saved_env = {k: v for k, v in os.environ.items() if k.startswith("NEXREMOTE_")}
    cwd = os.getcwd()
    out, err = io.StringIO(), io.StringIO()
    code = 1
    try:
        for k in list(os.environ):
            if k.startswith("NEXREMOTE_") and k not in env:
                del os.environ[k]
        os.environ.update(env)
        try:
            os.chdir(req.get("cwd") or cwd)
        except OSError:
            pass
        on = env.get("NEXREMOTE_TRACE", "") not in ("", "0")
        with trace.sink(on) as lines, redirect_stdout(out), redirect_stderr(err):
            try:
                args = parser.parse_args(req.get("argv") or [])
                with trace.span(f"komut {args.cmd} (servis)"):
                    code = _execute(nr, args)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 2
        trace_text = "".join(f"[trace] {ln}\n" for ln in lines)
    finally:
        for k in list(os.environ):
            if k.startswith("NEXREMOTE_"):
                del os.environ[k]
        os.environ.update(saved_env)
        try:
            os.chdir(cwd)
        except OSError:
            pass
    return {"out": out.getvalue(), "err": err.getvalue() + trace_text, "code": code}


def cmd_service(a) -> int:
    path = _service_path()
    if a.action == "start":
        try:
            _request(path, {"op": "status"}, 5)
            print("servis zaten çalışıyor")
            return 0
        except (OSError, ValueError):
            _spawn_service(path)
            for _ in range(100):
                time.sleep(0.05)
                try:
                    _request(path, {"op": "status"}, 5)
                    print("servis başlatıldı")
                    return 0
                except (OSError, ValueError):
                    pass
            print("servis başlatılamadı (günlük: "
                  f"{os.path.join(_runtime(), 'nexremote-svc.log')})", file=sys.stderr)
            return 1
    try:
        r = _request(path, {"op": a.action}, 5)
    except (OSError, ValueError):
        print("servis çalışmıyor" + ("" if _service_wanted() else
                                     " (NEXREMOTE_SERVICE=1 ile açılır)"))
        return 0 if a.action == "stop" else 1
    sys.stdout.write(r.get("out", ""))
    return int(r.get("code", 0))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["__serve__"]:
        return _serve(argv[1] if len(argv) > 1 else _service_path())
    argv = _norm_argv(argv)
    args = build_parser().parse_args(argv)
    if args.cmd == "service":
        return cmd_service(args)
    if (_service_wanted() and args.cmd in SERVICE_CMDS
            and not (args.cmd == "bench" and (args.cli or args.click or args.at))):
        rc = _via_service(argv)
        if rc is not None:
            return rc
    return _run_local(args)


if __name__ == "__main__":
    raise SystemExit(main())
