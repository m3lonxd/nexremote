#!/usr/bin/env python3
"""MCP sunucusunun stdio üzerinden gerçek JSON-RPC testi (salt okuma araçları).

    python3 tests/test_mcp_stdio.py
"""
import json
import os
import subprocess
import urllib.parse
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXPECTED = {"nexremote_observe", "nexremote_windows", "nexremote_window_action",
            "nexremote_click", "nexremote_click_at", "nexremote_scroll", "nexremote_drag",
            "nexremote_key", "nexremote_type", "nexremote_screenshot", "nexremote_terminals",
            "nexremote_terminal_read", "nexremote_terminal_send", "nexremote_run",
            "nexremote_launch", "nexremote_tree", "nexremote_cursor"}


def main() -> int:
    p = subprocess.Popen([sys.executable, f"{ROOT}/adapters/mcp/server.py"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True,
                         env={**os.environ, "NEXREMOTE_CURSOR": "0"})
    seq = iter(range(1, 100))

    def rpc(method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            msg["id"] = next(seq)
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()
        if notify:
            return None
        while True:
            line = p.stdout.readline()
            if not line:
                raise RuntimeError("sunucu kapandı")
            r = json.loads(line)
            if r.get("id") == msg["id"]:
                if "error" in r:
                    raise RuntimeError(r["error"])
                return r["result"]

    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        fails += not ok
        print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))

    try:
        init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "0"}})
        check("initialize", init["serverInfo"]["name"] == "nexremote")
        rpc("notifications/initialized", notify=True)
        tools = {t["name"] for t in rpc("tools/list")["tools"]}
        check(f"tools/list ({len(tools)} araç)", EXPECTED <= tools, str(EXPECTED - tools))
        obs = rpc("tools/call", {"name": "nexremote_observe", "arguments": {}})
        data = json.loads(obs["content"][0]["text"]) if obs["content"][0]["type"] == "text" else {}
        if "structuredContent" in obs:
            data = obs["structuredContent"].get("result", obs["structuredContent"])
        check("observe pencere+terminal döndü",
              bool(data.get("windows")) and "terminals" in data,
              f"{len(data.get('windows', []))} pencere, {len(data.get('terminals', []))} terminal")
        shot = rpc("tools/call", {"name": "nexremote_screenshot", "arguments": {"max_width": 640}})
        kinds = [c["type"] for c in shot["content"]]
        check("screenshot görüntü + açıklama döndü", kinds == ["image", "text"], str(kinds))
        res = {r["uri"] for r in rpc("resources/list")["resources"]}
        check("resources/list", {"nexremote://windows", "nexremote://terminals"} <= res, str(res))
        tpl = {t["uriTemplate"] for t in rpc("resources/templates/list")["resourceTemplates"]}
        check("resources/templates/list", "nexremote://tree/{ref}" in tpl, str(tpl))
        wins = json.loads(rpc("resources/read", {"uri": "nexremote://windows"})["contents"][0]["text"])
        check("windows kaynağı canlı liste", bool(wins) and all("ref" in w for w in wins),
              f"{len(wins)} pencere")
        terms = json.loads(rpc("resources/read", {"uri": "nexremote://terminals"})["contents"][0]["text"])
        check("terminals kaynağı liste", isinstance(terms, list), f"{len(terms)} terminal")
        if wins:
            enc = urllib.parse.quote(wins[0]["ref"], safe="")
            tree = rpc("resources/read", {"uri": f"nexremote://tree/{enc}"})["contents"][0]
            check("tree kaynağı (yüzde kodlu win ref)", tree["text"].startswith("["),
                  tree["text"].splitlines()[0][:60])
            foc = rpc("resources/read", {"uri": "nexremote://tree/focused"})["contents"][0]
            check("tree/focused etkin pencereyi verir", foc["text"].startswith("["))
        try:
            rpc("resources/read", {"uri": "nexremote://tree/win%3A%7Byok%7D"})
            check("olmayan pencere → hata", False)
        except RuntimeError as e:
            check("olmayan pencere → hata", True, str(e)[:70])
        bad = rpc("tools/call", {"name": "nexremote_terminal_read", "arguments": {"ref": "x"}})
        check("geçersiz ref → okunur araç hatası", bad.get("isError") is True,
              bad["content"][0]["text"][:80])
        nox = rpc("tools/call", {"name": "nexremote_cursor", "arguments": {}})
        check("nexremote_cursor koordinatsız → okunur hata", nox.get("isError") is True
              and "x and y" in nox["content"][0]["text"], nox["content"][0]["text"][:80])
        hid = rpc("tools/call", {"name": "nexremote_cursor", "arguments": {"hide": True}})
        check("nexremote_cursor hide=True çağrılabilir", not hid.get("isError"),
              hid["content"][0]["text"][:80])
    finally:
        p.terminate()
    print(f"MCP: {'TAMAM' if not fails else f'{fails} başarısız'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
