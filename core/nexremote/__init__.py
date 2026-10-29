"""NexRemote — yapay zekâ ajanlarına uzaktan görme + kontrol yeteneği.

Evrensel köprü: her harness'a (MCP'li/MCP'siz), her modele (tool-calling
olan/olmayan), GUI ve terminal (TUI) ortamlarına takılır.
"""
from .models import (
    Action, Capability, CommandResult, Element, Role, Screenshot, Window,
)
from .nexremote import NexRemote

__version__ = "0.5.0"
__all__ = [
    "NexRemote", "Element", "Window", "Screenshot", "CommandResult",
    "Capability", "Role", "Action", "__version__",
]
