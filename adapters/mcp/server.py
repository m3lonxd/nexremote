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
        _nr = NexRemote()
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
        "NexRemote: see and control this Linux desktop and its terminals. "
        "Start with nexremote_observe (windows + open terminals). "
        "Prefer structure over pixels: nexremote_find / nexremote_tree give "
        "element refs; nexremote_click(ref) presses them without moving the "
        "user's mouse. When an app exposes no tree, call nexremote_screenshot, "
        "then nexremote_click_at(x, y, from_screenshot=True) with pixel "
        "coordinates read from that image. Terminals already open on the "
        "desktop (other CLI agents, ssh, builds) are read with "
        "nexremote_terminal_read and driven with nexremote_terminal_send. "
        "Refs are opaque strings; pass them back unchanged."
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
def nexremote_observe() -> dict:
    """One-shot snapshot: every window on screen and every open terminal.

    windows: ref, title, app, pid, focused, bounds (x, y, w, h in screen
    pixels). terminals: ref, program (foreground process), cwd, title.
    Call this first, and again after acting to verify the result.
    """
    nr = _get_nexremote()
    return {"windows": [w.to_dict() for w in nr.windows()],
            "terminals": nr.terminals()}


@mcp.tool()
@_tool_errors
def nexremote_windows() -> list[dict]:
    """List every window on screen (KWin) with refs like win:{...}.

    Each entry has ref, title, app, pid, focused and bounds. Pass the ref to
    nexremote_window_action, nexremote_tree, nexremote_find or
    nexremote_screenshot(window_ref=...).
    """
    return [w.to_dict() for w in _get_nexremote().windows()]


@mcp.tool()
@_tool_errors
def nexremote_window_action(ref: str, action: str) -> bool:
    """Act on a window: action is 'activate' (raise + focus), 'minimize' or 'close'.

    Activate a window before typing into it. Returns False if the window no
    longer exists.
    """
    return bool(_get_nexremote().window_action(ref, action))


@mcp.tool()
@_tool_errors
def nexremote_launch(argv: list[str], wait: float = 15.0) -> dict:
    """Start a program with accessibility switched on and return its new window.

    argv is the program and its arguments, e.g. ["vesktop"] or ["code", "."].
    Electron/Chromium apps (Discord, VS Code, Slack, Chrome) only expose their
    buttons when started with --force-renderer-accessibility; this adds it.
    Returns {"window": "win:{...}" or null, "electron": bool, "note": str}.
    A single-instance app that is already running ignores the flag: close it
    fully first. Never close the user's apps without asking.
    """
    return _get_nexremote().launch(list(argv), wait)


@mcp.tool()
@_tool_errors
def nexremote_tree(window_ref: str = "") -> str:
    """Dump the accessibility tree of a window as indented readable text.

    Each line shows [role] name, actions like <click,set_text> and, for
    actionable elements, ref=... that nexremote_click accepts directly.
    Unnamed layout containers are folded away and hidden elements (closed
    menus) are left out. Pass window_ref from nexremote_windows; empty string
    uses the first window. An empty Electron tree ends with a hint line.
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
    """Press the UI element referenced by ref (from nexremote_find / nexremote_tree).

    Uses the element's accessible action, so the user's mouse does not move.
    Returns False if the element could not be resolved or activated.
    """
    return bool(_get_nexremote().click(ref))


@mcp.tool()
@_tool_errors
def nexremote_click_at(x: float, y: float, button: str = "left", double: bool = False,
                       from_screenshot: bool = True) -> dict:
    """Real mouse click at a point. button: left | right | middle.

    With from_screenshot=True (default) x, y are pixel coordinates in the
    image returned by the last nexremote_screenshot call (cropping and scaling
    are converted for you). With False they are absolute screen pixels.
    The user's pointer is put back where it was right after the click.
    """
    nr = _get_nexremote()
    sx, sy = nr.screen_point(x, y, from_screenshot)
    nr.click_at(sx, sy, button=button, count=2 if double else 1)
    return {"clicked": [sx, sy], "button": button, "double": double}


@mcp.tool()
@_tool_errors
def nexremote_scroll(steps: int, x: float = -1, y: float = -1,
                     from_screenshot: bool = True) -> bool:
    """Scroll the mouse wheel: positive steps scroll down, negative up.

    If x, y are given (>= 0) the wheel is used over that point (screenshot
    coordinates by default); otherwise wherever the pointer is.
    """
    nr = _get_nexremote()
    at = nr.screen_point(x, y, from_screenshot) if x >= 0 and y >= 0 else None
    return bool(nr.scroll(steps, at=at))


@mcp.tool()
@_tool_errors
def nexremote_drag(x1: float, y1: float, x2: float, y2: float,
                   from_screenshot: bool = True) -> bool:
    """Press at (x1, y1), move to (x2, y2), release — drag and drop, sliders, selections."""
    nr = _get_nexremote()
    a = nr.screen_point(x1, y1, from_screenshot)
    b = nr.screen_point(x2, y2, from_screenshot)
    return bool(nr.drag(a[0], a[1], b[0], b[1]))


@mcp.tool()
@_tool_errors
def nexremote_set_text(ref: str, text: str) -> bool:
    """Replace the text of an editable element (entry, text field, combo box).

    Uses the accessible set-text interface when available, otherwise focuses
    the element and types. Returns True on success.
    """
    return bool(_get_nexremote().set_text(ref, text))


@mcp.tool()
@_tool_errors
def nexremote_key(combo: str) -> bool:
    """Press a key combination in the focused window.

    Examples: 'ctrl+c', 'alt+F4', 'Return', 'ctrl+shift+t', 'Escape', 'F5'.
    """
    return bool(_get_nexremote().key(combo))


@mcp.tool()
@_tool_errors
def nexremote_type(text: str) -> bool:
    """Type text into the focused window, exactly as given (any language, newlines included).

    Characters missing from the keyboard layout are pasted through the
    clipboard, and the user's clipboard is restored afterwards.
    """
    return bool(_get_nexremote().type_text(text))


@mcp.tool()
@_tool_errors
def nexremote_screenshot(window_ref: str = "", max_width: int = 1568) -> list:
    """See the screen: returns the image itself plus how to click on it.

    window_ref (from nexremote_windows) crops to one window, which keeps
    detail readable. The image is downscaled to max_width; click on what you
    see with nexremote_click_at(x, y) using this image's pixel coordinates.
    """
    from mcp.server.mcpserver import Image
    shot = _get_nexremote().screenshot(None, window_ref=window_ref or None,
                                       max_width=max_width)
    note = (f"{shot.width}x{shot.height} image; screen = origin {list(shot.origin)} + "
            f"image / {shot.scale:.4f}. Use nexremote_click_at(x, y) with image pixels.")
    return [Image(path=shot.path), note]


# --------------------------------------------------------- desktop terminals
@mcp.tool()
@_tool_errors
def nexremote_terminals() -> list[dict]:
    """List terminals already open on the desktop (Konsole tabs, tmux panes).

    Each has ref, program (what is running in the foreground, e.g. claude,
    vim, ssh, bash), cwd and title. Use the ref with nexremote_terminal_read
    and nexremote_terminal_send.
    """
    return _get_nexremote().terminals()


@mcp.tool()
@_tool_errors
def nexremote_terminal_read(ref: str, history: int = 0) -> str:
    """Read what a desktop terminal currently shows, as plain text.

    history > 0 also returns that many scrollback lines above the screen
    (tmux only; Konsole exposes the visible screen).
    """
    return _get_nexremote().terminal_read(ref, history)


@mcp.tool()
@_tool_errors
def nexremote_terminal_send(ref: str, text: str = "", enter: bool = False,
                            keys: list[str] | None = None) -> bool:
    """Type into a desktop terminal as if from its keyboard.

    text is sent literally; enter=True presses Enter afterwards. keys are
    special keys sent after the text: 'Enter', 'C-c', 'C-d', 'Esc', 'Up',
    'Down', 'Tab'. The window does not need focus.
    """
    return bool(_get_nexremote().terminal_send(ref, text, enter=enter, keys=keys))


# ------------------------------------------- NexRemote-owned tmux sessions
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


# ----------------------------------------------------------------- resources
# Read-only views a client can attach as context without spending a tool call.
# Each read is live: it queries the desktop at the moment of the read.
@mcp.resource("nexremote://windows", name="windows", mime_type="application/json",
              description="Every window on screen: ref, title, app, pid, bounds, focused.")
def resource_windows() -> list[dict]:
    return [w.to_dict() for w in _get_nexremote().windows()]


@mcp.resource("nexremote://terminals", name="terminals", mime_type="application/json",
              description="Terminals open on the desktop (Konsole tabs, tmux panes, consoles).")
def resource_terminals() -> list[dict]:
    return _get_nexremote().terminals()


@mcp.resource("nexremote://tree/{ref}", name="tree", mime_type="text/plain",
              description="Accessibility tree of one window, as nexremote_tree renders it. "
                          "ref is a window ref from nexremote://windows, percent-encoded "
                          "(win:{id} → win%3A%7Bid%7D); 'focused' means the active window.")
def resource_tree(ref: str) -> str:
    nr = _get_nexremote()
    if ref == "focused":
        ws = nr.windows()
        target = next((w for w in ws if w.focused), ws[0] if ws else None)
        if target is None:
            raise ValueError("no window on screen")
        ref = target.ref
    return nr.tree(ref)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
