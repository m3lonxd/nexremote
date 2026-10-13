"""NexRemote çekirdek veri modelleri.

Bu modüller tüm backend ve adaptörlerin ortak dilidir (contract).
Backend'ler bunu doldurur, adaptörler bunu sunar.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Role(str, Enum):
    """Erişilebilirlik ağacındaki öğe rolü (küçük harf, backend-bağımsız)."""
    FRAME = "frame"
    WINDOW = "window"
    BUTTON = "button"
    ENTRY = "entry"
    TEXT = "text"
    MENU = "menu"
    MENU_ITEM = "menu_item"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    COMBOBOX = "combobox"
    TAB = "tab"
    LIST = "list"
    LIST_ITEM = "list_item"
    TREE = "tree"
    TREE_ITEM = "tree_item"
    LINK = "link"
    IMAGE = "image"
    SLIDER = "slider"
    PANEL = "panel"
    DIALOG = "dialog"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


# Eylem adları (backend-bağımsız normalize edilmiş)
class Action(str, Enum):
    CLICK = "click"
    SET_TEXT = "set_text"
    FOCUS = "focus"
    TOGGLE = "toggle"
    SELECT = "select"
    EXPAND = "expand"
    SCROLL = "scroll"
    KEY = "key"          # ham tuş gönder (örn. "ctrl+c")
    TYPE = "type"        # metin yaz


@dataclass
class Capability:
    """Bir backend'in ne yapabildiğini anlatır.

    Metin-tabanlı modeller bunu okuyup 'ne yapabilirim?' sorusunu yanıtlar.
    """
    name: str
    available: bool
    detail: str = ""
    needs: list[str] = field(default_factory=list)  # eksik bağımlılıklar


@dataclass
class Element:
    """Erişilebilirlik ağacındaki tek bir düğüm — AI'nın 'gördüğü' şey."""
    ref: str                      # opak referans (backend'e özgü, örn "atspi:0:1/3/2")
    role: Role = Role.UNKNOWN
    name: str = ""
    value: str = ""               # entry/checkbox için mevcut değer
    actions: list[str] = field(default_factory=list)
    bounds: Optional[tuple[int, int, int, int]] = None  # x,y,w,h
    focused: bool = False
    enabled: bool = True
    depth: int = 0
    children: list["Element"] = field(default_factory=list)

    def to_dict(self, with_children: bool = True) -> dict[str, Any]:
        d = {
            "ref": self.ref,
            "role": self.role.value,
            "name": self.name,
        }
        if self.value:
            d["value"] = self.value
        if self.actions:
            d["actions"] = self.actions
        if self.bounds:
            d["bounds"] = list(self.bounds)
        if self.focused:
            d["focused"] = True
        if not self.enabled:
            d["enabled"] = False
        if with_children and self.children:
            d["children"] = [c.to_dict(True) for c in self.children]
        return d

    def render(self, indent: int = 0, max_name: int = 60) -> str:
        """LLM dostu metin satırı: `  [button] Kaydet  <click>`"""
        pad = "  " * indent
        nm = self.name[:max_name].replace("\n", " ")
        extra = ""
        if self.actions:
            extra = f"  <{','.join(self.actions)}>"
        if self.value:
            extra += f"  = {self.value[:40]!r}"
        if self.focused:
            extra += "  (*)"
        line = f"{pad}[{self.role.value}] {nm}{extra}"
        out = [line]
        for c in self.children:
            out.append(c.render(indent + 1, max_name))
        return "\n".join(out)


@dataclass
class Window:
    """Üst düzey pencere."""
    ref: str
    title: str = ""
    app: str = ""
    pid: Optional[int] = None
    focused: bool = False
    bounds: Optional[tuple[int, int, int, int]] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", False)}


@dataclass
class Screenshot:
    """Ekran görüntüsü sonucu."""
    path: str
    width: int
    height: int
    display: Optional[str] = None
    taken_at: float = field(default_factory=time.time)
    backend: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CommandResult:
    """TUI/terminal komut sonucu."""
    output: str
    exit_code: int = 0
    session: str = ""
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
