#!/usr/bin/env bash
# İzole test masaüstü: kendi D-Bus'ı, kendi sanal KWin'i, kendi portalı.
# Kullanıcının gerçek oturumuna (ekran, fare, klavye, izinler) dokunmaz.
#
#   tests/sandbox.sh <komut...>     komutu sandbox ortamında çalıştırır
#
# Komut çalışırken şu değişkenler tanımlıdır:
#   WAYLAND_DISPLAY=nexr-sbx-$$   XDG_RUNTIME_DIR=<geçici>   NEXR_SANDBOX=1
set -euo pipefail

if [[ "${NEXR_SANDBOX_INNER:-}" != 1 ]]; then
    export NEXR_SANDBOX_INNER=1
    exec dbus-run-session -- "$0" "$@"
fi

ROOT="$(mktemp -d /tmp/nexr-sandbox.XXXXXX)"
chmod 700 "$ROOT"
mkdir -p "$ROOT"/{run,config,data,cache}
export XDG_RUNTIME_DIR="$ROOT/run" XDG_CONFIG_HOME="$ROOT/config" \
       XDG_DATA_HOME="$ROOT/data" XDG_CACHE_HOME="$ROOT/cache"
export XDG_CURRENT_DESKTOP=KDE XDG_SESSION_TYPE=wayland KDE_FULL_SESSION=true
export QT_QPA_PLATFORM=wayland QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 QT_ACCESSIBILITY=1
export WAYLAND_DISPLAY="nexr-sbx-$$" NEXR_SANDBOX=1
unset DISPLAY
# D-Bus'ın kendiliğinden başlattığı servisler (portal, a11y) bu ortamı görsün
dbus-update-activation-environment XDG_RUNTIME_DIR XDG_CONFIG_HOME XDG_DATA_HOME \
    XDG_CACHE_HOME XDG_CURRENT_DESKTOP XDG_SESSION_TYPE KDE_FULL_SESSION QT_QPA_PLATFORM \
    QT_LINUX_ACCESSIBILITY_ALWAYS_ON QT_ACCESSIBILITY WAYLAND_DISPLAY

pids=()
cleanup() {
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
    wait 2>/dev/null || true
    fusermount3 -u "$ROOT/run/doc" 2>/dev/null || true
    rm -rf "$ROOT" 2>/dev/null || true
}
trap cleanup EXIT

# Ekran yayını (ScreenCast) PipeWire ister — sandbox'a ait ayrı örnek
pipewire >"$ROOT/pipewire.log" 2>&1 & pids+=($!)
wireplumber >"$ROOT/wireplumber.log" 2>&1 & pids+=($!)
for _ in $(seq 50); do [[ -S "$XDG_RUNTIME_DIR/pipewire-0" ]] && break; sleep 0.1; done

kwin_wayland --virtual --no-lockscreen --socket "$WAYLAND_DISPLAY" \
    --width 1280 --height 800 >"$ROOT/kwin.log" 2>&1 &
pids+=($!)
for _ in $(seq 100); do [[ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]] && break; sleep 0.1; done
[[ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]] || { echo "sandbox: kwin başlamadı"; cat "$ROOT/kwin.log"; exit 1; }

for _ in $(seq 100); do
    busctl --user status org.freedesktop.portal.Desktop >/dev/null 2>&1 && break; sleep 0.1
done

export NEXR_SANDBOX_LOGS="$ROOT"
"$@"
