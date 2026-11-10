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
import threading
from pathlib import Path
from typing import Any, Optional

# Make `import nexremote` work regardless of the current working directory.
_CORE = Path(__file__).resolve().parents[2] / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

_nr: Any = None
# Araçlar iş parçacığı havuzunda çalışabilir; çekirdek (AT-SPI, D-Bus, X)
# iş parçacığı güvenli değil — çağrılar sırayla. RLock: batch iç içe çağırır.
_lock = threading.RLock()


def _get_nexremote():
    """Lazy singleton for the NexRemote facade.

    Built on first tool call so importing this module stays cheap and
    never fails just because a backend dependency is missing.
    """
    global _nr
    if _nr is None:
        from nexremote import NexRemote
        _nr = NexRemote(long_lived=True)
    return _nr


def _tool_errors(fn):
    """Convert any unexpected exception into a clean ToolError message.

    Tracebacks are never sent to the model; only a short, readable reason.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with _lock:
                return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            raise ToolError(f"{fn.__name__} failed: {code}: {exc}") from None

    return wrapper


mcp = MCPServer(
    name="nexremote",
    instructions=(
        "NexRemote: see and control this desktop and its terminals. "
        "Start with nexremote_observe (windows + open terminals). "
        "Prefer structure over pixels: nexremote_tree(interactive=True) gives "
        "element refs; nexremote_click(ref) presses them without moving the "
        "user's mouse; nexremote_click_text('Save') finds and presses in one call. "
        "BE FAST: plan several steps and send them in ONE nexremote_batch call "
        "(it stops at the first error and returns 'after' = what changed on "
        "screen, with refs for new elements) instead of one tool call per action "
        "plus a separate check. Use nexremote_wait instead of sleeping and re-reading. "
        "Old refs stay valid after the UI changes (they are matched by identity). "
        "When an app exposes no tree, call nexremote_screenshot, then "
        "nexremote_click_at(x, y, from_screenshot=True) with pixel coordinates read "
        "from that image. Terminals already open on the desktop (other CLI agents, "
        "ssh, builds) are read with nexremote_terminal_read and driven with "
        "nexremote_terminal_send. Refs are opaque strings; pass them back unchanged."
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
def nexremote_tree(window_ref: str = "", interactive: bool = False, root: str = "") -> str:
    """Snapshot a window's accessibility tree as indented readable text.

    Each line shows [role] name, actions like <click,set_text> and, for
    actionable elements, a snapshot ref like ref=@s7k2m9qx:e4 that
    nexremote_click / nexremote_set_text accept. Refs are tied to this
    snapshot: before acting, the element is re-identified by role, name and
    named ancestors; if the UI changed the action fails with STALE_REF or
    AMBIGUOUS_TARGET instead of hitting the wrong element - take a new tree.
    interactive=True keeps only actionable elements and their ancestors.
    root=<ref> returns only that element's subtree (drill into dense apps).
    Pass window_ref from nexremote_windows; empty string uses the first
    window. An empty Electron tree ends with a hint line.
    """
    return _get_nexremote().tree(window_ref or None, interactive=interactive,
                                 root=root or None)


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
def nexremote_cursor(x: Optional[float] = None, y: Optional[float] = None, click: bool = False,
                     hide: bool = False, from_screenshot: bool = True) -> dict:
    """Move the agent's OWN visible pointer so the user can see where you act.

    This is a separate on-screen arrow, not the user's mouse: it never moves
    their pointer, steals focus or blocks clicks. click=True shows a click
    ring (no real click — use nexremote_click_at for that). hide=True hides it.
    Coordinates work like nexremote_click_at. Linux (X11/XWayland), Windows
    and macOS (preview).
    """
    nr = _get_nexremote()
    if hide:
        return {"hidden": bool(nr.cursor_hide())}
    if x is None or y is None:
        raise ValueError("x and y are required unless hide=True")
    sx, sy = nr.screen_point(x, y, from_screenshot)
    return {"ok": bool(nr.cursor(sx, sy, click=click)), "at": [sx, sy]}


@mcp.tool()
@_tool_errors
def nexremote_scroll(steps: int, x: Optional[float] = None, y: Optional[float] = None,
                     from_screenshot: bool = True) -> bool:
    """Scroll the mouse wheel: positive steps scroll down, negative up.

    If x, y are given the wheel is used over that point (screenshot
    coordinates by default; negative values are valid on multi-monitor
    setups); otherwise wherever the pointer is.
    """
    nr = _get_nexremote()
    at = nr.screen_point(x, y, from_screenshot) if x is not None and y is not None else None
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


# ------------------------------------------------------------ fast paths
@mcp.tool()
@_tool_errors
def nexremote_click_text(text: str, window_ref: str = "", role: str = "",
                         exact: bool = False) -> dict:
    """Find an actionable element by its visible name and press it, in one call.

    Exact (case-insensitive) name matches win over substring matches. If more
    than one element matches, nothing is pressed and the error lists the
    candidates with refs (AMBIGUOUS_TARGET). role narrows it (button, link,
    menu_item, tab...). window_ref defaults to the focused window.
    """
    return _get_nexremote().click_text(text, window_ref=window_ref or None,
                                       role=role or None, exact=exact)


@mcp.tool()
@_tool_errors
def nexremote_fill(ref: str, text: str, enter: bool = False) -> bool:
    """Replace a field's text (like nexremote_set_text) and optionally press Enter in it."""
    return bool(_get_nexremote().fill(ref, text, enter=enter))


@mcp.tool()
@_tool_errors
def nexremote_wait(element: str = "", window_title: str = "", gone: str = "",
                   window_ref: str = "", timeout: float = 10.0) -> dict:
    """Wait (polling inside, cheaply) until ONE condition holds, then return.

    element: an element whose name/value contains this text appears (returns
    its ref). window_title: a window whose title contains this text opens.
    gone: the element behind this ref disappears. Fails with TIMEOUT.
    """
    return _get_nexremote().wait_for(element=element or None,
                                     window_title=window_title or None,
                                     gone=gone or None, window_ref=window_ref or None,
                                     timeout=timeout)


@mcp.tool()
@_tool_errors
def nexremote_after(window_ref: str = "") -> str:
    """What changed in a window since its last snapshot: '+' new elements (with
    refs), '-' gone, '~' changed values. Defaults to the focused window."""
    return _get_nexremote().after(window_ref=window_ref or None)


_BATCH_TOOLS = {
    "click": lambda **k: nexremote_click(**k),
    "click_text": lambda **k: nexremote_click_text(**k),
    "click_at": lambda **k: nexremote_click_at(**k),
    "set_text": lambda **k: nexremote_set_text(**k),
    "fill": lambda **k: nexremote_fill(**k),
    "key": lambda **k: nexremote_key(**k),
    "type": lambda **k: nexremote_type(**k),
    "scroll": lambda **k: nexremote_scroll(**k),
    "drag": lambda **k: nexremote_drag(**k),
    "window_action": lambda **k: nexremote_window_action(**k),
    "wait": lambda **k: nexremote_wait(**k),
    "tree": lambda **k: nexremote_tree(**k),
    "find": lambda **k: nexremote_find(**k),
    "terminal_send": lambda **k: nexremote_terminal_send(**k),
    "terminal_read": lambda **k: nexremote_terminal_read(**k),
    "run": lambda **k: nexremote_run(**k),
    "send_keys": lambda **k: nexremote_send_keys(**k),
    "read_screen": lambda **k: nexremote_read_screen(**k),
}


@mcp.tool()
@_tool_errors
def nexremote_batch(steps: list[dict], after: bool = True) -> dict:
    """Run several actions in ONE call, in order; stop at the first failure.

    steps: [{"tool": "click", "args": {"ref": "@s…:e4"}},
            {"tool": "type", "args": {"text": "7"}},
            {"tool": "key", "args": {"combo": "Return"}}]
    tool is one of: click, click_text, click_at, set_text, fill, key, type,
    scroll, drag, window_action, wait, tree, find, terminal_send,
    terminal_read, run, send_keys, read_screen (args as in those tools).
    after=True (default) appends what changed on screen afterwards, so no
    separate verification call is needed.
    """
    nr = _get_nexremote()
    try:
        before = nr.windows() if after else None
    except Exception:
        before = None
    out, failed = [], False
    for i, st in enumerate(steps):
        name = str(st.get("tool", "")).removeprefix("nexremote_")
        fn = _BATCH_TOOLS.get(name)
        if fn is None:
            out.append({"tool": name, "ok": False, "error": f"unknown or not batchable tool: {name}"})
            failed = True
            break
        try:
            out.append({"tool": name, "ok": True, "result": fn(**(st.get("args") or {}))})
        except Exception as e:
            out.append({"tool": name, "ok": False, "error": str(e)})
            failed = True
            break
    res: dict = {"ok": not failed, "steps": out, "skipped": len(steps) - len(out)}
    if after:
        try:
            res["after"] = nr.after(before_windows=before)
        except Exception as e:
            res["after"] = f"(after unavailable: {e})"
    return res


# ----------------------------------------------------------------- resources
# Read-only views a client can attach as context without spending a tool call.
# Each read is live: it queries the desktop at the moment of the read.
@mcp.resource("nexremote://windows", name="windows", mime_type="application/json",
              description="Every window on screen: ref, title, app, pid, bounds, focused.")
def resource_windows() -> list[dict]:
    with _lock:
        return [w.to_dict() for w in _get_nexremote().windows()]


@mcp.resource("nexremote://terminals", name="terminals", mime_type="application/json",
              description="Terminals open on the desktop (Konsole tabs, tmux panes, consoles).")
def resource_terminals() -> list[dict]:
    with _lock:
        return _get_nexremote().terminals()


@mcp.resource("nexremote://tree/{ref}", name="tree", mime_type="text/plain",
              description="Accessibility tree of one window, as nexremote_tree renders it. "
                          "ref is a window ref from nexremote://windows, percent-encoded "
                          "(win:{id} → win%3A%7Bid%7D); 'focused' means the active window.")
def resource_tree(ref: str) -> str:
    with _lock:
        return _resource_tree(ref)


def _resource_tree(ref: str) -> str:
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
