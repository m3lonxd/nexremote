---
name: nexremote
description: >
  See and control this desktop (Linux, Windows or macOS) and the terminals open on it: list every
  window, read a window's UI tree and press buttons by name, take a screenshot
  and click/scroll/drag on it, type text, press shortcuts, and read or type
  into terminals that are already running (other CLI agents, ssh, builds).
  Use when asked to operate a GUI app, check what is on screen, or watch and
  drive another terminal session. Works from any harness that can run a shell
  command.
---

# NexRemote — desktop + terminal control

Every command is `nexremote ...`. Add `--json` anywhere for machine output.

## 1. Look first
```bash
nexremote observe          # every window (ref win:{...}) + every open terminal
nexremote caps             # what works on this machine (input, windows, terminals)
```
If `caps` says something needs approval, run `nexremote setup` once:
- Linux Wayland: tell the user to press **Share** (with "Allow restoring"
  ticked) on the remote-control dialog. After that it never asks again.
- macOS: tell the user to allow the terminal/Python under System Settings →
  Privacy & Security → **Accessibility** and **Screen Recording**.
- Windows and X11 need no approval.

## 2. Act on GUI apps — structure first, pixels second
```bash
nexremote tree --window win:{...}          # visible UI; actionable lines end in ref=…
nexremote click <ref>                      # press it; the user's mouse does not move
nexremote find "Save" --window win:{...}   # search by name/value → refs
nexremote set-text <ref> "text"           # replace a field's content
```
`tree` leaves out closed menus and folds unnamed layout boxes, so what you
read is what is on screen. Content scrolled out of view may be missing:
scroll, then read again.

Electron/Chromium apps (Discord, Vesktop, VS Code, Slack, Chrome) publish an
empty tree unless started with a flag; `tree` then ends with a `#` hint line.
```bash
nexremote launch vesktop                   # starts it with the flag → win:{...}
nexremote launch code ~/project            # arguments pass through
```
A running single-instance app ignores the flag. Ask the user before closing
their app to relaunch it.

When the app still publishes no tree (games, canvas apps):
```bash
nexremote screenshot --window win:{...} --max-width 1568   # then look at the image
nexremote click 412 230 --from-shot        # pixel coords from THAT image
nexremote click 412 230 --from-shot --right | --double
nexremote scroll 5 --at 600 400            # + down / - up (screen coords)
nexremote drag 100 200 400 200             # screen coords
```
The user's pointer is moved back where it was right after each click.

## 3. Keyboard
```bash
nexremote window win:{...} activate        # focus the target window first
nexremote type "Merhaba dünya"             # any language, newlines ok
nexremote key ctrl+s | Return | alt+F4 | ctrl+shift+t
```
On macOS use `cmd` for the Command key (`cmd+s`, `cmd+v`); `ctrl` is Control.

## 4. Terminals already open on the desktop
```bash
nexremote terms                            # refs: konsole:… tmux:… wincon:PID terminal:… iterm:…
nexremote term-read konsole:7891:2         # what that terminal shows right now
nexremote term-send konsole:7891:2 "git status" --enter
nexremote term-send konsole:7891:2 --key C-c          # Enter, C-c, C-d, Esc, Up, Tab...
```
`program` tells you what runs in the foreground (claude, vim, ssh, bash).
Read again after sending: output takes time; poll `term-read` until it settles.

## 5. Your own scratch terminal
```bash
nexremote run "pytest -q" --session build  # runs, waits, returns output + exit code
nexremote screen --session build           # read it later (sessions persist)
nexremote keys C-c --session build
nexremote cleanup                          # close only the sessions you created
```

## Rules
- Verify every action: `observe`, `term-read` or a new screenshot. Do not assume.
- Activate the window before `type`/`key`; keystrokes go to the focused window.
- Closing windows, sending to someone's terminal, or typing into a shell is
  real and visible to the user. Say what you are about to do when it matters.
