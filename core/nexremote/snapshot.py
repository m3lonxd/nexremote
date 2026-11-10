"""Anlık görüntü (snapshot) ref'leri ve katı yeniden tanıma.

Fikir agent-desktop'tan (vercel-labs/agent-desktop, Apache-2.0) uyarlandı;
kod bu proje için yeniden yazıldı:

* Bir pencerenin ağacı okunduğunda her eyleme açık öğeye kısa bir ref verilir
  ve ref, okumanın kimliğiyle nitelenir: ``@s7k2m9qx:e5``. Çıplak ``e5`` kabul
  edilmez — hangi gözleme ait olduğu bilinmeyen ref'le eylem yapılmaz.
* Ref'in arkasında konum yolu değil **kimlik kanıtı** saklanır: pencerenin
  süreci + başlığı, öğenin rolü, kararlı adı ve adlı ataları. Eylemden önce
  canlı ağaç yeniden okunur ve öğe bu kanıtla aranır.
* Eşleşme yoksa ``STALE_REF``, birden fazlaysa ``AMBIGUOUS_TARGET`` — tahmin
  yok. Eski AT-SPI yol ref'leri (``atspi:0:1/3/2``) uygulama açılıp kapanınca
  ya da liste değişince sessizce BAŞKA öğeyi gösteriyordu; bu modül o hatayı
  kapatır.
* Eylem öncesi otomatik bekleme: öğe bulunup etkin olana kadar sınırlı süre
  yoklanır (varsayılan 5 sn), olmazsa ``TIMEOUT``.
* Hızlı yol (v0.7): arka uç ``probe`` sunuyorsa önce yalnızca ref'in yolundaki
  düğüm ve ataları okunur; rol, kararlı ad, adlı atalar ve pencere (süreç +
  başlık) saklı kanıtla birebir tutarsa öğe odur — ağacın tamamı okunmaz.
  Tutmazsa ya da öğenin eşi varsa tam (hafif) okumaya düşülür. Bu yol, ağacın
  BAŞKA bir yerinde sonradan ortaya çıkan ikizi fark etmez; ``NEXREMOTE_STRICT=1``
  her eylemde tam okumayı zorlar.
"""
from __future__ import annotations

import json
import os
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from . import trace
from .models import Element, Role, Window
from .platforms.base import runtime_dir

INTERACTIVE = {Role.BUTTON, Role.ENTRY, Role.CHECKBOX, Role.RADIO,
               Role.COMBOBOX, Role.MENU_ITEM, Role.TAB, Role.LINK,
               Role.LIST_ITEM, Role.TREE_ITEM, Role.SLIDER}
# Adı değeriyle değişebilen roller: içerik kimlik sayılmaz.
VOLATILE_NAME = {Role.ENTRY, Role.TEXT, Role.COMBOBOX, Role.SLIDER, Role.TERMINAL}
# Tek başına "eyleme açık" saymayan eylemler (web her düğüme focus verir).
_PASSIVE_ACTIONS = {"focus", "scroll"}
_ANCESTORS = 3
_KEEP = 256                     # diskte tutulan en fazla anlık görüntü
_ALPHABET = string.ascii_lowercase + string.digits
DEFAULT_WAIT = 5.0


class RefError(RuntimeError):
    code = "REF_ERROR"

    def __init__(self, message: str, **details) -> None:
        super().__init__(message)
        self.details = details

    def to_dict(self) -> dict:
        return {"code": self.code, "message": str(self), **self.details}


class InvalidRef(RefError):
    code = "INVALID_REF"


class StaleRef(RefError):
    code = "STALE_REF"


class AmbiguousTarget(RefError):
    code = "AMBIGUOUS_TARGET"


class ActionTimeout(RefError):
    code = "TIMEOUT"


def is_snapshot_ref(ref: str) -> bool:
    """Anlık görüntü ref'i (ya da reddedilecek çıplak eN) mi? Arka uç ref'leri
    (atspi:/uia:/ax:/win:) her zaman şema önekiyle başlar."""
    if not ref:
        return False
    bare = ref.lstrip("@")
    return (ref.startswith("@") or _split(ref) is not None
            or (bare[:1] == "e" and bare[1:].isdigit()))


def _split(ref: str) -> Optional[tuple[str, str]]:
    body = ref[1:] if ref.startswith("@") else ref
    sid, sep, eid = body.partition(":")
    if (sep and len(sid) == 9 and sid[0] == "s" and eid[:1] == "e"
            and eid[1:].isdigit() and all(c in _ALPHABET for c in sid)):
        return sid, eid
    return None


def parse_ref(ref: str) -> tuple[str, str]:
    """``@sXXXXXXXX:eN`` → (sid, eid). Çıplak ``eN`` / ``@eN`` reddedilir."""
    parts = _split(ref or "")
    if parts is None:
        if (ref or "").lstrip("@")[:1] == "e" and ref.lstrip("@")[1:].isdigit():
            raise InvalidRef(f"'{ref}' hangi anlık görüntüye ait belli değil — "
                             "`tree` çıktısındaki tam ref'i kullanın (@s…:eN)")
        raise InvalidRef(f"geçersiz anlık görüntü ref'i: {ref!r}")
    return parts


def actionable(e: Element) -> bool:
    """Eyleme açık mı? İsimsiz web kabı (her <div>'e 'click' verilir) sayılmaz."""
    if e.is_wrapper():
        return False
    if set(e.actions) - _PASSIVE_ACTIONS:
        return True
    return e.role in INTERACTIVE


def stable_name(e: Element) -> str:
    name = (e.name or "").strip()
    if e.role in VOLATILE_NAME and (not name or name == (e.value or "").strip()):
        return ""
    return name


def _walk(root: Element) -> Iterator[tuple[Element, tuple]]:
    """Belge sırasıyla (öğe, adlı-atalar) üretir. Kök dahil değildir: kök
    pencerenin kendisidir ve kimliği ayrı tutulur."""
    stack: list[tuple[Element, tuple]] = [(c, ()) for c in reversed(root.children)]
    while stack:
        node, anc = stack.pop()
        yield node, anc
        nm = stable_name(node)
        child_anc = (anc + ((node.role.value, nm),))[-_ANCESTORS:] if nm else anc
        for c in reversed(node.children):
            stack.append((c, child_anc))


def _key(e: Element, anc: tuple) -> tuple:
    return (e.role.value, stable_name(e), anc)


def _index(root: Element) -> tuple[list[tuple[Element, tuple]], dict[tuple, list[Element]]]:
    order: list[tuple[Element, tuple]] = []
    groups: dict[tuple, list[Element]] = {}
    for node, anc in _walk(root):
        if not actionable(node):
            continue
        k = _key(node, anc)
        order.append((node, k))
        groups.setdefault(k, []).append(node)
    return order, groups


@dataclass
class WindowIdentity:
    ref: str
    pid: Optional[int] = None
    title: str = ""
    app: str = ""

    def to_dict(self) -> dict:
        return {"ref": self.ref, "pid": self.pid, "title": self.title, "app": self.app}


@dataclass
class Snapshot:
    sid: str
    window: WindowIdentity
    entries: dict[str, dict]
    root: Element
    created: float = field(default_factory=time.time)
    refs: dict[str, str] = field(default_factory=dict)   # backend ref → @sid:eN

    def render(self, interactive: bool = False, subtree: Optional[Element] = None) -> str:
        node = subtree or self.root
        head = f"# anlık görüntü @{self.sid} — {self.window.title or self.window.app or self.window.ref}"
        return head + "\n" + node.render(refs=self.refs, interactive=interactive)


def store_dir() -> str:
    d = os.environ.get("NEXREMOTE_HOME")
    d = os.path.join(d, "snapshots") if d else os.path.join(runtime_dir(), "nexremote-snapshots")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _new_sid() -> str:
    return "s" + "".join(secrets.choice(_ALPHABET) for _ in range(8))


def _prune(d: str) -> None:
    try:
        files = sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")),
                       key=os.path.getmtime)
    except OSError:
        return
    for f in files[:-_KEEP]:
        try:
            os.remove(f)
        except OSError:
            pass


def window_identity(root: Element, windows: list[Window]) -> WindowIdentity:
    w = next((w for w in windows if w.ref == root.ref), None)
    if w is None:
        return WindowIdentity(ref=root.ref, title=root.name)
    return WindowIdentity(ref=w.ref, pid=w.pid, title=w.title, app=w.app)


def take(root: Element, windows: list[Window]) -> Snapshot:
    """Okunmuş bir pencere ağacından anlık görüntü üret ve diske yaz."""
    sid = _new_sid()
    ident = window_identity(root, windows)
    order, groups = _index(root)
    entries: dict[str, dict] = {}
    refs: dict[str, str] = {}
    seen: dict[tuple, int] = {}
    for n, (node, k) in enumerate(order, 1):
        eid = f"e{n}"
        dup = seen.get(k, 0)
        seen[k] = dup + 1
        entries[eid] = {
            "backend_ref": node.ref,
            "role": k[0], "name": k[1], "ancestors": [list(a) for a in k[2]],
            "dup_index": dup, "dup_count": len(groups[k]),
            "bounds": list(node.bounds) if node.bounds else None,
            "value": (node.value or "")[:60],        # kimlik değil: `after` farkı için
        }
        refs[node.ref] = f"@{sid}:{eid}"
    snap = Snapshot(sid=sid, window=ident, entries=entries, root=root, refs=refs)
    d = store_dir()
    tmp = os.path.join(d, f".{sid}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"sid": sid, "created": snap.created, "window": ident.to_dict(),
                   "entries": entries}, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(d, f"{sid}.json"))
    _prune(d)
    return snap


def load(sid: str) -> dict:
    path = os.path.join(store_dir(), f"{sid}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise StaleRef(f"anlık görüntü @{sid} bulunamadı (süresi geçmiş olabilir) — "
                       "yeniden `tree` alın", snapshot=sid) from None


def find_window(ident: dict, windows: list[Window]) -> Window:
    """Saklı pencere kimliğini canlı pencereyle eşleştir. Ref tek başına
    yetmez (AT-SPI uygulama indeksi kayar): süreç kimliği doğrulamalıdır."""
    pid, title, ref = ident.get("pid"), ident.get("title") or "", ident.get("ref")
    if pid is None:
        same = [w for w in windows if w.ref == ref]
        if len(same) == 1:
            return same[0]
        raise StaleRef("pencere kimliği doğrulanamıyor (süreç bilgisi yok) — "
                       "yeniden `tree` alın", window=ref)
    cands = [w for w in windows if w.pid == pid]
    if not cands:
        raise StaleRef(f"'{title}' penceresi kapanmış — yeniden `tree` alın", window=ref)
    for pick in ([w for w in cands if w.title == title and w.ref == ref],
                 [w for w in cands if w.title == title],
                 cands if len(cands) == 1 else []):
        if len(pick) == 1:
            return pick[0]
    raise AmbiguousTarget(f"süreç {pid} birden çok pencere açmış, '{title}' tekil "
                          "seçilemiyor — yeniden `tree --window` alın",
                          candidates=[w.ref for w in cands])


def resolve_in(entry: dict, root: Element, sid: str, eid: str) -> Element:
    """Kimlik kanıtıyla canlı ağaçta TEK öğe bul; yoksa/çoksa reddet."""
    key = (entry["role"], entry["name"], tuple(tuple(a) for a in entry["ancestors"]))
    _, groups = _index(root)
    matches = groups.get(key, [])
    ref = f"@{sid}:{eid}"
    label = f"[{entry['role']}] {entry['name']}".strip()
    if not matches:
        raise StaleRef(f"{ref} {label} artık ekranda yok — yeniden `tree` alın", ref=ref)
    if len(matches) == entry["dup_count"]:
        return matches[entry["dup_index"]]
    if entry["dup_count"] == 1 and len(matches) > 1:
        raise AmbiguousTarget(f"{ref} {label} artık {len(matches)} öğeyle eşleşiyor — "
                              "yeniden `tree` alın", ref=ref, matches=len(matches))
    raise StaleRef(f"{ref} {label}: eşi olan öğelerin sayısı değişti "
                   f"({entry['dup_count']}→{len(matches)}), hangisi olduğu kanıtlanamıyor "
                   "— yeniden `tree` alın", ref=ref)


def strict() -> bool:
    return os.environ.get("NEXREMOTE_STRICT", "") not in ("", "0")


def _fast(entry: dict, ident: dict, probe: Callable[[str], Optional[dict]]) -> Optional[Element]:
    """Hızlı yol: saklı yoldaki düğüm kanıtla birebir tutuyorsa onu döndür."""
    if entry.get("dup_count") != 1 or ident.get("pid") is None:
        return None
    p = probe(entry["backend_ref"])
    if not p or p.get("pid") != ident["pid"] or (p.get("title") or "") != (ident.get("title") or ""):
        return None
    el = p["element"]
    want = (entry["role"], entry["name"], tuple(tuple(a) for a in entry["ancestors"]))
    got = (el.role.value, stable_name(el), tuple(tuple(a) for a in p["ancestors"]))
    if want != got or not actionable(el):
        return None
    return el


def resolve(ref: str, read_tree: Callable[[str], Element],
            list_windows: Callable[[], list[Window]],
            wait: float = DEFAULT_WAIT, need_enabled: bool = True,
            probe: Optional[Callable[[str], Optional[dict]]] = None) -> Element:
    """``@sid:eN`` → canlı öğe. Öğe devre dışıysa ``wait`` saniye etkinleşmesi
    beklenir. Bayat/belirsiz ref beklenmez: o bir gözlem hatasıdır, zaman değil.
    probe: arka ucun yol + kimlik hızlı yolu (bkz. modül açıklaması)."""
    sid, eid = parse_ref(ref)
    data = load(sid)
    entry = data["entries"].get(eid)
    if entry is None:
        raise InvalidRef(f"@{sid} içinde {eid} yok", ref=ref)
    use_fast = probe is not None and not strict()
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        with trace.span("çözümleme", ref=ref) as info:
            el = _fast(entry, data["window"], probe) if use_fast else None
            info["yol"] = "hızlı" if el is not None else "tam"
            if el is None:
                win = find_window(data["window"], list_windows())
                el = resolve_in(entry, read_tree(win.ref), sid, eid)
        if el.enabled or not need_enabled:
            return el
        if time.monotonic() >= deadline:
            raise ActionTimeout(f"@{sid}:{eid} [{entry['role']}] {entry['name']} "
                                f"{wait:g} sn içinde etkinleşmedi", ref=ref)
        time.sleep(0.15)


# ------------------------------------------------------------ eylem sonrası fark
def _ident_match(a: dict, b: dict) -> bool:
    if a.get("pid") is not None and b.get("pid") is not None:
        return a["pid"] == b["pid"] and (a.get("title") or "") == (b.get("title") or "")
    return a.get("ref") == b.get("ref")


def previous(ident: dict, exclude: str = "", within: float = 3600.0) -> Optional[dict]:
    """Aynı pencerenin `exclude` dışındaki en yeni anlık görüntüsü (yoksa None)."""
    d = store_dir()
    try:
        files = sorted((os.path.join(d, f) for f in os.listdir(d)
                        if f.endswith(".json") and f != f"{exclude}.json"),
                       key=os.path.getmtime, reverse=True)
    except OSError:
        return None
    now = time.time()
    for f in files[:64]:
        try:
            if now - os.path.getmtime(f) > within:
                break
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if _ident_match(data.get("window") or {}, ident):
            return data
    return None


def _entry_key(e: dict) -> tuple:
    return (e["role"], e["name"], tuple(tuple(a) for a in e["ancestors"]), e["dup_index"])


def diff(old: dict, new: Snapshot, limit: int = 40) -> list[str]:
    """Önceki anlık görüntüye göre değişen eyleme açık öğeler (+ eklendi, - gitti,
    ~ değeri değişti). Ref'ler yeni anlık görüntünündür."""
    olds = {_entry_key(e): e for e in old.get("entries", {}).values()}
    news = {_entry_key(e): (eid, e) for eid, e in new.entries.items()}
    lines: list[str] = []

    def label(e: dict) -> str:
        return f"[{e['role']}] {e['name']}".rstrip()

    for k, (eid, e) in news.items():
        if k not in olds:
            lines.append(f"+ {label(e)}  ref=@{new.sid}:{eid}")
        elif (olds[k].get("value") or "") != (e.get("value") or ""):
            lines.append(f"~ {label(e)}: {olds[k].get('value') or ''!r} → "
                         f"{e.get('value') or ''!r}  ref=@{new.sid}:{eid}")
    for k, e in olds.items():
        if k not in news:
            lines.append(f"- {label(e)}")
    if len(lines) > limit:
        lines = lines[:limit] + [f"… {len(lines) - limit} değişiklik daha (tam liste: tree -i)"]
    return lines
