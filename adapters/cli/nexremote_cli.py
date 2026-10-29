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
  nexremote tree [--window REF]        erişilebilirlik ağacı (LLM dostu metin, ref'li)
  nexremote find "Kaydet" [--window R] öğe arar → atspi ref
  nexremote click REF                  öğeye isimle tıklar (imleç oynamaz)
  nexremote click X Y [--right|--middle] [--double]   koordinata gerçek tık
  nexremote move X Y | scroll N [--at X Y] | drag X1 Y1 X2 Y2
  nexremote key "ctrl+s"               tuş kombinasyonu
  nexremote type "metin"               odaktaki alana yazar (Türkçe dahil)
  nexremote set-text REF "metin"       alanın içeriğini değiştirir
  nexremote screenshot [PATH]          ekran görüntüsü (koordinatlar = tık koordinatı)

  nexremote terms                      masaüstündeki açık terminaller
  nexremote term-read REF [--history N]
  nexremote term-send REF "metin" [--enter] [--key C-c ...]

  nexremote run "ls -la" [--session S] [--timeout T]   kendi tmux oturumumuz
  nexremote screen | keys "C-c" | sessions | cleanup
  nexremote skill                      LLM'e verilecek yetenek talimatı

Global: --json  (makine-okunur çıktı)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# çekirdeği bul
_HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (os.path.join(_HERE, "..", "..", "core"),
             os.path.join(_HERE, "..", "core")):
    cand = os.path.abspath(cand)
    if os.path.isdir(os.path.join(cand, "nexremote")) and cand not in sys.path:
        sys.path.insert(0, cand)


def _get_nr():
    from nexremote import NexRemote
    return NexRemote()


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
    if not ref:
        ws = nr.windows()
        if not ws:
            _out({"error": "pencere yok"}, a.json, "pencere yok")
            return
        foc = [w for w in ws if w.focused] or ws
        ref = foc[0].ref
    try:
        txt = nr.tree(ref)
    except Exception as e:
        _out({"error": str(e)}, a.json, f"hata: {e}")
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
    from nexremote import terminals
    txt = nr.terminal_read(a.ref, a.history)
    note = ""
    if a.history and not terminals.history_supported(a.ref):
        note = "Konsole yalnızca görünen ekranı verir; geçmiş okunamadı"
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


def cmd_screenshot(nr, a) -> None:
    s = nr.screenshot(a.path, window_ref=a.window, max_width=a.max_width)
    note = ""
    if s.scale != 1.0 or s.origin != (0, 0):
        note = (f"\ngörüntü ölçeği {s.scale:.4f}, orijin {s.origin}: görüntüdeki noktaya "
                f"tıklamak için `nexremote click X Y --from-shot`")
    _out(s.to_dict(), a.json, f"ekran görüntüsü: {s.path} ({s.width}x{s.height}){note}")


def cmd_run(nr, a) -> None:
    r = nr.run(a.command, a.session, a.timeout)
    _out(r.to_dict(), a.json, r.output)


def cmd_screen(nr, a) -> None:
    txt = nr.read_screen(a.session)
    if a.json:
        _out({"session": a.session, "screen": txt}, True)
    else:
        print(txt)


def cmd_keys(nr, a) -> None:
    ok = nr.send_keys(a.keys, a.session)
    _out({"ok": ok}, a.json, "gönderildi" if ok else "gönderilemedi")


def cmd_sessions(nr, a) -> None:
    ss = nr.sessions()
    _out(ss, a.json, "\n".join(ss) if ss else "(oturum yok)")


def cmd_cleanup(nr, a) -> None:
    killed = nr.cleanup()
    _out(killed, a.json,
         ("kapatıldı: " + ", ".join(killed)) if killed else "(kapatılacak oturum yok)")


def cmd_cursor(nr, a) -> None:
    """Görünür fare imlecini (x,y)'ye götür; --click ile tıkla."""
    ok, msg = nr.pointer_available()
    if not ok:
        _out({"ok": False, "error": msg}, a.json, f"imleç kullanılamaz: {msg}")
        return
    if a.click:
        nr.cursor(a.x, a.y, click=True, button=a.button)
        _out({"ok": True, "x": a.x, "y": a.y, "clicked": True}, a.json,
             f"imleç ({a.x},{a.y}) → tıklandı")
    else:
        nr.cursor_move(a.x, a.y, steps=a.steps, duration=a.duration)
        _out({"ok": True, "x": a.x, "y": a.y}, a.json,
             f"imleç ({a.x},{a.y}) → taşındı")


def cmd_cursor_hide(nr, a) -> None:
    ok = nr.cursor_hide()
    _out({"ok": bool(ok)}, a.json, "imleç gizlendi" if ok else "(görünür imleç yok)")


def cmd_point(nr, a) -> None:
    """Bir öğeyi (ref) bul, imleci üzerine GÖRÜNÜR şekilde götür ve tıkla."""
    b = nr.bounds(a.ref)
    if not b:
        _out({"ok": False, "error": "öğe konumu bulunamadı"}, a.json,
             "öğe konumu bulunamadı")
        return
    x, y = b[0] + b[2] // 2, b[1] + b[3] // 2
    # 1) ajan imleci GÖRÜNÜR şekilde öğeye gitsin
    nr.cursor(x, y, click=True, button=a.button)
    # 2) ayrıca AT-SPI ile gerçek tıklama (güvenilir eylem)
    nr.click(a.ref)
    _out({"ok": True, "ref": a.ref, "x": x, "y": y}, a.json,
         f"imleç ({x},{y}) → öğe tıklandı")


def cmd_skill(nr, a) -> None:
    """LLM'e verilecek yetenek talimatı (adapters/skill/nexremote.md)."""
    path = os.path.join(_HERE, "..", "skill", "nexremote.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.startswith("---"):            # YAML ön bilgisini atla
        text = text.split("---", 2)[2].lstrip()
    print(text)


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
    wa.set_defaults(fn=cmd_window)
    la = sub.add_parser("launch", help="programı erişilebilirlik açık başlat (Electron dahil)")
    la.add_argument("--wait", type=float, default=15.0, help="pencere bekleme süresi (sn)")
    la.add_argument("argv", nargs=argparse.REMAINDER, help="program ve argümanları")
    la.set_defaults(fn=cmd_launch)
    t = sub.add_parser("tree", help="erişilebilirlik ağacı")
    t.add_argument("--window", default=None)
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
    c.set_defaults(fn=cmd_click)
    mv = sub.add_parser("move", help="imleci taşı")
    mv.add_argument("x", type=int); mv.add_argument("y", type=int)
    mv.set_defaults(fn=cmd_move)
    scr = sub.add_parser("scroll", help="tekerlek (+aşağı, -yukarı)")
    scr.add_argument("steps", type=int)
    scr.add_argument("--horizontal", type=int, default=0)
    scr.add_argument("--at", type=int, nargs=2, metavar=("X", "Y"))
    scr.set_defaults(fn=cmd_scroll)
    dr = sub.add_parser("drag", help="sürükle-bırak")
    for n in ("x1", "y1", "x2", "y2"):
        dr.add_argument(n, type=int)
    dr.set_defaults(fn=cmd_drag)
    st = sub.add_parser("set-text", help="alana yaz")
    st.add_argument("ref"); st.add_argument("text")
    st.set_defaults(fn=cmd_set_text)
    k = sub.add_parser("key", help="tuş kombinasyonu")
    k.add_argument("combo")
    k.set_defaults(fn=cmd_key)
    ty = sub.add_parser("type", help="aktif pencereye yaz")
    ty.add_argument("text")
    ty.set_defaults(fn=cmd_type)
    sc = sub.add_parser("screenshot", help="ekran görüntüsü")
    sc.add_argument("path", nargs="?", default=None)
    sc.add_argument("--window", default=None, help="yalnızca bu pencere (win:{...})")
    sc.add_argument("--max-width", type=int, default=0, help="genişliği en fazla N piksele küçült")
    sc.set_defaults(fn=cmd_screenshot)
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
    cur = sub.add_parser("cursor", help="görünür fare imlecini taşı/tıkla")
    cur.add_argument("x", type=int); cur.add_argument("y", type=int)
    cur.add_argument("--click", action="store_true")
    cur.add_argument("--button", type=int, default=1)
    cur.add_argument("--steps", type=int, default=24)
    cur.add_argument("--duration", type=float, default=0.4)
    cur.set_defaults(fn=cmd_cursor)
    sub.add_parser("cursor-hide", help="görünür ajan imlecini gizle").set_defaults(fn=cmd_cursor_hide)
    pt = sub.add_parser("point", help="öğeyi bul, imleci üzerine götür, tıkla")
    pt.add_argument("ref")
    pt.add_argument("--button", type=int, default=1)
    pt.set_defaults(fn=cmd_point)
    sub.add_parser("skill", help="LLM yetenek talimatı").set_defaults(fn=cmd_skill)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--json" in argv:          # ajanlar bayrağı genelde sona yazar
        argv.remove("--json")
        argv.insert(0, "--json")
    args = build_parser().parse_args(argv)
    try:
        nr = _get_nr()
    except Exception as e:
        print(f"nexremote çekirdeği yüklenemedi: {e}", file=sys.stderr)
        print(f"İpucu: PYTHONPATH={os.path.join(_HERE,'..','..','core')} ayarlayın",
              file=sys.stderr)
        return 2
    try:
        args.fn(nr, args)
        return 0
    except NotImplementedError:
        print("bu özellik bu makinede mevcut değil", file=sys.stderr)
        return 3
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"hata: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            nr.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
