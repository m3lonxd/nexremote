"""Backend soyut arayüzü (ABC).

Her backend (gui, tui, ...) bu sınıfı uygular. Ana NexRemote sınıfı
backend'leri bu arayüz üzerinden kullanır — polimorfizm burada.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .models import Capability, CommandResult, Element, Screenshot, Window


class Backend(ABC):
    """Bir yetenek alanı: GUI, TUI, vb."""

    name: str = "base"
    kind: str = "generic"  # "gui" | "tui"

    @abstractmethod
    def capability(self) -> Capability:
        """Bu backend kullanılabilir mi? Ne gerekiyor?"""

    # --- GUI yöntemleri (TUI backend'leri NotImplemented bırakır) ---
    def windows(self) -> list[Window]:
        raise NotImplementedError

    def tree(self, window_ref: str) -> Element:
        raise NotImplementedError

    def find(self, query: str, window_ref: Optional[str] = None) -> list[Element]:
        raise NotImplementedError

    def click(self, ref: str) -> bool:
        raise NotImplementedError

    def set_text(self, ref: str, text: str) -> bool:
        raise NotImplementedError

    def key(self, combo: str) -> bool:
        raise NotImplementedError

    def type_text(self, text: str) -> bool:
        raise NotImplementedError

    def screenshot(self, path: str) -> Screenshot:
        raise NotImplementedError

    def focus(self, ref: str) -> bool:
        raise NotImplementedError

    def bounds(self, ref: str) -> Optional[tuple[int, int, int, int]]:
        """Öğenin ekran dikdörtgeni (x, y, w, h) — platformun ekran koordinatında."""
        raise NotImplementedError

    # --- TUI yöntemleri ---
    def run(self, command: str, session: str = "default",
            timeout: float = 30.0) -> CommandResult:
        raise NotImplementedError

    def read_screen(self, session: str = "default") -> str:
        raise NotImplementedError

    def send_keys(self, keys: str, session: str = "default") -> bool:
        raise NotImplementedError

    def list_sessions(self) -> list[str]:
        raise NotImplementedError

    def close(self) -> None:
        """Kaynakları serbest bırak."""
        return None
