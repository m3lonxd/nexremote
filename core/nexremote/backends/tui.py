"""TUI / terminal backend — AI ajanının uzaktan terminal yönetimi.

Üç strateji (otomatik seçilir):

  A) tmux varsa  — kalıcı oturumlar + `capture-pane` ile EKRAN OKUMA.
     TUI ajanları (htop, vim, claude-code, menülü CLI'lar) için kritik.
  B) tmux yoksa  — saf PTY (`pty.openpty` + `subprocess`) — Linux, macOS, BSD.
  C) Windows     — boru (pipe) ile kabuk: `run` tek seferlik, `send_keys` /
     `read_screen` süreç içinde kalıcı bir cmd.exe oturumu. Tam ekran TUI
     programları için masaüstündeki konsolu `terminals` ile kullanın.

AI ajanı için bu backend'in özü: "komut çalıştır, ekranı metin olarak OKU,
tuş gönder". Ekran okuma olmadan bir TUI ajanı yönetilemez.

GÜVENLİK: Bu backend yalnızca kendi açtığı ``nexremote_`` önekli tmux
oturumlarını yönetir. Kullanıcının kendi tmux oturumlarına ASLA dokunmaz.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional

_WINDOWS = sys.platform == "win32"
if not _WINDOWS:
    import pty
    import select

from ..base import Backend
from ..models import Capability, CommandResult

# Tüm NexRemote oturumları bu öneki taşır — sahiplik/ayrım anahtarı.
SESSION_PREFIX = "nexremote_"

# tmux'ta doğrudan tuş adı olarak tanınan isimler (Enter, C-c, ...).
# Büyük/küçük harf duyarlı: "end", "up", "tab" gibi sıradan kelimeler metin olarak
# yazılmalı, tuş olarak değil. Yalnızca tmux'un kendi yazımı tuş sayılır.
_TMUX_KEY_RE = re.compile(r"^(?:[CMS]-(?:[A-Za-z0-9]|Space|Enter|Tab|Up|Down|Left|Right|"
                          r"Home|End|BSpace)|Enter|Escape|Space|Tab|BSpace|"
                          r"BTab|DC|IC|Up|Down|Left|Right|Home|End|PgUp|PgDn|PPage|NPage|"
                          r"F[0-9]{1,2}|KP[0-9]|C-\\\\|C-\\)$")
_CTRL_RE = re.compile(r"^(?:C-|ctrl\+)([A-Za-z])$", re.IGNORECASE)


def _key_name(k: str) -> Optional[str]:
    """Tuş adıysa tmux yazımı ('Enter', 'C-c', 'Up'…), değilse None (düz metin).
    ctrl+x / C-x büyük-küçük harf fark etmez; 'Esc' de kabul edilir."""
    m = _CTRL_RE.match(k)
    if m:
        return f"C-{m.group(1).lower()}"
    if k == "Esc":
        return "Escape"
    return k if _TMUX_KEY_RE.match(k) else None


# ANSI CSI / OSC kaçış dizileri (PTY çıktısını LLM dostu metne indirgemek için).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-_]")

# Sentinel (tamamlanma belirteci) biçimi: komut bitince ekrana basılır.
# Her çalıştırmada benzersiz belirteç: geçmişte kalan eskileriyle karışmaz,
# ekrandan/geçmişten taşan uzun çıktıda da bitiş yakalanır.
_START_TOKEN = "__NEXR_START__"          # eski sürüm uyumu (testler)
_EXIT_RE = re.compile(r"^__NEXR_(\d+)__$")


def _pane(name: str) -> str:
    """Tam ad eşleşmeli bölme hedefi: `-t build` build2'ye de gider, `=build:` gitmez."""
    return f"={name}:"


def _sess(name: str) -> str:
    return f"={name}"


def _normalize_session(session: str) -> str:
    """Oturum adını tmux-güvenli hale getir: boşluk/özel karakter -> ``_``.

    tmux oturum adları ``.`` ve ``:`` içeremez; ayrıca kabuk/tmux
    argümanlarında güvenlik için yalnızca [A-Za-z0-9_-] bırakırız.
    """
    s = (session or "").strip()
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")
    return s or "default"


def _strip_ansi(text: str) -> str:
    """Kaçış dizilerini ve kontrol karakterlerini metne indirge (PTY için)."""
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Yazdırılamaz artık kontrol karakterlerini (tab ve \n hariç) temizle.
    return "".join(c if (c == "\n" or c == "\t" or ord(c) >= 32) else ""
                   for c in text)


_PTY_KEYS = {
    "Enter": b"\r", "Space": b" ", "Tab": b"\t", "BTab": b"\x1b[Z", "Escape": b"\x1b",
    "BSpace": b"\x7f", "Up": b"\x1b[A", "Down": b"\x1b[B", "Right": b"\x1b[C",
    "Left": b"\x1b[D", "Home": b"\x1b[H", "End": b"\x1b[F", "PgUp": b"\x1b[5~",
    "PPage": b"\x1b[5~", "PgDn": b"\x1b[6~", "NPage": b"\x1b[6~", "DC": b"\x1b[3~",
    "IC": b"\x1b[2~",
}


def _keys_to_bytes(keys: str) -> bytes:
    """Tuş adını PTY'ye yazılacak ham baytlara çevir; tmux kipiyle aynı adlar
    tuş sayılır (Enter, C-c, BSpace, Up…), gerisi düz metindir."""
    name = _key_name((keys or "").strip())
    if name and name.startswith("C-") and len(name) == 3:
        return bytes([ord(name[2].upper()) - 64])
    if name in _PTY_KEYS:
        return _PTY_KEYS[name]
    return (keys or "").encode("utf-8", "replace")


class _PtySession:
    """PTY modunda kalıcı (etkileşimli) tek bir kabuk oturumu.

    send_keys ham bayt yazar, read_screen biriken buffer'ı boşaltır.
    """

    def __init__(self, shell: str = "/bin/sh") -> None:
        self.shell = shell
        self.master, slave = pty.openpty()
        os.set_blocking(self.master, False)
        self.proc = subprocess.Popen(
            [shell],
            stdin=slave, stdout=slave, stderr=slave,
            preexec_fn=os.setsid, close_fds=True,
            env={**os.environ, "TERM": os.environ.get("TERM", "xterm-256color")},
        )
        os.close(slave)
        self.buffer = ""

    def write(self, data: bytes) -> None:
        try:
            os.write(self.master, data)
        except OSError:
            pass

    def drain(self, wait: float = 0.0) -> None:
        """Master'dan okunabilir ne varsa buffer'a ekle (isteğe bağlı bekle)."""
        deadline = time.monotonic() + wait
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0 and self.buffer:
                break
            r, _, _ = select.select([self.master], [], [], max(remain, 0))
            if not r:
                break
            try:
                chunk = os.read(self.master, 65536)
            except OSError:  # EIO — çocuk kapandı
                break
            if not chunk:
                break
            self.buffer += chunk.decode("utf-8", "replace")
            if wait <= 0:
                continue  # non-blocking: eldeki her şeyi al

    def take(self) -> str:
        out, self.buffer = self.buffer, ""
        return out

    def close(self) -> None:
        try:
            os.killpg(os.getpgid(self.proc.pid), 15)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            os.close(self.master)
        except OSError:
            pass


def _console_encoding() -> str:
    """Windows'ta kabuk çıktısının kod sayfası (cmd.exe OEM kod sayfası yazar)."""
    if not _WINDOWS:
        return "utf-8"
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetOEMCP()
        return f"cp{cp}" if cp != 65001 else "utf-8"
    except (OSError, AttributeError):
        return "utf-8"


class _PipeSession:
    """Windows: borularla bağlı kalıcı cmd.exe. Çıktı arka iş parçacığında birikir.

    Gerçek konsol değildir (imleç/renk yok); satır tabanlı programlar için uygundur.
    """

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [os.environ.get("COMSPEC", "cmd.exe"), "/Q", "/K"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.encoding = _console_encoding()
        self.buffer = ""
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        while True:
            chunk = self.proc.stdout.read1(65536)
            if not chunk:
                return
            with self._lock:
                self.buffer += chunk.decode(self.encoding, "replace")

    def write(self, data: bytes) -> None:
        if self.proc.poll() is not None or self.proc.stdin is None:
            raise RuntimeError("oturumun kabuğu kapanmış")
        # boruda satır düzenleme yok: Enter (\r) kabuğa satır sonu olarak gitmeli
        text = data.decode("utf-8", "replace").replace("\r", "\r\n")
        self.proc.stdin.write(text.encode(self.encoding, "replace"))
        self.proc.stdin.flush()

    def drain(self, wait: float = 0.0) -> None:
        if wait > 0:
            time.sleep(wait)

    def take(self) -> str:
        with self._lock:
            out, self.buffer = self.buffer, ""
        return out

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class TuiBackend(Backend):
    """Terminal backend'i — tmux varsa onu, yoksa saf PTY kullanır."""

    name = "tui"
    kind = "tui"

    def __init__(self) -> None:
        self.tmux = shutil.which("tmux")
        self._pty_sessions: dict[str, _PtySession] = {}
        # Kendi açtığı tmux oturumlarının kaydı (sahiplik takibi).
        self._owned: set[str] = set()
        # Ekran genişliği: TUI çıktısı sarmasın diye geniş.
        self._width = 220
        self._height = 50

    # ------------------------------------------------------------ capability
    def capability(self) -> Capability:
        """tmux var mı? PTY her zaman var. Hangi mod aktif?"""
        has_tmux = self.tmux is not None
        if _WINDOWS:
            return Capability(name=self.name, available=True, detail=(
                "mod=pipe (Windows); run=tek seferlik; send_keys/screen=süreç içi cmd.exe "
                "oturumu; tam ekran TUI için masaüstü konsolu (terminals)"))
        needs: list[str] = [] if has_tmux else ["tmux (kalıcı oturumlar için)"]
        mode = "tmux" if has_tmux else "pty"
        detail = (
            f"mod={mode}; tmux={'var' if has_tmux else 'yok'}; "
            f"pty=var; kalıcı oturum={'evet' if has_tmux else 'hayır (her komut tek seferlik)'}"
        )
        # PTY stdlib'de her zaman var -> backend her durumda kullanılabilir.
        return Capability(name=self.name, available=True, detail=detail, needs=needs)

    @property
    def active_mode(self) -> str:
        return "tmux" if self.tmux else ("pipe" if _WINDOWS else "pty")

    # ------------------------------------------------------------ yardımcı
    def _session_name(self, session: str) -> str:
        return SESSION_PREFIX + _normalize_session(session)

    def _tmux(self, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
        """tmux alt komutu çalıştır (kabuk yok, argv olarak güvenli)."""
        assert self.tmux
        return subprocess.run(
            [self.tmux, *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "TERM": "xterm-256color"},
        )

    def _ensure_session(self, name: str) -> bool:
        """Oturum yoksa oluştur; varsa (ve bizimse) dokunma."""
        try:
            if self._tmux("has-session", "-t", _sess(name)).returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            return False
        try:
            r = self._tmux("new-session", "-d", "-s", name,
                           "-x", str(self._width), "-y", str(self._height))
            if r.returncode == 0:
                self._owned.add(name)
            return r.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _capture(self, name: str) -> str:
        """Tüm geçmişi + görünür ekranı düz metin olarak al."""
        try:
            r = self._tmux("capture-pane", "-p", "-J", "-S", "-", "-t", _pane(name))
            if r.returncode == 0:
                return r.stdout
        except (subprocess.TimeoutExpired, OSError):
            pass
        return ""

    def _session_exists(self, name: str) -> bool:
        try:
            return self._tmux("has-session", "-t", _sess(name)).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    # ------------------------------------------------------------ run (tmux)
    def _run_tmux(self, command: str, session: str,
                  timeout: float) -> CommandResult:
        name = self._session_name(session)
        if not self._ensure_session(name):
            return CommandResult(output=f"tmux oturumu açılamadı: {name}",
                                 exit_code=-1, session=session, timed_out=False)

        # Benzersiz başlangıç/bitiş belirteçleri. Komut `{ …\n}` içine alınır:
        # sonu `&` ya da `# yorum` ile biten komut da sözdizimini bozmaz.
        tok = secrets.token_hex(4)
        start_tok = f"__NEXR_S_{tok}__"
        end_re = re.compile(rf"^__NEXR_E_{tok}_(\d+)__$")
        payload = f"echo {start_tok}; {{ {command}\n}}; echo __NEXR_E_{tok}_$?__"
        try:
            # -l (literal): özel karakterler tuş adı olarak yorumlanmasın; -- ile
            # '-' ile başlayan metin bayrak sanılmasın.
            self._tmux("send-keys", "-t", _pane(name), "-l", "--", payload)
            self._tmux("send-keys", "-t", _pane(name), "Enter")
        except (subprocess.TimeoutExpired, OSError) as e:
            return CommandResult(output=f"send-keys hatası: {e}",
                                 exit_code=-1, session=session)

        deadline = time.monotonic() + max(timeout, 0.1)
        delay = 0.03
        while True:
            screen = self._capture(name)
            done = self._parse_tokens(screen.splitlines(), start_tok, end_re)
            if done is not None:
                out, code = done
                return CommandResult(output=out, exit_code=code,
                                     session=session, timed_out=False)
            if not self._session_exists(name):
                return CommandResult(output=screen, exit_code=-1, session=session)
            if time.monotonic() >= deadline:
                break
            time.sleep(delay)
            delay = min(delay * 1.5, 0.15)     # kısa komut hızlı döner, uzun komut az yoklanır

        return CommandResult(output=self._capture(name), exit_code=-1,
                             session=session, timed_out=True)

    @staticmethod
    def _parse_tokens(lines: list[str], start_tok: str,
                      end_re: "re.Pattern[str]") -> Optional[tuple[str, int]]:
        """Bitiş belirteci görüldüyse (çıktı, çıkış kodu); yoksa None. Başlangıç
        satırı geçmişten taşmışsa çıktı ekranın başından alınır."""
        end_i = code = None
        for i in range(len(lines) - 1, -1, -1):
            m = end_re.match(lines[i].strip())
            if m:
                end_i, code = i, int(m.group(1))
                break
        if end_i is None:
            return None
        start_i = -1
        for i in range(end_i - 1, -1, -1):
            if lines[i].strip() == start_tok:
                start_i = i
                break
        return "\n".join(lines[start_i + 1:end_i]).rstrip(), code

    @staticmethod
    def _after_last_start(screen: str) -> str:
        """Son ``__NEXR_START__`` satırından SONRAKİ kısmı döndür."""
        lines = screen.splitlines()
        last = -1
        for i, ln in enumerate(lines):
            if ln.strip() == _START_TOKEN:
                last = i
        return "\n".join(lines[last + 1:]) if last != -1 else screen

    @staticmethod
    def _parse_exit(lines: list[str]) -> Optional[int]:
        """Son ``__NEXR_<kod>__`` satırından çıkış kodunu ayrıştır."""
        for ln in reversed(lines):
            m = _EXIT_RE.match(ln.strip())
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _extract_output(lines: list[str]) -> str:
        """START ile EXIT belirteçleri ARASINDAKİ satırları döndür."""
        start = exit_i = -1
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s == _START_TOKEN:
                start = i
            elif _EXIT_RE.match(s):
                exit_i = i
        if exit_i == -1:
            return "\n".join(lines).strip()
        lo = start + 1 if start != -1 else 0
        out = lines[lo:exit_i]
        return "\n".join(out).rstrip()

    # ------------------------------------------------------------ run (PTY)
    def _run_pty(self, command: str, session: str,
                 timeout: float) -> CommandResult:
        """Tek seferlik: PTY içinde komutu çalıştır, çıktıyı topla."""
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                command, shell=True, stdin=slave, stdout=slave, stderr=slave,
                preexec_fn=os.setsid, close_fds=True,
                env={**os.environ, "TERM": "xterm-256color"},
            )
        except Exception as e:  # pragma: no cover - nadir
            os.close(master)
            os.close(slave)
            return CommandResult(output=f"PTY çalıştırma hatası: {e}",
                                 exit_code=-1, session=session)
        os.close(slave)

        chunks: list[bytes] = []
        deadline = time.monotonic() + max(timeout, 0.1)
        timed_out = False
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                timed_out = proc.poll() is None
                if timed_out:
                    break
                remain = 0
            r, _, _ = select.select([master], [], [], max(remain, 0) if remain else 0)
            if not r:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master, 65536)
            except OSError:  # EIO: çocuk kapandı
                break
            if not data:
                break
            chunks.append(data)
        os.close(master)

        if timed_out:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except Exception:
                proc.kill()
        try:
            proc.wait(timeout=3)
        except Exception:
            pass

        raw = b"".join(chunks).decode("utf-8", "replace")
        code = proc.returncode if proc.returncode is not None else -1
        return CommandResult(output=_strip_ansi(raw).rstrip(),
                             exit_code=code, session=session,
                             timed_out=timed_out)

    # ------------------------------------------------------------ run (genel)
    def run(self, command: str, session: str = "default",
            timeout: float = 30.0) -> CommandResult:
        """Komutu oturumda çalıştır; ekranı oku, çıkış kodunu ayrıştır."""
        if not command or not command.strip():
            return CommandResult(output="", exit_code=0, session=session)
        if self.tmux:
            try:
                return self._run_tmux(command, session, timeout)
            except subprocess.TimeoutExpired:
                return CommandResult(output="tmux komutu zaman aşımına uğradı",
                                     exit_code=-1, session=session, timed_out=True)
        if _WINDOWS:
            return self._run_pipe(command, session, timeout)
        return self._run_pty(command, session, timeout)

    def _run_pipe(self, command: str, session: str, timeout: float) -> CommandResult:
        """Windows: komutu cmd.exe ile tek seferlik çalıştır."""
        try:
            r = subprocess.run(command, shell=True, capture_output=True,
                               timeout=max(timeout, 0.1),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"") + (e.stderr or b"")
            return CommandResult(output=out.decode(_console_encoding(), "replace").rstrip(),
                                 exit_code=-1, session=session, timed_out=True)
        enc = _console_encoding()
        out = r.stdout.decode(enc, "replace") + r.stderr.decode(enc, "replace")
        return CommandResult(output=out.replace("\r\n", "\n").rstrip(),
                             exit_code=r.returncode, session=session)

    # ------------------------------------------------------------ ekran oku
    def read_screen(self, session: str = "default") -> str:
        """Oturumun mevcut ekranını (görünür pencere) metin olarak döndür."""
        if self.tmux:
            name = self._session_name(session)
            if not self._ensure_session(name):
                return ""
            try:
                r = self._tmux("capture-pane", "-p", "-t", _pane(name))
                if r.returncode == 0:
                    return r.stdout.rstrip("\n")
            except (subprocess.TimeoutExpired, OSError):
                pass
            return ""
        # PTY modu: kalıcı oturumun biriken buffer'ı.
        ps = self._pty_sessions.get(_normalize_session(session))
        if ps is None:
            return ""
        ps.drain(wait=0.1)
        return _strip_ansi(ps.take()).rstrip("\n")

    # ------------------------------------------------------------ tuş gönder
    def send_keys(self, keys: str, session: str = "default") -> bool:
        """Tuşa bas: Enter/C-c/C-d/Escape gibi adlar veya düz metin.

        tmux ve PTY kipi aynı adları kabul eder: tmux yazımı (Enter, Escape, C-c,
        BSpace, Up…; büyük-küçük harf önemli) ve ctrl+x. "end", "up" gibi sıradan
        kelimeler düz metin olarak yazılır."""
        if not keys:
            return False
        k = keys.strip()
        if self.tmux:
            name = self._session_name(session)
            if not self._ensure_session(name):
                return False
            key = _key_name(k)
            try:
                if key:
                    r = self._tmux("send-keys", "-t", _pane(name), key)
                else:
                    r = self._tmux("send-keys", "-t", _pane(name), "-l", "--", keys)
                return r.returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                return False
        # PTY modu: kalıcı oturuma ham bayt yaz.
        key = _normalize_session(session)
        ps = self._pty_sessions.get(key)
        if ps is None:
            ps = _PipeSession() if _WINDOWS else _PtySession()
            self._pty_sessions[key] = ps
        ps.write(_keys_to_bytes(keys))
        return True

    # ------------------------------------------------------------ oturumlar
    def list_sessions(self) -> list[str]:
        """Yalnızca kendi ``nexremote_`` önekli oturumlarımızı listele."""
        if self.tmux:
            try:
                r = self._tmux("list-sessions", "-F", "#{session_name}")
            except (subprocess.TimeoutExpired, OSError):
                return []
            if r.returncode != 0:
                return []
            out: list[str] = []
            for ln in r.stdout.splitlines():
                nm = ln.strip()
                if nm.startswith(SESSION_PREFIX):
                    out.append(nm[len(SESSION_PREFIX):])
            return out
        return list(self._pty_sessions.keys())

    # ------------------------------------------------------------ kapat
    def close(self) -> None:
        """Süreçle ilgili kaynakları serbest bırak — SADECE PTY oturumları.

        tmux oturumları KALICI tasarlanmıştır: CLI çağrıları arasında yaşamalı
        ki `run` sonrası ayrı bir `screen` çağrısı ekranı okuyabilsin. Bu yüzden
        tmux oturumlarına DOKUNULMAZ. Açıkça temizlemek için ``cleanup()`` çağır
        (`nexremote cleanup`).
        """
        for ps in self._pty_sessions.values():
            ps.close()
        self._pty_sessions.clear()

    def cleanup(self) -> list[str]:
        """YALNIZCA kendi açtığı ``nexremote_`` önekli tmux oturumlarını kapat.

        Kullanıcının kendi tmux oturumlarına dokunulmaz. Kapatılan adları
        (öneksiz) döndürür. Bu, açık bir işlemdir (`nexremote cleanup`).
        """
        killed: list[str] = []
        if self.tmux:
            try:
                r = self._tmux("list-sessions", "-F", "#{session_name}")
                names = ([ln.strip() for ln in r.stdout.splitlines()
                          if ln.strip().startswith(SESSION_PREFIX)]
                         if r.returncode == 0 else [])
            except (subprocess.TimeoutExpired, OSError):
                names = []
            for nm in names:
                try:
                    self._tmux("kill-session", "-t", _sess(nm))
                    killed.append(nm[len(SESSION_PREFIX):])
                except (subprocess.TimeoutExpired, OSError):
                    pass
            self._owned.clear()
        for ps in self._pty_sessions.values():
            ps.close()
        self._pty_sessions.clear()
        return killed
