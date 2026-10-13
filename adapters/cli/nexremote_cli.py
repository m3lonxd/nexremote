#!/usr/bin/env python3
"""NexRemote CLI adaptörü — EVRENSEL TABAN.

Her harness (Claude Code, Cursor, Aider, düz kabuk, proxy'lenmiş CLI'lar)
kabuk komutu çalıştırabildiği sürece NexRemote'u kullanabilir. MCP gerekmez,
tool-calling gerekmez, özel SDK gerekmez. Sadece `nexremote ...` komutları.

Kullanım:
  nexremote caps                       ne yapabildiğimizi anlatır
  nexremote observe                    tam durum anlık görüntüsü (modele besle)
  nexremote windows                    pencereleri listeler
  nexremote tree [--window REF]        erişilebilirlik ağacı (LLM dostu metin)
  nexremote find "Kaydet" [--window R] öğe arar
  nexremote click REF                  öğeye tıklar
  nexremote set-text REF "metin"       alana yazar
  nexremote key "ctrl+s"               tuş kombinasyonu
  nexremote type "metin"               aktif pencereye yazar
  nexremote screenshot [PATH]          ekran görüntüsü
  nexremote run "ls -la" [--session S] [--timeout T]
  nexremote screen [--session S]       terminal oturumunun ekranını okur
  nexremote keys "C-c" [--session S]   terminale tuş gönderir
  nexremote sessions                   terminal oturumlarını listeler
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
    print("NexRemote yetenekleri:")
    for name, c in caps["backends"].items():
        mark = "✓" if c["available"] else "✗"
        print(f"  {mark} {name}: {c['detail']}")
        if c.get("needs"):
            print(f"      eksik: {', '.join(c['needs'])}")
    print(f"  eylemler: {', '.join(caps['actions'])}")


def cmd_observe(nr, a) -> None:
    """Tam durum anlık görüntüsü — bir modele tek seferde beslenecek özet."""
    snap = {"capabilities": nr.capabilities(), "windows": [], "terminal": {}}
    try:
        for w in nr.windows():
            snap["windows"].append(w.to_dict())
    except Exception as e:
        snap["windows_error"] = str(e)
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
    print("=== NexRemote Gözlem Anlık Görüntüsü ===")
    for name, c in snap["capabilities"]["backends"].items():
        print(f"[{name}] {'aktif' if c['available'] else 'kapalı'} — {c['detail']}")
    print(f"\nAçık pencereler ({len(snap['windows'])}):")
    for w in snap["windows"]:
        f = " *" if w.get("focused") else ""
        print(f"  {w.get('app','?')} | {w.get('title','')} | ref={w['ref']}{f}")
    if snap.get("terminal", {}).get("sessions"):
        print(f"\nTerminal oturumları: {', '.join(snap['terminal']['sessions'])}")
    if snap.get("tree"):
        print("\n--- Pencere ağacı ---")
        print(snap["tree"][:4000])


def cmd_windows(nr, a) -> None:
    ws = nr.windows()
    if a.json:
        _out([w.to_dict() for w in ws], True)
        return
    for w in ws:
        f = " *" if w.focused else ""
        print(f"{w.ref}\t{w.app}\t{w.title}{f}")


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
        return
    if a.json:
        print(json.dumps({"window": ref, "tree": txt}, ensure_ascii=False, indent=2))
    else:
        print(txt)


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
    ok = nr.click(a.ref)
    _out({"ok": ok, "ref": a.ref}, a.json, "tıklandı" if ok else "tıklanamadı")


def cmd_set_text(nr, a) -> None:
    ok = nr.set_text(a.ref, a.text)
    _out({"ok": ok, "ref": a.ref}, a.json, "yazıldı" if ok else "yazılamadı")


def cmd_key(nr, a) -> None:
    ok = nr.key(a.combo)
    _out({"ok": ok, "combo": a.combo}, a.json, "gönderildi" if ok else "gönderilemedi")


def cmd_type(nr, a) -> None:
    ok = nr.type_text(a.text)
    _out({"ok": ok}, a.json, "yazıldı" if ok else "yazılamadı")


def cmd_screenshot(nr, a) -> None:
    path = a.path or "/tmp/nexremote-shot.png"
    s = nr.screenshot(path)
    _out(s.to_dict(), a.json,
         f"ekran görüntüsü: {s.path} ({s.width}x{s.height}) via {s.backend}")


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


def cmd_skill(nr, a) -> None:
    """LLM'e verilecek yetenek talimatı — tool-calling olmayan modeller için."""
    print(_SKILL_TEXT)


_SKILL_TEXT = """\
# NexRemote — masaüstü ve terminal kontrolü

Kabuk komutu çalıştırarak bu makinenin grafik masaüstünü ve terminal
oturumlarını görebilir ve kontrol edebilirsin. Tüm komutlar `nexremote`
ile başlar; makine-okunur çıktı için `--json` ekle.

## Gözlem
  nexremote caps                     # ne yapabildiğini öğren (önce bunu çalıştır)
  nexremote observe                  # tam durum: pencereler + ağaç + oturumlar
  nexremote windows                  # açık pencereler (ref, uygulama, başlık)
  nexremote tree --window REF        # pencerenin erişilebilirlik ağacı (metin)
  nexremote find "Kaydet"            # öğe adıyla ara → ref döner
  nexremote screenshot /tmp/s.png    # piksel görüntüsü (görsel model gerekir)

## GUI eylemleri
  nexremote click REF                # öğeye tıkla (ref: tree/find çıktısından)
  nexremote set-text REF "metin"     # metin alanına yaz
  nexremote key "ctrl+s"             # tuş kombinasyonu
  nexremote type "metin"             # aktif pencereye yaz

## Terminal (TUI) eylemleri
  nexremote run "ls -la"             # komut çalıştır, çıktıyı al
  nexremote screen                   # aktif terminal oturumunun ekranını oku
  nexremote screen --session ad      # belirli oturumu oku
  nexremote keys "C-c" --session ad  # terminale tuş gönder
  nexremote sessions                 # oturumları listele

## Tipik döngü
1. `nexremote caps`      → ne yapabildiğini öğren
2. `nexremote observe`   → mevcut durumu gör
3. `nexremote find X`    → hedef öğenin ref'ini al
4. `nexremote click REF` → eylemi yap
5. `nexremote observe`   → sonucu doğrula

## Notlar
- Electron uygulamaları (Discord, VSCode) iç butonlarını göstermek için
  `--force-renderer-accessibility` bayrağı ister.
- Öğe yoksa koordinata tıklama yerine önce adıyla aramayı dene.
- GUI yoksa (başsız sunucu) yalnızca terminal komutları (run/screen/keys) çalışır.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nexremote", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="makine-okunur çıktı")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("caps", help="yetenekleri göster").set_defaults(fn=cmd_caps)
    o = sub.add_parser("observe", help="tam durum anlık görüntüsü")
    o.add_argument("--window", default=None)
    o.set_defaults(fn=cmd_observe)
    sub.add_parser("windows", help="pencereleri listele").set_defaults(fn=cmd_windows)
    t = sub.add_parser("tree", help="erişilebilirlik ağacı")
    t.add_argument("--window", default=None)
    t.set_defaults(fn=cmd_tree)
    f = sub.add_parser("find", help="öğe ara")
    f.add_argument("query")
    f.add_argument("--window", default=None)
    f.set_defaults(fn=cmd_find)
    c = sub.add_parser("click", help="öğeye tıkla")
    c.add_argument("ref")
    c.set_defaults(fn=cmd_click)
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
    sub.add_parser("sessions", help="oturumları listele").set_defaults(fn=cmd_sessions)
    sub.add_parser("skill", help="LLM yetenek talimatı").set_defaults(fn=cmd_skill)
    return p


def main(argv=None) -> int:
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
