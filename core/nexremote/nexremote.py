"""NexRemote — ana facade.

Tüm backend'leri toplar, tek bir birleşik API sunar. Adaptörler (MCP/CLI/HTTP)
bu sınıfı sarar. Hiçbir adaptör backend detayını bilmez.
"""
from __future__ import annotations

from typing import Any, Optional

from .base import Backend
from .models import Capability, CommandResult, Element, Screenshot, Window


class NexRemote:
    def __init__(self, backends: Optional[list[Backend]] = None):
        if backends is None:
            backends = self._default_backends()
        self.backends: dict[str, Backend] = {b.name: b for b in backends}

    @staticmethod
    def _default_backends() -> list[Backend]:
        """Var olan tüm backends'leri yükle. Bir backend eksik/bozuksa
        diğerlerini engellemesin — kısmi ortamlarda da çalışmalı."""
        import importlib
        out: list[Backend] = []
        for mod_name, cls_name in (("gui", "GuiBackend"), ("tui", "TuiBackend")):
            try:
                mod = importlib.import_module(f".backends.{mod_name}", __package__)
                out.append(getattr(mod, cls_name)())
            except Exception:
                pass
        return out

    def backend(self, name: str) -> Backend:
        if name not in self.backends:
            raise KeyError(f"backend yok: {name}. Mevcut: {list(self.backends)}")
        return self.backends[name]

    def _gui(self) -> Backend:
        return self.backend("gui")

    def _tui(self) -> Backend:
        return self.backend("tui")

    # ---------------------------------------------------------------- keşif
    def capabilities(self) -> dict[str, Any]:
        """Ne yapabildiğimi anlat — metin-tabanlı modeller bunu okur."""
        caps = {name: b.capability() for name, b in self.backends.items()}
        return {
            "backends": {
                n: {"available": c.available, "detail": c.detail, "needs": c.needs}
                for n, c in caps.items()
            },
            "actions": [
                "windows", "tree", "find", "click", "set_text",
                "key", "type_text", "screenshot", "focus",
                "run", "read_screen", "send_keys", "sessions",
            ],
        }

    # ---------------------------------------------------------------- GUI
    def windows(self) -> list[Window]:
        return self._gui().windows()

    def tree(self, window_ref: Optional[str] = None) -> str:
        """Pencere ağacını LLM dostu metin olarak döndür."""
        w = self._gui().tree(window_ref) if window_ref else self._gui().tree("")
        return w.render()

    def find(self, query: str, window_ref: Optional[str] = None) -> list[Element]:
        return self._gui().find(query, window_ref)

    def click(self, ref: str) -> bool:
        return self._gui().click(ref)

    def set_text(self, ref: str, text: str) -> bool:
        return self._gui().set_text(ref, text)

    def key(self, combo: str) -> bool:
        return self._gui().key(combo)

    def type_text(self, text: str) -> bool:
        return self._gui().type_text(text)

    def screenshot(self, path: Optional[str] = None) -> Screenshot:
        return self._gui().screenshot(path or "/tmp/nexremote-shot.png")

    def focus(self, ref: str) -> bool:
        return self._gui().focus(ref)

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

    def close(self) -> None:
        for b in self.backends.values():
            try:
                b.close()
            except Exception:
                pass
