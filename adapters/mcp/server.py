"""NexRemote MCP adapter server.

Exposes the NexRemote facade as MCP tools over stdio so any MCP-capable
LLM client (Claude Desktop, Claude Code, custom harnesses) can see and
control the local desktop: GUI (AT-SPI accessibility tree) and terminal
(TUI/tmux) sessions.

Start:  python3 adapters/mcp/server.py
Transport: stdio (JSON-RPC on stdin/stdout, logs on stderr).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Make `import nexremote` work regardless of the current working directory.
_CORE = Path(__file__).resolve().parents[2] / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

_nr: Any = None


def _get_nexremote():
    """Lazy singleton for the NexRemote facade.

    Built on first tool call so importing this module stays cheap and
    never fails just because a backend dependency is missing.
    """
    global _nr
    if _nr is None:
        from nexremote import NexRemote
        try:
            _nr = NexRemote()
        except Exception:
            # Facade default construction can fail if a backend module is
            # missing; fall back to whatever backends import cleanly.
            backends: list[Any] = []
            try:
                from nexremote.backends.gui import GuiBackend
                backends.append(GuiBackend())
            except Exception:
                pass
            try:
                from nexremote.backends.tui import TuiBackend
                backends.append(TuiBackend())
            except Exception:
                pass
            _nr = NexRemote(backends=backends)
    return _nr


def _tool_errors(fn):
    """Convert any unexpected exception into a clean ToolError message.

    Tracebacks are never sent to the model; only a short, readable reason.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"{fn.__name__} failed: {type(exc).__name__}: {exc}") from None

    return wrapper


mcp = MCPServer(
    name="nexremote",
    instructions=(
        "NexRemote desktop and terminal control. "
        "Discover what is on screen with nexremote_windows / nexremote_tree / "
        "nexremote_find, then act with nexremote_click / nexremote_set_text / "
        "nexremote_key / nexremote_type. Terminal sessions are driven with "
        "nexremote_run / nexremote_read_screen / nexremote_send_keys / "
        "nexremote_sessions. Element references are opaque strings returned "
        "by tree/find/windows; pass them back unchanged."
    ),
)


# ------------------------------------------------------------------ GUI tools
@mcp.tool()
@_tool_errors
def nexremote_capabilities() -> dict:
    """Report which backends (GUI, TUI) are available and what they can do.

    Returns a dict with per-backend availability, human-readable detail, and
    missing system dependencies. Call this first to know what is possible.
    """
    return _get_nexremote().capabilities()


@mcp.tool()
@_tool_errors
def nexremote_windows() -> list[dict]:
    """List top-level desktop windows (dialogs and frames) with opaque refs.

    Each entry has ref, title, app, pid, focused and bounds. Use the ref with
    nexremote_tree or nexremote_find to inspect a window's contents.
    """
    return [w.to_dict() for w in _get_nexremote().windows()]


@mcp.tool()
@_tool_errors
def nexremote_tree(window_ref: str = "") -> str:
    """Dump the accessibility tree of a window as indented readable text.

    Each line shows [role] name and available actions like <click,set_text>.
    Pass window_ref from nexremote_windows; empty string uses the first window.
    This is the primary way to "see" the GUI structure before acting.
    """
    return _get_nexremote().tree(window_ref or None)


@mcp.tool()
@_tool_errors
def nexremote_find(query: str, window_ref: str = "") -> list[dict]:
    """Search the accessibility tree for elements whose name or value matches.

    Case-insensitive substring match. Returns interactive elements with their
    refs; pass a ref to nexremote_click or nexremote_set_text. window_ref
    restricts the search to one window; empty string searches all windows.
    """
    return [e.to_dict() for e in _get_nexremote().find(query, window_ref or None)]


@mcp.tool()
@_tool_errors
def nexremote_click(ref: str) -> bool:
    """Click (activate) the UI element referenced by ref.

    Uses the element's native action when available, otherwise a physical
    mouse click at its center. Returns True on success, False if the element
    could not be resolved or activated.
    """
    return bool(_get_nexremote().click(ref))


@mcp.tool()
@_tool_errors
def nexremote_set_text(ref: str, text: str) -> bool:
    """Replace the text of an editable element (entry, text field, combo box).

    Uses the element's accessible set-text interface when available, otherwise
    focuses the element and types the text. Returns True on success.
    """
    return bool(_get_nexremote().set_text(ref, text))


@mcp.tool()
@_tool_errors
def nexremote_key(combo: str) -> bool:
    """Send a key combination to the currently focused element/window.

    Examples: 'ctrl+c', 'alt+F4', 'Return', 'ctrl+shift+t'. Modifier names are
    case-insensitive. Returns True if the key event was delivered.
    """
    return bool(_get_nexremote().key(combo))


@mcp.tool()
@_tool_errors
def nexremote_type(text: str) -> bool:
    """Type literal text into the currently focused element/window.

    Does not interpret special keys; for shortcuts use nexremote_key instead.
    Returns True if the text was delivered.
    """
    return bool(_get_nexremote().type_text(text))


@mcp.tool()
@_tool_errors
def nexremote_screenshot(path: str = "") -> dict:
    """Capture a screenshot of the current desktop to a PNG file.

    path is the output file; empty string uses /tmp/nexremote-shot.png. Returns
    a dict with path, width, height, display and the tool used. Note: this
    harness is text-only; the file is metadata for the user, not for the model.
    """
    return _get_nexremote().screenshot(path or None).to_dict()


# ------------------------------------------------------------------ TUI tools
@mcp.tool()
@_tool_errors
def nexremote_run(command: str, session: str = "default", timeout: float = 30.0) -> dict:
    """Run a shell command in a persistent terminal session and return its result.

    The session (default "default") keeps shell state between calls. Returns a
    dict with output, exit_code, session and timed_out. timeout is in seconds
    (default 30); on timeout the command keeps running in the session.
    """
    return _get_nexremote().run(command, session, timeout).to_dict()


@mcp.tool()
@_tool_errors
def nexremote_read_screen(session: str = "default") -> str:
    """Read the current visible screen content of a terminal session as text.

    Returns ANSI-stripped screen text: what a user would see in the terminal
    right now, including prompts and partial output. Useful to observe
    interactive programs before sending keys.
    """
    return _get_nexremote().read_screen(session)


@mcp.tool()
@_tool_errors
def nexremote_send_keys(keys: str, session: str = "default") -> bool:
    """Send raw keystrokes to a terminal session (tmux send-keys syntax).

    Special keys use tmux names: 'Enter', 'Escape', 'C-c' (ctrl+c), 'Up', or a
    plain literal string. Use after nexremote_read_screen to drive interactive
    programs. Returns True if the keys were delivered.
    """
    return bool(_get_nexremote().send_keys(keys, session))


@mcp.tool()
@_tool_errors
def nexremote_sessions() -> list[str]:
    """List the names of all live terminal sessions managed by NexRemote.

    Returns an empty list when no session exists yet. Sessions are created on
    first nexremote_run / nexremote_read_screen call.
    """
    return [str(s) for s in _get_nexremote().sessions()]


if __name__ == "__main__":
    mcp.run(transport="stdio")
