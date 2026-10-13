"""TUI / terminal backend — AI ajanının uzaktan terminal yönetimi.

İki strateji (otomatik seçilir):

  A) tmux varsa  — kalıcı oturumlar + `capture-pane` ile EKRAN OKUMA.
     TUI ajanları (htop, vim, claude-code, menülü CLI'lar) için kritik.
  B) tmux yoksa  — saf PTY (`pty.openpty` + `subprocess`).

AI ajanı için bu backend'in özü: "komut çalıştır, ekranı metin olarak OKU,
tuş gönder". Ekran okuma olmadan bir TUI ajanı yönetilemez.

GÜVENLİK: Bu backend yalnızca kendi açtığı ``nexremote_`` önekli tmux
oturumlarını yönetir. Kullanıcının kendi tmux oturumlarına ASLA dokunmaz.
"""
from __future__ import annotations

import os
import pty
import re
import select
import shutil
import subprocess
import time
from typing import Optional

from ..base import Backend
from ..models import Capability, CommandResult

# Tüm NexRemote oturumları bu öneki taşır — sahiplik/ayrım anahtarı.
SESSION_PREFIX = "nexremote_"

# tmux'ta doğrudan tuş adı olarak tanınan isimler (Enter, C-c, ...).
_TMUX_KEY_RE = re.compile(r"^(?:[CM]-[A-Za-z]|Enter|Escape|Esc|Space|Tab|BSpace|"
                          r"BTab|DC|IC|Up|Down|Left|Right|Home|End|PgUp|PgDn|"
                          r"F[0-9]{1,2}|KP[0-9]|C-\\\\)$", re.IGNORECASE)

# ANSI CSI / OSC kaçış dizileri (PTY çıktısını LLM dostu metne indirgemek için).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-_]")

# Sentinel (tamamlanma belirteci) biçimi: komut bitince ekrana basılır.
_START_TOKEN = "__NEXR_START__"
_EXIT_RE = re.compile(r"^__NEXR_(\d+)__$")


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


def _keys_to_bytes(keys: str) -> bytes:
    """Tuş adını PTY'ye yazılacak ham baytlara çevir (Enter/C-c/Escape...)."""
    table = {
        "enter": b"\r", "return": b"\r", "space": b" ", "tab": b"\t",
        "escape": b"\x1b", "esc": b"\x1b", "backspace": b"\x7f",
        "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C",
        "left": b"\x1b[D", "home": b"\x1b[H", "end": b"\x1b[F",
        "pgup": b"\x1b[5~", "pgdn": b"\x1b[6~", "delete": b"\x1b[3~",
    }
    k = (keys or "").strip()
    low = k.lower()
    if low in table:
        return table[low]
    # C-x / ctrl+x -> kontrol karakteri
    m = re.match(r"^(?:C-|ctrl\+)([A-Za-z])$", k, re.IGNORECASE)
    if m:
        return bytes([ord(m.group(1).upper()) - 64])
    return k.encode("utf-8", "replace")


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
        return "tmux" if self.tmux else "pty"

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
            if self._tmux("has-session", "-t", name).returncode == 0:
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
            r = self._tmux("capture-pane", "-p", "-S", "-", "-t", name)
            if r.returncode == 0:
                return r.stdout
        except (subprocess.TimeoutExpired, OSError):
            pass
        return ""

    def _session_exists(self, name: str) -> bool:
        try:
            return self._tmux("has-session", "-t", name).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    # ------------------------------------------------------------ run (tmux)
    def _run_tmux(self, command: str, session: str,
                  timeout: float) -> CommandResult:
        name = self._session_name(session)
        if not self._ensure_session(name):
            return CommandResult(output=f"tmux oturumu açılamadı: {name}",
                                 exit_code=-1, session=session, timed_out=False)

        # Belirteçli satır: başlangıç işareti + komut + çıkış kodu.
        # $? kabuk tarafından genişletilir; __NEXR_<kod>__ olarak ekrana basılır.
        # ÖNEMLİ: yeni komut ekrana gelmeden ÖNCE geçmişteki (scrollback) eski
        # sentinel/exit belirteçleriyle yanlış eşleşmemek için taban çizgisi al.
        pre = self._capture(name)
        pre_starts = pre.count(_START_TOKEN)

        payload = f"echo {_START_TOKEN}; {command}; echo __NEXR_$?__"
        try:
            # -l (literal): özel karakterler tuş adı olarak yorumlanmasın.
            self._tmux("send-keys", "-t", name, "-l", payload)
            self._tmux("send-keys", "-t", name, "Enter")
        except (subprocess.TimeoutExpired, OSError) as e:
            return CommandResult(output=f"send-keys hatası: {e}",
                                 exit_code=-1, session=session)

        deadline = time.monotonic() + max(timeout, 0.1)
        # 1) YENİ start belirtecini bekle (eski sentinel'lere aldanma).
        start_deadline = min(time.monotonic() + 3.0, deadline)
        while time.monotonic() < start_deadline:
            if self._capture(name).count(_START_TOKEN) > pre_starts:
                break
            if not self._session_exists(name):
                return CommandResult(output="oturum kapandı (çıkış?)",
                                     exit_code=-1, session=session)
            time.sleep(0.05)

        # 2) Yeni start'tan SONRAKİ exit belirtecini bekle.
        timed_out = True
        while True:
            screen = self._capture(name)
            if screen.count(_START_TOKEN) > pre_starts:
                after = self._after_last_start(screen)
                exit_code = self._parse_exit(after.splitlines())
                if exit_code is not None:
                    out = self._extract_output(after.splitlines())
                    return CommandResult(output=out, exit_code=exit_code,
                                         session=session, timed_out=False)
            if not self._session_exists(name):
                # Kabuk çıktı: sentinel hiç basılmadı -> erken dur.
                return CommandResult(output=screen, exit_code=-1,
                                     session=session)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.12)

        # Zaman aşımı — eldeki ekranı döndür.
        return CommandResult(output=self._capture(name), exit_code=-1,
                             session=session, timed_out=timed_out)

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
        return self._run_pty(command, session, timeout)

    # ------------------------------------------------------------ ekran oku
    def read_screen(self, session: str = "default") -> str:
        """Oturumun mevcut ekranını (görünür pencere) metin olarak döndür."""
        if self.tmux:
            name = self._session_name(session)
            if not self._ensure_session(name):
                return ""
            try:
                r = self._tmux("capture-pane", "-p", "-t", name)
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
        """Tuşa bas: Enter/C-c/C-d/Escape gibi adlar veya düz metin."""
        if not keys:
            return False
        if self.tmux:
            name = self._session_name(session)
            if not self._ensure_session(name):
                return False
            try:
                if _TMUX_KEY_RE.match(keys.strip()):
                    r = self._tmux("send-keys", "-t", name, keys.strip())
                else:
                    r = self._tmux("send-keys", "-t", name, "-l", keys)
                return r.returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                return False
        # PTY modu: kalıcı oturuma ham bayt yaz.
        key = _normalize_session(session)
        ps = self._pty_sessions.get(key)
        if ps is None:
            ps = _PtySession()
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
        """YALNIZCA kendi açtığı nexremote_ oturumlarını kapat.

        Kullanıcının kendi tmux oturumlarına dokunulmaz.
        """
        if self.tmux:
            try:
                r = self._tmux("list-sessions", "-F", "#{session_name}")
                names = []
                if r.returncode == 0:
                    names = [ln.strip() for ln in r.stdout.splitlines()
                             if ln.strip().startswith(SESSION_PREFIX)]
            except (subprocess.TimeoutExpired, OSError):
                names = []
            for nm in names:
                try:
                    self._tmux("kill-session", "-t", nm)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            self._owned.clear()
        for ps in self._pty_sessions.values():
            ps.close()
        self._pty_sessions.clear()
