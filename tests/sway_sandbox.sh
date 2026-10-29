#!/usr/bin/env bash
# İzole, başsız GERÇEK Sway: kendi D-Bus'ı, kendi XDG_RUNTIME_DIR'ı.
# Kullanıcının oturumuna dokunmaz. Sway yoksa 77 ile çıkar (atla).
#
#   tests/sway_sandbox.sh <komut...>
set -euo pipefail
command -v sway >/dev/null || { echo "sway yok — atlandı"; exit 77; }

if [[ "${NEXR_SWAY_INNER:-}" != 1 ]]; then
    export NEXR_SWAY_INNER=1
    exec dbus-run-session -- "$0" "$@"
fi

ROOT="$(mktemp -d /tmp/nexr-sway.XXXXXX)"
chmod 700 "$ROOT"
mkdir -p "$ROOT"/{run,config}
export XDG_RUNTIME_DIR="$ROOT/run" XDG_CONFIG_HOME="$ROOT/config"
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1
export XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland NEXR_SANDBOX=1 NEXR_SWAY=1
export GTK_A11Y=atspi NO_AT_BRIDGE=0
unset DISPLAY WAYLAND_DISPLAY SWAYSOCK HYPRLAND_INSTANCE_SIGNATURE KDE_FULL_SESSION
cat > "$ROOT/sway.conf" <<CONF
output HEADLESS-1 resolution 1280x800 position 0 0
default_border none
focus_follows_mouse no
seat seat0 hide_cursor 0
CONF

cleanup() {
    [[ -n "${SWAY_PID:-}" ]] && kill "$SWAY_PID" 2>/dev/null
    wait 2>/dev/null || true
    fusermount3 -u "$ROOT/run/doc" 2>/dev/null || fusermount -u "$ROOT/run/doc" 2>/dev/null || true
    rm -rf "$ROOT"
}
trap cleanup EXIT

sway -c "$ROOT/sway.conf" >"$ROOT/sway.log" 2>&1 &
SWAY_PID=$!
s="" w=""
for _ in $(seq 100); do
    for f in "$XDG_RUNTIME_DIR"/sway-ipc.*.sock; do [[ -S "$f" ]] && s="$f"; done
    for f in "$XDG_RUNTIME_DIR"/wayland-?; do [[ -S "$f" ]] && w="${f##*/}"; done
    [[ -n "$s" && -n "$w" ]] && break
    sleep 0.1
done
[[ -n "$s" && -n "$w" ]] || { echo "sway başlamadı"; cat "$ROOT/sway.log"; exit 1; }
export SWAYSOCK="$s" WAYLAND_DISPLAY="$w"
dbus-update-activation-environment XDG_RUNTIME_DIR WAYLAND_DISPLAY SWAYSOCK XDG_CURRENT_DESKTOP
export NEXR_SANDBOX_LOGS="$ROOT"
# Başsız Sway'de fare aygıtı yok: istemciler wl_pointer almasın diye sanal bir tane tak
python3 "$(dirname "$0")/wl_virtual_pointer.py" >"$ROOT/vptr.log" 2>&1 &
for _ in $(seq 50); do grep -q ok "$ROOT/vptr.log" 2>/dev/null && break; sleep 0.1; done
grep -q ok "$ROOT/vptr.log" || { echo "sanal fare takılamadı"; cat "$ROOT/vptr.log"; exit 1; }
"$@"
