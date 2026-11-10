#!/usr/bin/env python3
"""HTTP adaptörünün güvenlik ve protokol testi (salt okuma — girdi enjekte etmez).

    python3 tests/test_http.py

Sunucuyu boş bir portta alt süreç olarak açar: token zorunluluğu, sabit
zamanlı karşılaştırma yolu, bozuk/büyük gövde, TLS, token dosyası, uzak
bağlanma kuralları. Yalnızca caps/windows gibi okuma uçlarını çağırır.
"""
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SERVER = os.path.join(ROOT, "adapters", "http", "server.py")
ENV = {**os.environ, "NEXREMOTE_CURSOR": "0"}
ENV.pop("NEXREMOTE_TOKEN", None)
TOKEN = "t" * 12 + "-gizli-anahtar-0123456789"

fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(*args):
    port = free_port()
    p = subprocess.Popen([sys.executable, SERVER, "--port", str(port), *args],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
    line = p.stdout.readline()          # sunucu dinlemeye başlayınca yazar
    return p, port, line


def req(port, method, path, body=None, token=None, raw=None, headers=None, tls=False):
    if tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        c = http.client.HTTPSConnection("127.0.0.1", port, timeout=20, context=ctx)
    else:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    try:
        return r.status, json.loads(out)
    except ValueError:
        return r.status, out


def run_cli(*args):
    return subprocess.run([sys.executable, SERVER, *args], capture_output=True, text=True,
                          timeout=20, env=ENV)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="nexr-http-")
    p, port, line = start("--token", TOKEN)
    try:
        check("sunucu açıldı", "http://127.0.0.1" in line, line.strip())
        check("health token'sız açık", req(port, "GET", "/health") == (200, {"ok": True}))
        check("token'sız caps → 401", req(port, "GET", "/caps")[0] == 401)
        check("yanlış token → 401", req(port, "POST", "/windows", {}, token=TOKEN + "x")[0] == 401)
        check("kısa önek token → 401", req(port, "POST", "/windows", {}, token=TOKEN[:-1])[0] == 401)
        st, caps = req(port, "GET", "/caps", token=TOKEN)
        check("doğru token → caps", st == 200 and isinstance(caps, dict), str(st))
        st, ws = req(port, "POST", "/windows", {}, token=TOKEN)
        check("windows liste döndü", st == 200 and isinstance(ws, list), f"{st}, {len(ws)} pencere")
        st, e = req(port, "POST", "/windows", raw=b"{bozuk", token=TOKEN)
        check("bozuk JSON → 400", st == 400 and "JSON" in e.get("error", ""), str(e))
        st, e = req(port, "POST", "/windows", raw=b"[1,2]", token=TOKEN)
        check("JSON nesne değil → 400", st == 400, str(e))
        st, e = req(port, "POST", "/windows", raw=b"{}", token=TOKEN,
                    headers={"Content-Length": str(64 * 1024 * 1024)})
        check("64 MB gövde bildirimi → 400 (okunmadan)", st == 400 and "büyük" in e.get("error", ""))
        st, e = req(port, "POST", "/launch", {"argv": "vesktop"}, token=TOKEN)
        check("launch argv metin değil liste ister → 400", st == 400, str(e))
        st, e = req(port, "POST", "/yok", {}, token=TOKEN)
        check("bilinmeyen uç → hata kodu", st >= 400, str(st))
        # tarayıcı koruması: bir web sayfası localhost'a istek atamasın
        st, e = req(port, "POST", "/windows", {}, token=TOKEN,
                    headers={"Origin": "http://kotu.example"})
        check("Origin başlıklı (tarayıcı) istek → 403", st == 403, str(st))
        st, e = req(port, "GET", "/screenshot.png", token=TOKEN,
                    headers={"Sec-Fetch-Site": "cross-site"})
        check("çapraz site GET → 403", st == 403, str(st))
        st, e = req(port, "POST", "/click", {}, token=TOKEN)
        check("eksik alan → 400 (çekirdek KeyError'ıyla karışmaz)",
              st == 400 and "ref" in e.get("error", ""), str(e))
        st, e = req(port, "POST", "/batch", {"steps": "x"}, token=TOKEN)
        check("batch: steps liste değil → 400", st == 400, str(e))
        st, e = req(port, "POST", "/batch", {"steps": [{"method": "windows"},
                                                       {"method": "yok"},
                                                       {"method": "windows"}]}, token=TOKEN)
        check("batch: ilk hatada durur, kalan sayılır",
              st == 200 and e["ok"] is False and len(e["steps"]) == 2 and e["skipped"] == 1, str(e)[:200])
    finally:
        p.terminate()
        p.wait(5)

    # token'sız (yalnız localhost): DNS yeniden bağlamaya karşı Host denetimi
    p, port, line = start()
    try:
        st, _ = req(port, "POST", "/windows", {})
        check("token'sız localhost: yerel Host → 200", st == 200, str(st))
        st, _ = req(port, "POST", "/windows", {}, headers={"Host": f"kotu.example:{port}"})
        check("token'sız localhost: yabancı Host → 403", st == 403, str(st))
    finally:
        p.terminate()
        p.wait(5)

    r = run_cli("--host", "0.0.0.0", "--port", str(free_port()))
    check("ağa token'sız → reddedildi (2)", r.returncode == 2, r.stderr.strip()[:70])
    r = run_cli("--host", "0.0.0.0", "--port", str(free_port()), "--token", "kisa")
    check("ağa kısa token'la → reddedildi (2)", r.returncode == 2)
    r = run_cli("--gen-token")
    tok = r.stdout.strip()
    check("--gen-token ≥ 32 karakter", r.returncode == 0 and len(tok) >= 32, f"{len(tok)} karakter")
    r = run_cli("--port", str(free_port()), "--tls-cert", "/yok.pem")
    check("sertifika var anahtar yok → 2", r.returncode == 2)
    r = run_cli("--port", str(free_port()), "--token-file", "/yok/token")
    check("okunamayan token dosyası → 2", r.returncode == 2)
    empty = os.path.join(tmp, "bos")
    open(empty, "w").close()
    r = run_cli("--port", str(free_port()), "--token-file", empty)
    check("boş token dosyası → 2 (sessizce token'sız açılmaz)", r.returncode == 2)

    # token dosyası + TLS (kendinden imzalı sertifika)
    tf = os.path.join(tmp, "token")
    with open(tf, "w") as f:
        f.write(tok + "\n")
    cert, key = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
    o = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=127.0.0.1", "-keyout", key, "-out", cert],
                       capture_output=True, timeout=60)
    if o.returncode != 0:
        print("  ⊘ openssl yok — TLS testi atlandı")
    else:
        p, port, line = start("--token-file", tf, "--tls-cert", cert, "--tls-key", key)
        try:
            check("HTTPS açıldı", line.startswith("NexRemote HTTP: https://"), line.strip())
            # El sıkışmayan istemci accept'i kilitlememeli
            idle = socket.create_connection(("127.0.0.1", port))
            st, _ = req(port, "GET", "/caps", token=tok, tls=True)
            check("token dosyasıyla HTTPS caps (boşta bağlantı varken)", st == 200, str(st))
            idle.close()
            check("HTTPS yanlış token → 401",
                  req(port, "GET", "/caps", token=tok[:-1] + "x", tls=True)[0] == 401)
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("GET", "/health")
                c.getresponse()
                plain_ok = True
            except Exception:
                plain_ok = False
            check("TLS portunda düz HTTP çalışmıyor", not plain_ok)
        finally:
            p.terminate()
            p.wait(5)

    print(f"HTTP: {'TAMAM' if not fails else f'{fails} başarısız'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
