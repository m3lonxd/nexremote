# Release notes

Short, user-facing notes. The detailed development report and test results are
in `PROGRESS.md` (Turkish); the Turkish version of this file is `CHANGELOG.md`.

The site's release notes section is built from both files; versions and dates
must match, in the same order, or the site build stops.

## v0.7.0 — Speed (2026-11-10)

No more seconds between clicks: an action on a ref now takes milliseconds.

- **A background service.** The first `nexremote` call starts it; later calls
  reuse its open connections (a CLI call on Xvfb: 120 → 55–70 ms). It restarts
  itself when the code changes and quits when idle. `NEXREMOTE_SERVICE=0` turns
  it off; it is off by default on macOS and Windows.
- **A ref fast path.** Before a click only the path to the element is read and
  its identity checked, not the whole window (18 → 3 ms on GTK). If anything
  moved, a full read finds it by identity. `NEXREMOTE_STRICT=1` reads the full
  tree on every action.
- **Fewer model turns.** `do` runs several steps in one call and stops at the
  first failure; `--after` reports what changed, new refs included; plus `wait`,
  `click-text`, `fill` and `sleep`. MCP gains `nexremote_batch`,
  `nexremote_click_text`, `nexremote_fill`, `nexremote_wait` and
  `nexremote_after`; HTTP gains `/batch`, `/click_text`, `/fill` and `/wait`.
- **Clicks don't wait for the cursor.** The agent cursor is told where to go
  and the click goes out at once; if the cursor isn't running, it starts in the
  background without holding the click.
- **Measure it.** `NEXREMOTE_TRACE=1` prints per-step timings; `nexremote bench`
  reports p50/p95.
- **Fixes.** A `win:` ref could pick another window of the same app; tmux
  session names matched by prefix (`build` → `build2`); text starting with `-`
  and commands ending in `&` or a comment broke `run`; `run` hid the exit code;
  the HTTP adapter could be driven from a browser through localhost, and an
  empty token file started it without a token; on X11 `key A` went out without
  Shift.
- Speeds were measured on Xvfb, not yet on a real KDE desktop.

## v0.6.0 — Faster on Linux (2026-11-09)

- **Screenshots from the live stream.** On Wayland the frame is copied from
  the PipeWire stream the input service already holds: about 0.2 s instead of
  1.5 s.
- **Clicks don't wait for the cursor animation.**

## v0.5.0 — The agent cursor everywhere (2026-10-29)

- **A visible agent cursor** on Linux, Windows and macOS. It never touches your
  mouse: your clicks pass through it and focus doesn't move.
- **From every adapter:** MCP `nexremote_cursor`, HTTP `/cursor` and
  `/cursor_hide`, CLI `cursor`, `point` and `cursor-hide`.
- The Windows cursor is tested under Wine; the macOS cursor has not yet run on
  a real Mac.

## v0.4.0 — Everyday use (2026-10-29)

- **Electron and Chromium by name:** `nexremote launch <program>` starts an app
  with its accessibility tree on and returns the window's ref. When a tree is
  empty, `tree` says why and what to do.
- **A compact tree:** hidden items are skipped, nameless wrappers folded, and
  actionable lines carry `ref=` (550 → 21 lines in the Zen browser).
- **MCP resources:** `nexremote://windows`, `nexremote://terminals`,
  `nexremote://tree/{ref}`.
- **HTTP adapter:** token file, TLS, a body limit; a token of at least 24
  characters is required to listen beyond localhost. `deploy/install-service.sh`
  installs a systemd user service.
- **Sway on a real compositor:** the mouse through Sway IPC, the wheel through
  the wlroots virtual pointer, screenshots with grim.

## v0.3.0 — Multi-platform (2026-10-17)

- **One set of commands, five desktops:** KDE Plasma 6 on Wayland, X11,
  Sway/Hyprland, Windows and macOS (preview) behind the same contract.
- **A pip package:** `pip install .[all]` installs the four commands on Windows
  and macOS too.
- **The first run on a real desktop** (KDE Plasma 6, three monitors) found three
  bugs the sandbox couldn't: clicks couldn't reach beyond the first monitor, the
  agent cursor was drawn inside a black square, and it showed 36 px off its
  target. All three are fixed.
- **The agent cursor** is drawn from your cursor theme and moves at 120 fps.

## v0.1 — First release (2026-10-13)

- **Four ways in:** a 15-command CLI (with `--json`), a 13-tool MCP server, an
  HTTP API and a text loop for models without tool calling; a skill text for
  agents.
- **GUI:** see windows and press controls by name through AT-SPI.
- **Terminal:** tmux/PTY sessions that live on between calls.
