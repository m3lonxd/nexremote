#!/usr/bin/env python3
"""NexRemote HTTP adaptörü — YALNIZCA stdlib.

Kabuk komutu çalıştıramayan ama HTTP konuşabilen harness'lar için
(uzak ajanlar, web arayüzleri, diğer makinelerdeki ajanlar). Sıfır bağımlılık.

Çalıştır:
  python3 server.py --host 127.0.0.1 --port 8788

API (hepsi POST, JSON gövde; GET /health ve GET /caps de var):
  GET  /health                 -> {"ok": true}
  GET  /caps                   -> yetenekler
  POST /observe                -> tam durum
  POST /windows                -> pencereler
  POST /tree    {window_ref}   -> erişilebilirlik ağacı (metin)
  POST /find    {query, window_ref}
  POST /click   {ref}
  POST /set_text{ref, text}
  POST /key     {combo}
  POST /type    {text}
  POST /screenshot {path, window_ref, max_width}  -> meta (+ GET /screenshot.png)
  POST /window_action {ref, action}         activate | close | minimize
  POST /click_at {x, y, button, double, from_screenshot}
  POST /scroll  {steps, x, y}   POST /drag {x1, y1, x2, y2}
  POST /terminals               POST /terminal_read {ref, history}
  POST /terminal_send {ref, text, enter, keys}
  POST /run     {command, session, timeout}
  POST /screen  {session}
  POST /send_keys {keys, session}
  POST /sessions

Güvenlik: varsayılan olarak yalnızca localhost'a bağlanır. Başka bir arayüze
token'sız bağlanmayı REDDEDER (masaüstü kontrolü ağa açılmış olurdu).
--token verilince Authorization: Bearer <token> zorunludur.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (os.path.join(_HERE, "..", "..", "core"),
             os.path.join(_HERE, "..", "core")):
    cand = os.path.abspath(cand)
    if os.path.isdir(os.path.join(cand, "nexremote")) and cand not in sys.path:
        sys.path.insert(0, cand)

TOKEN = None
_NR = None


def get_nr():
    global _NR
    if _NR is None:
        from nexremote import NexRemote
        _NR = NexRemote()
    return _NR


def _dispatch(method: str, body: dict):
    nr = get_nr()
    if method == "caps":
        return nr.capabilities()
    if method == "observe":
        snap = {"capabilities": nr.capabilities(), "windows": [], "terminals": []}
        try:
            snap["windows"] = [w.to_dict() for w in nr.windows()]
        except Exception as e:
            snap["windows_error"] = str(e)
        try:
            snap["terminals"] = nr.terminals()
        except Exception as e:
            snap["terminals_error"] = str(e)
        if body.get("window_ref"):
            snap["tree"] = nr.tree(body["window_ref"])
        return snap
    if method == "windows":
        return [w.to_dict() for w in nr.windows()]
    if method == "tree":
        return {"tree": nr.tree(body.get("window_ref") or "")}
    if method == "find":
        return [{"ref": e.ref, "role": e.role.value, "name": e.name,
                 "value": e.value, "actions": e.actions}
                for e in nr.find(body.get("query", ""), body.get("window_ref"))]
    if method == "click":
        return {"ok": nr.click(body["ref"])}
    if method == "set_text":
        return {"ok": nr.set_text(body["ref"], body.get("text", ""))}
    if method == "key":
        return {"ok": nr.key(body.get("combo", ""))}
    if method == "type":
        return {"ok": nr.type_text(body.get("text", ""))}
    if method == "screenshot":
        return nr.screenshot(body.get("path"), window_ref=body.get("window_ref"),
                             max_width=int(body.get("max_width", 0))).to_dict()
    if method == "window_action":
        return {"ok": nr.window_action(body["ref"], body["action"])}
    if method == "click_at":
        x, y = nr.screen_point(float(body["x"]), float(body["y"]),
                               bool(body.get("from_screenshot", False)))
        nr.click_at(x, y, button=body.get("button", "left"),
                    count=2 if body.get("double") else 1)
        return {"ok": True, "clicked": [x, y]}
    if method == "scroll":
        at = None
        if "x" in body and "y" in body:
            at = nr.screen_point(float(body["x"]), float(body["y"]),
                                 bool(body.get("from_screenshot", False)))
        return {"ok": nr.scroll(int(body.get("steps", 1)), at=at)}
    if method == "drag":
        fs = bool(body.get("from_screenshot", False))
        a = nr.screen_point(float(body["x1"]), float(body["y1"]), fs)
        b = nr.screen_point(float(body["x2"]), float(body["y2"]), fs)
        return {"ok": nr.drag(a[0], a[1], b[0], b[1])}
    if method == "terminals":
        return nr.terminals()
    if method == "terminal_read":
        return {"screen": nr.terminal_read(body["ref"], int(body.get("history", 0)))}
    if method == "terminal_send":
        return {"ok": nr.terminal_send(body["ref"], body.get("text", ""),
                                       enter=bool(body.get("enter")), keys=body.get("keys"))}
    if method == "run":
        return nr.run(body.get("command", ""), body.get("session", "default"),
                      float(body.get("timeout", 30.0))).to_dict()
    if method == "screen":
        return {"screen": nr.read_screen(body.get("session", "default"))}
    if method == "send_keys":
        return {"ok": nr.send_keys(body.get("keys", ""), body.get("session", "default"))}
    if method == "sessions":
        return nr.sessions()
    raise LookupError(f"bilinmeyen yöntem: {method}")


class Handler(BaseHTTPRequestHandler):
    server_version = "NexRemote/0.1"

    def _send(self, code: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        if not TOKEN:
            return True
        h = self.headers.get("Authorization", "")
        return h == f"Bearer {TOKEN}"

    def log_message(self, *a):  # sessiz
        pass

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/health":
            return self._send(200, {"ok": True})
        if not self._authed():
            return self._send(401, {"error": "yetkisiz"})
        if path == "/caps":
            try:
                return self._send(200, _dispatch("caps", {}))
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if path == "/screenshot.png":
            shot = get_nr().last_screenshot()
            if shot is None or not os.path.exists(shot.path):
                return self._send(404, {"error": "önce POST /screenshot"})
            with open(shot.path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        return self._send(404, {"error": "bulunamadı"})

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"error": "yetkisiz"})
        method = urlparse(self.path).path.rstrip("/").lstrip("/")
        try:
            return self._send(200, _dispatch(method, self._body()))
        except KeyError as e:
            return self._send(400, {"error": f"eksik alan: {e.args[0]}"})
        except LookupError as e:
            return self._send(404, {"error": str(e)})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except NotImplementedError:
            return self._send(501, {"error": "bu makinede mevcut değil"})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def main(argv=None) -> int:
    global TOKEN
    p = argparse.ArgumentParser(description="NexRemote HTTP adaptörü")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8788)
    p.add_argument("--token", default=os.environ.get("NEXREMOTE_TOKEN"))
    a = p.parse_args(argv)
    TOKEN = a.token
    if a.host not in ("127.0.0.1", "localhost", "::1") and not TOKEN:
        print("HATA: localhost dışına token'sız açmak masaüstünü ağa açar. "
              "--token GİZLİ (veya NEXREMOTE_TOKEN) verin.", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"NexRemote HTTP: http://{a.host}:{a.port}  (token: {'var' if TOKEN else 'yok'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nkapanıyor...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
