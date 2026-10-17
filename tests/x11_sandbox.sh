#!/usr/bin/env bash
# İzole X11 masaüstü: kendi D-Bus'ı + Xvfb. Kullanıcının oturumuna dokunmaz.
#   tests/x11_sandbox.sh <komut...>
set -euo pipefail
if [[ "${NEXR_X11_INNER:-}" != 1 ]]; then
    export NEXR_X11_INNER=1
    exec dbus-run-session -- "$0" "$@"
fi
ROOT="$(mktemp -d /tmp/nexr-x11.XXXXXX)"
chmod 700 "$ROOT"
# D-Bus'ın bu oturum için kendiliğinden başlattığı servisler (portal, a11y)
# oturum kapanınca kendileri çıkmayabilir: veri yolu adresi bizimkiler olanı kapat
cleanup() {
    for env in /proc/[0-9]*/environ; do
        pid="${env#/proc/}"; pid="${pid%/environ}"
        [[ "$pid" == "$$" ]] && continue
        if tr '\0' '\n' <"$env" 2>/dev/null | grep -qxF "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    rm -rf "$ROOT"
}
trap cleanup EXIT
unset WAYLAND_DISPLAY SWAYSOCK HYPRLAND_INSTANCE_SIGNATURE KDE_FULL_SESSION
export XDG_SESSION_TYPE=x11 XDG_CURRENT_DESKTOP=none XDG_RUNTIME_DIR="$ROOT" \
       GDK_BACKEND=x11 NEXR_SANDBOX=1 NEXR_SANDBOX_KIND=x11 NEXREMOTE_CURSOR=0
xvfb-run -a -s "-screen 0 1280x800x24" "$@"
