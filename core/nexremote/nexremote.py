"""NexRemote — ana facade.

Tüm backend'leri toplar, tek bir birleşik API sunar. Adaptörler (MCP/CLI/HTTP)
bu sınıfı sarar. Hiçbir adaptör backend detayını bilmez.

İşletim sistemine bağlı her şey (pencereler, fare/klavye, ekran görüntüsü,
masaüstündeki terminaller) `platforms.Desktop` üzerinden gider; erişilebilirlik
ağacı (öğe bul/tıkla) platformun verdiği GUI backend'idir (AT-SPI / UIA).

HIZ (v0.7)
    * Her genel çağrı bir "eylem kapsamı" açar: pencere listeleri ve çözülen
      öğe kapsam boyunca bir kez okunur (eskiden tek tıkta 3-4 kez).
    * Ajan imleci gerçek tıkı beklemez: komutlar gönderilir, tık hemen atılır.
    * Ref çözümlemesi önce yol + kimlik hızlı yolunu dener (snapshot.py).
    * `after()` eylemden sonra değişeni döndürür; ayrı doğrulama turu gerekmez.
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from . import launch as launcher
from . import snapshot as snap
from . import trace
from .lease import input_lease
from .base import Backend
from .models import CommandResult, Element, Role, Screenshot, Window
from .platforms import Desktop, Unsupported, detect
from .platforms.base import runtime_dir as _runtime_dir


def _save_shot_meta(shot: Screenshot) -> None:
    with open(os.path.join(_runtime_dir(), "nexremote-shot.json"), "w") as f:
        json.dump(shot.to_dict(), f)


def _find_node(root: Element, ref: str) -> Optional[Element]:
    stack = [root]
    while stack:
        n = stack.pop()
        if n.ref == ref:
            return n
        stack.extend(n.children)
    return None


def _count(root: Element) -> int:
    n, stack = 0, [root]
    while stack:
        e = stack.pop()
        n += 1
        stack.extend(e.children)
    return n


def cursor_seconds() -> float:
    """Ajan imlecinin hedefe süzülme süresi (NEXREMOTE_CURSOR_MS, varsayılan 80)."""
    try:
        ms = float(os.environ.get("NEXREMOTE_CURSOR_MS", "80"))
    except ValueError:
        ms = 80.0
    return max(0.001, ms / 1000)


class NotFound(snap.RefError):
    code = "NOT_FOUND"


class NexRemote:
    # Sınıf düzeyinde varsayılanlar: testler örneği __init__ olmadan kurar.
    _memo: Optional[dict] = None
    long_lived = False

    def __init__(self, backends: Optional[list[Backend]] = None,
                 desktop: Optional[Desktop] = None, long_lived: bool = False):
        self.desktop: Desktop = desktop if desktop is not None else detect()
        # Varsayılan arka uçlar ilk kullanımda yüklenir: AT-SPI kurulumu ~0.2 sn
        # sürer ve koordinatla tık, klavye, ekran görüntüsü ona hiç ihtiyaç duymaz.
        self._backends: Optional[dict[str, Backend]] = (
            None if backends is None else {b.name: b for b in backends})
        # Uzun ömürlü süreç (servis, MCP, HTTP): olay döngüsü kuyruğu her
        # eylemden önce boşaltılır, pencere listesi izlenebilir.
        self.long_lived = long_lived
        if long_lived and hasattr(self.desktop, "enable_watch"):
            try:
                self.desktop.enable_watch()
            except Exception:
                pass

    @property
    def backends(self) -> dict[str, Backend]:
        if self._backends is None:
            with trace.span("arka uçlar"):
                self._backends = {b.name: b for b in self._default_backends(self.desktop)}
        return self._backends

    @staticmethod
    def _default_backends(desktop: Desktop) -> list[Backend]:
        """Var olan tüm backends'leri yükle. Bir backend eksik/bozuksa
        diğerlerini engellemesin — kısmi ortamlarda da çalışmalı."""
        out: list[Backend] = []
        try:
            gui = desktop.gui_backend()
            if gui is not None:
                out.append(gui)
        except Exception:
            pass
        try:
            from .backends.tui import TuiBackend
            out.append(TuiBackend())
        except Exception:
            pass
        return out

    def backend(self, name: str) -> Backend:
        if name not in self.backends:
            raise KeyError(f"backend yok: {name}. Mevcut: {list(self.backends)}")
        return self.backends[name]

    def _gui(self) -> Backend:
        if "gui" not in self.backends:
            raise Unsupported(
                f"erişilebilirlik ağacı bu sistemde yok ({self.desktop.name}) — "
                "`nexremote caps` eksikleri gösterir; ekran görüntüsü + koordinat "
                "tıklaması her zaman kullanılabilir")
        return self.backends["gui"]

    def _tui(self) -> Backend:
        return self.backend("tui")

    # ------------------------------------------------------------ eylem kapsamı
    @contextmanager
    def _scope(self) -> Iterator[None]:
        """Bir genel çağrı boyunca okunanları paylaş (pencere listeleri, çözülen
        öğe). İç içe çağrılar dıştakinin kapsamını kullanır."""
        if self._memo is not None:
            yield
            return
        self._pump()
        self._memo = {}
        try:
            yield
        finally:
            self._memo = None

    def _m(self, key: str, fn: Callable[[], Any]) -> Any:
        memo = self._memo
        if memo is None:
            return fn()
        if key not in memo:
            memo[key] = fn()
        return memo[key]

    def _forget(self) -> None:
        """Pencereleri değiştiren eylemden sonra (etkinleştir, kapat…)."""
        if self._memo is not None:
            self._memo.clear()

    def _pump(self) -> None:
        """Uzun ömürlü süreçte GLib kuyruğunu boşalt: AT-SPI önbelleğini
        güncelleyen sinyaller ve KWin izleyicisinin bildirimleri işlenir."""
        if not self.long_lived:
            return
        GLib = sys.modules.get("gi.repository.GLib")
        if GLib is None:
            return
        try:
            ctx = GLib.MainContext.default()
            n = 0
            while ctx.pending() and n < 20000:
                ctx.iteration(False)
                n += 1
        except Exception:
            pass

    # ---------------------------------------------------------------- keşif
    def capabilities(self) -> dict[str, Any]:
        """Ne yapabildiğimi anlat — metin-tabanlı modeller bunu okur."""
        caps = {name: b.capability() for name, b in self.backends.items()}
        try:
            dcaps = {k: c.to_dict() for k, c in self.desktop.caps().items()}
        except Exception as e:
            dcaps = {k: {"available": False, "detail": f"yoklanamadı: {e}"}
                     for k in ("windows", "input", "screenshot", "terminals")}
        return {
            "platform": self.desktop.name,
            "backends": {
                n: {"available": c.available, "detail": c.detail, "needs": c.needs}
                for n, c in caps.items()
            },
            **dcaps,
            "pointer": self._pointer_cap(),
            "actions": [
                "windows", "window_action", "tree", "find", "click", "click_at",
                "click_text", "fill", "move", "scroll", "drag", "key", "type_text",
                "set_text", "screenshot", "terminals", "terminal_read", "terminal_send",
                "run", "read_screen", "send_keys", "sessions", "cursor", "after", "wait",
            ],
        }

    def setup(self) -> list[tuple[str, bool, str]]:
        """İlk kurulum (izinler, erişilebilirlik) — platforma özgü."""
        return self.desktop.setup()

    def _pointer_cap(self) -> dict[str, Any]:
        try:
            ok, msg = self.pointer_available()
            return {"available": ok, "detail": msg}
        except Exception as e:
            return {"available": False, "detail": str(e)}

    # ---------------------------------------------------------------- GUI
    def windows(self) -> list[Window]:
        """Ekrandaki pencereler. Pencere yöneticisi her pencereyi bilir;
        erişilebilirlik ağacı yalnızca yayınlayanları — ona yalnızca
        pencere yöneticisine erişilemezse düşülür."""
        return self._m("wm", self._windows_now)

    def _windows_now(self) -> list[Window]:
        with trace.span("pencereler"):
            try:
                return [Window(ref=f"win:{w.id}", title=w.title, app=w.app, pid=w.pid,
                               focused=w.active, bounds=(w.x, w.y, w.w, w.h))
                        for w in self.desktop.windows()]
            except Unsupported:
                if "gui" not in self.backends:
                    raise
                return self._gui().windows()

    def _gwin(self) -> list[Window]:
        """Erişilebilirlik ağacının pencereleri (kapsam boyunca bir kez)."""
        def read():
            with trace.span("ağaç pencereleri"):
                return self._gui().windows()
        return self._m("gwin", read)

    def window_action(self, ref: str, action: str) -> bool:
        """activate | close | minimize — ref: windows() çıktısındaki win:{...}"""
        if not ref.startswith("win:"):
            raise ValueError("pencere eylemi win:... ref'i ister; `windows` ile alın")
        if action not in ("activate", "close", "minimize"):
            raise ValueError(f"bilinmeyen pencere eylemi: {action}")
        try:
            return self.desktop.window_action(ref[4:], action)
        finally:
            self._forget()

    def _focused_ref(self) -> Optional[str]:
        try:
            ws = self.windows()
        except Exception:
            return None
        foc = [w for w in ws if w.focused]
        return foc[0].ref if foc else None

    def _read_tree(self, ref: str, light: bool = False) -> Element:
        gui = self._gui()
        with trace.span("ağaç okuma", kip="hafif" if light else "tam") as info:
            try:
                root = gui.tree(ref, light=light) if light else gui.tree(ref)
            except TypeError:                  # light parametresi olmayan arka uç
                root = gui.tree(ref)
            if trace.enabled():
                info["düğüm"] = _count(root)
        return root

    def snapshot(self, window_ref: Optional[str] = None,
                 root: Optional[str] = None) -> tuple["snap.Snapshot", Optional[Element]]:
        """Pencere ağacını oku, eyleme açık her öğeye @sid:eN ref'i ver.
        root: bir anlık görüntü ref'i verilirse yalnızca o öğenin alt ağacı
        döner (yoğun uygulamalarda önce iskelet, sonra bölge)."""
        with self._scope():
            sub_ref = None
            if root:
                el = self._resolve_el(root, need_enabled=False, wait=0)
                sub_ref = el.ref
                data = snap.load(snap.parse_ref(root)[0])
                window_ref = snap.find_window(data["window"], self._gwin()).ref
            if not window_ref:
                window_ref = self._focused_ref()   # etkin pencere (arka ucun ilki değil)
            ref = self._gui_ref(window_ref) if window_ref else ""
            tree = self._read_tree(ref)
            s = snap.take(tree, self._gwin())
            sub = None
            if sub_ref:
                sub = _find_node(tree, sub_ref)
                if sub is None:
                    raise snap.StaleRef(f"{root} yeniden okumada kayboldu — yeniden `tree` alın")
            return s, sub

    def tree(self, window_ref: Optional[str] = None, interactive: bool = False,
             root: Optional[str] = None) -> str:
        """Pencere ağacını LLM dostu metin olarak döndür (ref'ler @sid:eN)."""
        with self._scope():
            s, sub = self.snapshot(window_ref, root)
            text = s.render(interactive=interactive, subtree=sub)
            if s.root.truncated:
                text += ("\n# ağaç kısaltıldı (düğüm/süre sınırı: NEXREMOTE_TREE_NODES, "
                         "NEXREMOTE_TREE_MS) — `tree --root @…` ile bölgeye inin")
            if not s.root.children and sub is None:
                win = next((w for w in self._gwin() if w.ref == s.root.ref), None)
                exe = launcher.pid_exe(win.pid) if win is not None else None
                if launcher.is_chromium(exe):
                    text += "\n# " + launcher.electron_hint(win.title or win.app, exe)
            return text

    def _resolve_el(self, ref: str, need_enabled: bool = True,
                    wait: Optional[float] = None) -> Element:
        gui = self._gui()
        if wait is None:
            wait = float(os.environ.get("NEXREMOTE_WAIT", snap.DEFAULT_WAIT))
        el = snap.resolve(ref, lambda r: self._read_tree(r, light=True), self._gwin,
                          wait=wait, need_enabled=need_enabled,
                          probe=getattr(gui, "probe", None))
        if self._memo is not None:
            self._memo["el:" + el.ref] = el
        return el

    def _live(self, ref: str, need_enabled: bool = True) -> str:
        """Anlık görüntü ref'ini katı kimlik doğrulamasıyla canlı arka uç
        ref'ine çevir; ham arka uç ref'leri (geriye uyum) olduğu gibi geçer."""
        if snap.is_snapshot_ref(ref):
            return self._resolve_el(ref, need_enabled).ref
        return ref

    def launch(self, argv: list[str], wait: float = 15.0) -> dict:
        """Programı erişilebilirlik açık başlat (Electron'a bayrağı ekler) ve
        açılan pencerenin win: ref'ini döndür."""
        return launcher.launch(argv, self._windows_now, wait)

    def _gui_ref(self, ref: str) -> str:
        """win:{id} (pencere yöneticisi) → aynı pencerenin erişilebilirlik ref'i.

        Arka uç kimliği doğrudan eşleyebiliyorsa (UIA: HWND, AX: CGWindowID) o
        kullanılır. AT-SPI'de süreç + başlık + boyutla eşlenir; eşit adaylar
        arasında tahmin yapılmaz (AMBIGUOUS_TARGET)."""
        if not ref.startswith("win:"):
            return ref
        target = next((w for w in self.windows() if w.ref == ref), None)
        if target is None:
            raise ValueError(f"pencere bulunamadı: {ref}")
        gui = self._gui()
        direct = getattr(gui, "ref_for_window", None)
        if direct is not None:
            r = direct(target)
            if r:
                return r
        cands = [w for w in self._gwin() if target.pid is not None and w.pid == target.pid]
        if not cands:
            raise RuntimeError(
                f"'{target.title}' erişilebilirlik ağacı yayınlamıyor — "
                "`nexremote setup` ile erişilebilirliği açıp uygulamayı yeniden başlatın "
                "ya da ekran görüntüsü + koordinat tıklaması kullanın")
        if len(cands) == 1:
            return cands[0].ref
        pool = ([w for w in cands if w.title == target.title]
                or [w for w in cands if w.title and target.title.startswith(w.title)]
                or cands)
        if len(pool) == 1:
            return pool[0].ref
        if target.bounds:
            tx, ty, tw, th = target.bounds

            def dist(w: Window) -> int:
                if not w.bounds:
                    return 10 ** 9
                x, y, ww, hh = w.bounds
                return abs(ww - tw) + abs(hh - th)
            ranked = sorted(pool, key=dist)
            if dist(ranked[0]) < dist(ranked[1]):
                return ranked[0].ref
        raise snap.AmbiguousTarget(
            f"'{target.title}' için {len(pool)} erişilebilirlik penceresi eşleşiyor "
            "(aynı süreç, aynı başlık) — pencereyi öne alıp `tree` deneyin ya da "
            "`tree --window atspi:…` ile doğrudan seçin", candidates=[w.ref for w in pool])

    # geriye uyum: eski ad
    _atspi_ref = _gui_ref

    def find(self, query: str, window_ref: Optional[str] = None) -> list[Element]:
        """Adı/değeri eşleşen eyleme açık öğeler; ref'leri anlık görüntü ref'i.
        Yalnızca sonuç çıkan pencereler için anlık görüntü yazılır (her pencere
        için yazmak, elde tutulan eski ref'leri depodan taşırırdı)."""
        q = (query or "").lower().strip()
        if not q:
            return []
        with self._scope():
            wins = self._gwin()
            refs = [self._gui_ref(window_ref)] if window_ref else [w.ref for w in wins]
            hits: list[Element] = []
            for r in refs:
                try:
                    tree = self._read_tree(r)
                except Exception:
                    if window_ref:
                        raise
                    continue                    # tek bozuk pencere aramayı durdurmasın
                matched = [n for n, _ in snap._walk(tree)
                           if snap.actionable(n) and (q in n.name.lower()
                                                      or q in (n.value or "").lower())]
                if not matched:
                    continue
                s = snap.take(tree, wins)
                for node in matched:
                    hits.append(Element(ref=s.refs[node.ref], role=node.role,
                                        name=node.name, value=node.value,
                                        actions=node.actions, bounds=node.bounds,
                                        focused=node.focused, enabled=node.enabled))
            return hits

    # ----------------------------------------------------------- ref eylemleri
    def click(self, ref: str) -> bool:
        """Öğeye isimle tıkla. Öğe eylem sunmuyorsa ortasına gerçek tık."""
        with trace.span("click", ref=ref), self._scope():
            return self._click_backend(self._live(ref))

    def _click_backend(self, bref: str) -> bool:
        gui = self._gui()
        rect, proven = self._safe_rect(bref)
        ok_rect = bool(rect and rect[2] > 0 and rect[3] > 0)
        if proven and ok_rect:
            self._agent_cursor(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
        with self._lease():
            with trace.span("gönderim"):
                if gui.click(bref):
                    return True
                # Eylem sunmayan öğe: ortasına gerçek tık — yalnızca konum
                # kanıtlıysa (pencere dışına düşen konum başka bir şeye basardı).
                if not (proven and ok_rect):
                    return False
                self.desktop.click(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
        self._forget()
        return True

    def bounds(self, ref: str) -> Optional[tuple[int, int, int, int]]:
        """Öğenin ekran dikdörtgeni (x, y, w, h). Wayland'de AT-SPI konumu
        pencereye göreli gelir; pencere kökü eklenir."""
        with self._scope():
            return self._screen_bounds(ref)[0]

    def _safe_rect(self, bref: str):
        try:
            return self._screen_bounds(bref)
        except Exception:
            return (None, False)

    def _raw_bounds(self, bref: str) -> Optional[tuple[int, int, int, int]]:
        """Arka ucun verdiği dikdörtgen; bu kapsamda çözülen öğe varsa onunki."""
        el = (self._memo or {}).get("el:" + bref)
        if el is not None and el.bounds:
            return el.bounds
        return self._gui().bounds(bref)

    def _screen_bounds(self, ref: str):
        """(dikdörtgen, kanıtlı) döndürür. agent-desktop mantığı: konum tek
        kaynaktan gelmez; üretilen nokta pencere içine düşmüyorsa KANITSIZDIR,
        yanlış yere imleç koymaktansa işaretlenir (kanıtlı=False)."""
        ref = self._live(ref, need_enabled=False)
        b = self._raw_bounds(ref)
        if not b or not ref.startswith("atspi:"):
            return (b, b is not None)
        dx, dy, win = self._window_offset(ref)
        rect = (b[0] + dx, b[1] + dy, b[2], b[3])
        if win is None:
            return (rect, True)                 # pencere bulunamadı: en iyi tahmin
        wx, wy, ww, wh = win
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        proven = wx - 2 <= cx <= wx + ww + 2 and wy - 2 <= cy <= wy + wh + 2
        return (rect, proven)

    def _window_offset(self, ref: str):
        """(dx, dy, pencere_dikdörtgeni) döndürür. AT-SPI çerçeve konumu ile
        pencere yöneticisi konumunu kıyaslar. Çerçeve konumu WM'inkiyle zaten
        örtüşüyorsa toolkit global konum veriyordur → offset 0 (X11). Örtüşmüyorsa
        konum pencereye görelidir → WM içerik kökü eklenir (XWayland/Wayland)."""
        parts = ref.split("/")[0]
        try:
            frame = next((w for w in self._gwin() if w.ref == parts), None)
            if frame is None or not frame.bounds or not frame.pid:
                return (0, 0, None)
            cands = [w for w in self.windows() if w.pid == frame.pid and w.bounds]
        except Exception:
            return (0, 0, None)
        if not cands:
            return (0, 0, None)
        fx, fy, fw, fh = frame.bounds

        def rank(w) -> tuple[bool, int]:
            same_title = bool(frame.title) and w.title.startswith(frame.title)
            return (not same_title, abs(w.bounds[2] - fw) + abs(w.bounds[3] - fh))
        win = min(cands, key=rank).bounds
        wx, wy, ww, wh = win
        # origins_agree: çerçeve zaten ekran konumunda mı? (±dekorasyon payı)
        pad = max(0, ww - fw) + 4
        if abs(fx - wx) <= pad and abs(fy - wy) <= (wh - fh) + pad:
            return (0, 0, win)                  # global konum: node bounds ekranda
        side = max(0, ww - fw) // 2             # dekorasyon: kenar
        top = max(0, wh - fh - side)            #             + başlık çubuğu
        return (wx + side - fx, wy + top - fy, win)

    def set_text(self, ref: str, text: str) -> bool:
        """Alanın içeriğini değiştir. Arka uç metni doğrudan yazamıyorsa öğe
        odaklanır, tümü seçilir ve yazılır — odak alınamazsa HİÇBİR ŞEY yazılmaz
        (metin başka bir alana gitmesin)."""
        with trace.span("set_text", ref=ref), self._scope():
            gui = self._gui()
            bref = self._live(ref)
            self._agent_cursor_at(bref)
            with self._lease():
                if gui.set_text(bref, text):
                    return True
                if not gui.focus(bref):
                    return False
                self.desktop.key(self.desktop.select_all_combo())
                if text:
                    self.desktop.type_text(text)
                return True

    def fill(self, ref: str, text: str, enter: bool = False) -> bool:
        """set_text + istenirse Enter (odak öğeye alınarak)."""
        with self._scope():
            if not self.set_text(ref, text):
                return False
            if enter:
                bref = self._live(ref, need_enabled=False)
                with self._lease():
                    self._gui().focus(bref)
                    self.desktop.key("enter")
            return True

    def click_text(self, text: str, window_ref: Optional[str] = None,
                   role: Optional[str] = None, exact: bool = False) -> dict:
        """Adıyla bul ve tıkla — tek okuma. Tam ad eşleşmesi (büyük-küçük harf
        duyarsız) tercih edilir; yoksa içeren. Birden fazla aday varsa tahmin
        yapılmaz: AMBIGUOUS_TARGET adayların ref'leriyle döner."""
        q = (text or "").strip().lower()
        if not q:
            raise ValueError("aranacak metin boş")
        with trace.span("click_text", metin=text), self._scope():
            s, _ = self.snapshot(window_ref)
            nodes = [n for n, _ in snap._walk(s.root) if n.ref in s.refs]
            if role:
                nodes = [n for n in nodes if n.role.value == role]
            same = [n for n in nodes if n.name.strip().lower() == q]
            cands = same or ([] if exact else [n for n in nodes if q in n.name.lower()])
            if not cands:
                raise NotFound(f"'{text}' adlı eyleme açık öğe yok — `tree -i` ile bakın",
                               snapshot=s.sid)
            if len(cands) > 1:
                raise snap.AmbiguousTarget(
                    f"'{text}' {len(cands)} öğeyle eşleşiyor — ref ile tıklayın",
                    candidates=[f"{s.refs[n.ref]} [{n.role.value}] {n.name}" for n in cands[:10]])
            node = cands[0]
            if self._memo is not None:
                self._memo["el:" + node.ref] = node
            if not node.enabled:
                # devre dışı: bekleme kuralı ref'li tıklamayla aynı olsun
                node = self._resolve_el(s.refs[node.ref])
            ok = self._click_backend(node.ref)
            return {"ok": ok, "ref": s.refs[cands[0].ref], "role": node.role.value,
                    "name": node.name}

    def key(self, combo: str) -> bool:
        with trace.span("key", combo=combo), self._lease():
            self.desktop.key(combo)
        self._forget()
        return True

    def type_text(self, text: str) -> bool:
        with trace.span("type", n=len(text)), self._lease():
            self.desktop.type_text(text)
        return True

    @contextmanager
    def _lease(self) -> Iterator[None]:
        with trace.span("kira"):
            ctx = input_lease()
            ctx.__enter__()
        try:
            yield
        finally:
            ctx.__exit__(None, None, None)

    # --------------------------------------------------------- gerçek fare
    def click_at(self, x: float, y: float, button: str = "left", count: int = 1,
                 restore_pointer: bool = True, show_cursor: bool = True,
                 from_shot: bool = False) -> bool:
        """Ekran koordinatına gerçek tıklama. Sistemde tek imleç vardır;
        kullanıcının imleci tıklamadan sonra eski yerine geri konur.

        from_shot=True: (x, y) son ekran görüntüsü üzerindeki pikseldir.
        """
        self._check_button(button)
        with trace.span("click_at", x=x, y=y), self._scope():
            x, y = self.screen_point(x, y, from_shot)
            with trace.span("imleç konumu"):
                back = self._user_pointer() if restore_pointer else None
            if show_cursor:
                self._agent_cursor(x, y)
            with self._lease(), trace.span("gönderim"):
                self.desktop.click_and_return(x, y, button, int(count), back)
            self._forget()
        return True

    @staticmethod
    def _check_button(button: str) -> None:
        if button not in ("left", "right", "middle"):
            raise ValueError(f"bilinmeyen fare düğmesi: {button}")

    def screen_point(self, x: float, y: float, from_shot: bool) -> tuple[int, int]:
        if not from_shot:
            return int(x), int(y)
        shot = self.last_screenshot()
        if shot is None:
            raise RuntimeError("kayıtlı ekran görüntüsü yok — önce screenshot alın")
        return shot.to_screen(x, y)

    def move(self, x: int, y: int) -> bool:
        with self._lease():
            self.desktop.move(int(x), int(y))
        return True

    def scroll(self, dy: int, dx: int = 0, at: Optional[tuple[int, int]] = None,
               restore_pointer: bool = True) -> bool:
        with trace.span("scroll"), self._scope():
            back = self._user_pointer() if (at and restore_pointer) else None
            if at:
                self._agent_cursor(int(at[0]), int(at[1]), click=False)
            with self._lease():
                if at:
                    self.desktop.move(int(at[0]), int(at[1]))
                self.desktop.scroll(int(dy), int(dx))
                if back is not None:
                    self.desktop.move(*back)
        return True

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left",
             steps: int = 12) -> bool:
        self._check_button(button)
        steps = max(1, int(steps))
        with trace.span("drag"), self._scope():
            back = self._user_pointer()
            self._agent_cursor(int(x1), int(y1), click=False)
            with self._lease():
                self.desktop.move(int(x1), int(y1))
                self.desktop.button(button, True)
                try:
                    for i in range(1, steps + 1):
                        self.desktop.move(x1 + (x2 - x1) * i / steps,
                                          y1 + (y2 - y1) * i / steps)
                        time.sleep(0.01)
                finally:
                    self.desktop.button(button, False)
                self._agent_cursor(int(x2), int(y2), click=False)
                if back is not None:
                    self.desktop.move(*back)
            self._forget()
        return True

    def _user_pointer(self) -> Optional[tuple[int, int]]:
        try:
            return self.desktop.pointer_pos()
        except Exception:
            return None  # imleci geri koymak isteğe bağlı; konum bilinmiyorsa atla

    def _agent_cursor(self, x: int, y: int, click: bool = True) -> None:
        """Görsel geri bildirim: ajan imleci hedefe gider. BEKLENMEZ — komutlar
        gönderilir, gerçek eylem hemen yapılır (imleç ~80 ms süzülür). Daemon
        yoksa arka planda başlatılır ve bu komutları kendisi çalıştırır; hata
        yutulur çünkü eylemin kendisi imlece bağlı değildir."""
        if os.environ.get("NEXREMOTE_CURSOR", "1") == "0":
            return
        if not self._m("ptr-ok", lambda: self.pointer_available()[0]):
            return
        # SHOW: cursor-hide sonrası da görünsün.
        cmds = ["SHOW", f"MOVE {int(x)} {int(y)} {cursor_seconds():g}"] + (["CLICK"] if click else [])
        from . import agent_cursor as ac
        with trace.span("imleç"):
            try:
                ac.batch(cmds, wait=0)
            except (OSError, ValueError, KeyError):   # daemon yok: başlat, komutları o çalıştırsın
                try:
                    ac.spawn_daemon(self._cursor_display(), cmds)
                except Exception:
                    pass
            except Exception:
                pass

    def _agent_cursor_at(self, ref: str) -> None:
        """Ref ile yapılan eylemde de ajan imleci öğenin üstüne gitsin. Konumu
        olmayan ya da KANITSIZ (pencere dışına düşen) öğede imleç atlanır;
        eylem yine çalışır — yanlış yere imleç koymaktansa hiç koyma."""
        try:
            b, proven = self._screen_bounds(ref)
        except Exception:
            return
        if proven and b and b[2] > 0 and b[3] > 0:
            self._agent_cursor(b[0] + b[2] // 2, b[1] + b[3] // 2)

    @staticmethod
    def _cursor_display() -> str:
        return os.environ.get("DISPLAY", "") if sys.platform.startswith("linux") else ""

    @classmethod
    def _cursor_daemon(cls):
        """Ajan imleci daemon'ı (gerekirse başlatılır, hazır olması beklenir)."""
        from . import agent_cursor as ac
        if not ac.daemon_running():
            ac.start_daemon(cls._cursor_display())
        if not ac.daemon_running():
            raise RuntimeError("ajan imleci daemon'ı başlatılamadı")
        return ac

    # -------------------------------------------- masaüstündeki terminaller
    def terminals(self) -> list[dict]:
        return [t.to_dict() for t in self.desktop.terminals()]

    def terminal_read(self, ref: str, history: int = 0) -> str:
        return self.desktop.terminal_read(ref, history)

    def terminal_history_supported(self, ref: str) -> bool:
        return self.desktop.terminal_history_supported(ref)

    def terminal_send(self, ref: str, text: str = "", enter: bool = False,
                      keys: Optional[list[str]] = None) -> bool:
        if ref.startswith("terminal:"):
            # Terminal.app gerçek klavye olayıyla yazılır: diğer ajanlarla sıraya gir
            with self._lease():
                self.desktop.terminal_send(ref, text, enter=enter, keys=keys)
        else:
            self.desktop.terminal_send(ref, text, enter=enter, keys=keys)
        return True

    def screenshot(self, path: Optional[str] = None, window_ref: Optional[str] = None,
                   max_width: int = 0) -> Screenshot:
        """Ekran görüntüsü; istenirse tek pencereye kırpılır ve küçültülür.

        Ölçek ve orijin kaydedilir: `click_at(..., from_shot=True)` görüntü
        üzerindeki koordinatı ekran koordinatına kendisi çevirir.
        """
        with trace.span("screenshot"), self._scope():
            return self._screenshot(path, window_ref, max_width)

    def _screenshot(self, path, window_ref, max_width) -> Screenshot:
        path = os.path.abspath(path or os.path.join(_runtime_dir(), "nexremote-shot.png"))
        wbounds = None
        if window_ref:
            w = next((w for w in self.windows() if w.ref == window_ref), None)
            if w is None or not w.bounds:
                raise ValueError(f"pencere bulunamadı: {window_ref}")
            wbounds = w.bounds
        # Bellekteki kare varsa kırp/küçült, PNG'yi yalnızca son boyutta bir kez kodla.
        # Yayın servisi kırpmayı kendisi yapabiliyorsa yalnızca pencerenin baytları gelir.
        grab = getattr(self.desktop, "screenshot_image", None)
        frame = None
        if grab is not None:
            with trace.span("kare"):
                try:
                    frame = grab(crop=wbounds)
                except TypeError:
                    frame = grab()
        img = None
        if frame is not None:
            img, origin, scale = frame
            shot = Screenshot(path=path, width=img.width, height=img.height,
                              backend="pipewire", scale=scale, origin=origin,
                              display=os.environ.get("WAYLAND_DISPLAY"))
        else:
            shot = self.desktop.screenshot(path)
        crop = None
        if wbounds:
            # ekran koordinatı → görüntü pikseli (orijin ve ölçek platformdan gelir)
            x, y, ww, hh = wbounds
            ox, oy, sc = shot.origin[0], shot.origin[1], shot.scale
            crop = (max(0, round((x - ox) * sc)), max(0, round((y - oy) * sc)),
                    min(shot.width, round((x + ww - ox) * sc)),
                    min(shot.height, round((y + hh - oy) * sc)))
            if crop[2] <= crop[0] or crop[3] <= crop[1]:
                raise ValueError(f"pencere ekranın dışında: {window_ref}")
            if crop == (0, 0, shot.width, shot.height):
                crop = None                    # kare zaten pencereye kırpılmış
        if crop or (max_width and shot.width > max_width):
            try:
                from PIL import Image
            except ImportError as e:
                raise RuntimeError("kırpma/küçültme için Pillow gerekli "
                                   "(pip install pillow)") from e
            if img is None:
                img = Image.open(path)
            if crop:
                img = img.crop(crop)
                shot.origin = (round(shot.origin[0] + crop[0] / shot.scale),
                               round(shot.origin[1] + crop[1] / shot.scale))
            if max_width and img.width > max_width:
                factor = max_width / img.width
                shot.scale *= factor
                img = img.resize((max_width, round(img.height * factor)), Image.LANCZOS,
                                 reducing_gap=3.0)
            with trace.span("png"):
                img.save(path, compress_level=1)   # dosya biraz büyür, kayıt ~5 kat hızlı
            shot.width, shot.height = img.size
        elif img is not None:
            with trace.span("png"):
                img.save(path, compress_level=1)
        _save_shot_meta(shot)
        return shot

    @staticmethod
    def last_screenshot() -> Optional[Screenshot]:
        try:
            with open(os.path.join(_runtime_dir(), "nexremote-shot.json")) as f:
                d = json.load(f)
            d["origin"] = tuple(d["origin"])
            return Screenshot(**d)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def focus(self, ref: str) -> bool:
        with self._scope():
            ref = self._live(ref, need_enabled=False)
            self._agent_cursor_at(ref)
            with self._lease():
                return self._gui().focus(ref)

    # --------------------------------------------- eylem sonrası + bekleme
    def after(self, window_ref: Optional[str] = None,
              before_windows: Optional[list[Window]] = None,
              settle: Optional[float] = None) -> str:
        """Eylemden sonra ne değişti: arayüzün durulması kısa süre beklenir,
        etkin (ya da verilen) pencerenin eyleme açık öğeleri o pencerenin son
        anlık görüntüsüyle karşılaştırılır. Yeni öğelerin ref'leri yazılır;
        eski ref'ler kimlikle çözüldüğü için geçerli kalır. Önceki görüntü
        yoksa etkileşimli ağacın tamamı döner."""
        if settle is None:
            try:
                settle = float(os.environ.get("NEXREMOTE_SETTLE", "0.15"))
            except ValueError:
                settle = 0.15
        if settle > 0:
            time.sleep(settle)
        lines: list[str] = []
        with trace.span("after"), self._scope():
            try:
                wins = self.windows()
            except Exception:
                wins = []
            foc = next((w for w in wins if w.focused), None)
            if before_windows is not None:
                old = {w.ref: w for w in before_windows}
                new = {w.ref: w for w in wins}
                for r, w in new.items():
                    if r not in old:
                        lines.append(f"+ pencere {r}  {w.app}  {w.title!r}")
                for r, w in old.items():
                    if r not in new:
                        lines.append(f"- pencere {r}  {w.title!r}")
                old_foc = next((w.ref for w in before_windows if w.focused), None)
                if foc is not None and foc.ref != old_foc:
                    lines.append(f"* odak → {foc.ref}  {foc.title!r}")
            target = window_ref or (foc.ref if foc else None)
            if "gui" not in self.backends:
                return "\n".join(lines or ["(erişilebilirlik ağacı yok — ekran görüntüsüyle doğrulayın)"])
            try:
                s, _ = self.snapshot(target)
            except Exception as e:
                lines.append(f"# ağaç okunamadı: {e}")
                return "\n".join(lines)
            head = f"# sonrası @{s.sid} — {s.window.title or s.window.app or s.window.ref}"
            prev = snap.previous(s.window.to_dict(), exclude=s.sid)
            if prev is None:
                return "\n".join([head] + lines + [s.render(interactive=True).split("\n", 1)[-1]])
            changes = snap.diff(prev, s)
            return "\n".join([head] + lines + (changes or ["(öğelerde değişiklik yok)"]))

    def wait_for(self, element: Optional[str] = None, window_title: Optional[str] = None,
                 gone: Optional[str] = None, window_ref: Optional[str] = None,
                 timeout: float = 10.0) -> dict:
        """Koşul gerçekleşene kadar bekle (içeride yoklar; model uyuyup tekrar
        sormaz). element: adı/değeri metni içeren öğe görünsün; window_title:
        başlığı metni içeren pencere açılsın; gone: ref'in öğesi kaybolsun.
        Süre dolarsa TIMEOUT."""
        if sum(x is not None for x in (element, window_title, gone)) != 1:
            raise ValueError("wait: --element, --window-title ya da --gone'dan tam olarak biri")
        deadline = time.monotonic() + max(0.0, timeout)
        delay = 0.08
        q = (element or window_title or "").strip().lower()
        while True:
            with self._scope():
                if window_title is not None:
                    w = next((w for w in self.windows() if q in w.title.lower()), None)
                    if w is not None:
                        return {"window": w.ref, "title": w.title, "app": w.app}
                elif gone is not None:
                    try:
                        self._resolve_el(gone, need_enabled=False, wait=0)
                    except snap.StaleRef:
                        return {"gone": gone}
                    except snap.AmbiguousTarget:
                        pass
                else:
                    target = window_ref or self._focused_ref()
                    ref = self._gui_ref(target) if target else ""
                    tree = self._read_tree(ref, light=True)
                    hit = next((n for n, _ in snap._walk(tree)
                                if q in n.name.lower() or q in (n.value or "").lower()), None)
                    if hit is not None:
                        full = self._read_tree(ref)
                        s = snap.take(full, self._gwin())
                        node = next((n for n, _ in snap._walk(full)
                                     if q in n.name.lower() or q in (n.value or "").lower()), hit)
                        return {"ref": s.refs.get(node.ref), "role": node.role.value,
                                "name": node.name, "snapshot": s.sid}
            if time.monotonic() >= deadline:
                what = (f"'{element}' öğesi" if element is not None else
                        f"'{window_title}' penceresi" if window_title is not None else
                        f"{gone} öğesinin kaybolması")
                raise snap.ActionTimeout(f"{what} {timeout:g} sn içinde gerçekleşmedi")
            time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
            delay = min(delay * 1.5, 0.5)

    # ------------------------------------------------- görünür fare imleci
    def cursor(self, x: int, y: int, click: bool = False,
               button: int = 1, visible: bool = True) -> bool:
        """AJANIN KENDİ imlecini (x,y)'ye götür; istenirse tık halkası göster.

        Kullanıcının faresine DOKUNMAZ: imleç boyutunda, her şeyin üstünde,
        tıklamaları arkaya geçiren küçük bir penceredir (Linux: X11/XWayland
        override-redirect, Windows: katmanlı WS_EX_TRANSPARENT, macOS:
        ignoresMouseEvents NSWindow). Gerçek tıklamayı click/press yapar.
        """
        ac = self._cursor_daemon()
        if visible:
            ac.send("SHOW")
        ac.send(f"MOVE {int(x)} {int(y)}")   # yanıt hareket bitince gelir
        if click:
            ac.send("CLICK")
        return True

    def cursor_move(self, x: int, y: int, steps: int = 24,
                    duration: float = 0.5) -> bool:
        """Ajan imlecini yumuşak hareketle (x,y)'ye taşı (duration sn)."""
        return self._cursor_daemon().send(f"MOVE {int(x)} {int(y)} {duration}") == "OK"

    def cursor_hide(self) -> bool:
        from . import agent_cursor as ac
        if ac.daemon_running():
            ac.send("HIDE")
            return True
        return False

    def pointer_available(self) -> tuple[bool, str]:
        """Ajan imleci kullanılabilir mi? (Linux X11/XWayland, Windows, macOS)"""
        from . import agent_cursor as ac
        return ac.available()

    # ---------------------------------------------------------------- TUI
    def run(self, command: str, session: str = "default",
            timeout: float = 30.0) -> CommandResult:
        return self._tui().run(command, session, timeout)

    def read_screen(self, session: str = "default") -> str:
        return self._tui().read_screen(session)

    def send_keys(self, keys: str, session: str = "default") -> bool:
        return self._tui().send_keys(keys, session)

    def sessions(self) -> list[str]:
        try:
            return self._tui().list_sessions()
        except Exception:
            return []

    def cleanup(self) -> list[str]:
        """Kendi açtığı TUI oturumlarını kapat (tmux). Kullanıcınınkine dokunmaz."""
        b = self.backends.get("tui")
        if b is not None and hasattr(b, "cleanup"):
            try:
                return b.cleanup()
            except Exception:
                return []
        return []

    def close(self) -> None:
        for b in (self._backends or {}).values():
            try:
                b.close()
            except Exception:
                pass
        closer = getattr(self.desktop, "close", None)
        if closer is not None and self.long_lived:
            try:
                closer()
            except Exception:
                pass
