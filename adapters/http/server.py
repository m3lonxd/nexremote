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
  POST /launch  {argv, wait}   programı ağacı açık başlat (Electron'a bayrak)

Güvenlik: varsayılan olarak yalnızca localhost'a bağlanır. Başka bir arayüze
token'sız (veya 24 karakterden kısa token'la) bağlanmayı REDDEDER — masaüstü
kontrolü ağa açılmış olurdu. Token verilince Authorization: Bearer <token>
zorunludur; sabit zamanlı karşılaştırılır. Token'ı komut satırına değil
NEXREMOTE_TOKEN'a ya da --token-file'a koyun (ps çıktısında görünmesin).
--tls-cert/--tls-key ile HTTPS; ağ üzerinden düz HTTP'de token okunabilir,
TLS yoksa SSH tüneli kullanın. Kurulum: deploy/install-service.sh.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import ssl
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
MAX_BODY = 4 * 1024 * 1024   # type/set_text için bol; daha büyüğü hata
MIN_REMOTE_TOKEN = 24


class BadRequest(Exception):
    """İstemci hatası (400) — gövde okunamadı veya çok büyük."""


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
    if method == "launch":
        argv = body.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError("launch: 'argv' boş olmayan metin listesi olmalı")
        return nr.launch(argv, float(body.get("wait", 15.0)))
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
    server_version = "NexRemote"
    timeout = 60   # sessiz bağlantı bir iş parçacığını sonsuza dek tutmasın

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
        return hmac.compare_digest(h.encode(), f"Bearer {TOKEN}".encode())

    def log_message(self, *a):  # sessiz
        pass

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest("Content-Length sayı değil")
        if n > MAX_BODY:
            raise BadRequest(f"gövde çok büyük ({n} bayt, sınır {MAX_BODY})")
        if n <= 0:
            return {}
        try:
            body = json.loads(self.rfile.read(n))
        except (ValueError, UnicodeDecodeError) as e:
            raise BadRequest(f"gövde geçerli JSON değil: {e}")
        if not isinstance(body, dict):
            raise BadRequest("gövde bir JSON nesnesi olmalı")
        return body

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
        except BadRequest as e:
            return self._send(400, {"error": str(e)})
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
    p.add_argument("--token", default=os.environ.get("NEXREMOTE_TOKEN"),
                   help="Bearer token (tercihen NEXREMOTE_TOKEN ya da --token-file)")
    p.add_argument("--token-file", default=os.environ.get("NEXREMOTE_TOKEN_FILE"),
                   help="token'ı bu dosyadan oku (ilk satır)")
    p.add_argument("--tls-cert", default=os.environ.get("NEXREMOTE_TLS_CERT"))
    p.add_argument("--tls-key", default=os.environ.get("NEXREMOTE_TLS_KEY"))
    p.add_argument("--gen-token", action="store_true", help="rastgele token yaz ve çık")
    a = p.parse_args(argv)
    if a.gen_token:
        print(secrets.token_urlsafe(32))
        return 0
    TOKEN = a.token
    if a.token_file:
        try:
            with open(a.token_file) as f:
                TOKEN = f.readline().strip()
        except OSError as e:
            print(f"HATA: token dosyası okunamadı: {e}", file=sys.stderr)
            return 2
    TOKEN = TOKEN or None
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        if not TOKEN or len(TOKEN) < MIN_REMOTE_TOKEN:
            print("HATA: localhost dışına açmak masaüstünü ağa açar; en az "
                  f"{MIN_REMOTE_TOKEN} karakterlik token gerekir "
                  "(üretmek için: nexremote-http --gen-token).", file=sys.stderr)
            return 2
        if not a.tls_cert:
            print("UYARI: TLS yok — token ağda düz metin gider. --tls-cert/--tls-key "
                  "verin ya da SSH tüneli kullanın.", file=sys.stderr)
    if bool(a.tls_cert) != bool(a.tls_key):
        print("HATA: --tls-cert ve --tls-key birlikte verilmeli.", file=sys.stderr)
        return 2
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        print(f"HATA: {a.host}:{a.port} dinlenemiyor: {e}", file=sys.stderr)
        return 2
    scheme = "http"
    if a.tls_cert:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(a.tls_cert, a.tls_key)
        except (OSError, ssl.SSLError) as e:
            print(f"HATA: TLS sertifikası yüklenemedi: {e}", file=sys.stderr)
            return 2
        # El sıkışma accept'te değil istek iş parçacığında olsun: yavaş/bozuk
        # bir istemci diğer bağlantıları bekletmesin.
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True,
                                     do_handshake_on_connect=False)
        scheme = "https"
    print(f"NexRemote HTTP: {scheme}://{a.host}:{a.port}  (token: {'var' if TOKEN else 'yok'})",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nkapanıyor...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
