#!/usr/bin/env bash
# Wine içindeki Windows Python'uyla betik çalıştırır — başsız (xvfb-run -a),
# kullanıcının gerçek ekranına asla bağlanmaz.
#
#   tests/wine/run.sh <betik.py> [argümanlar]
#   tests/wine/run.sh -c "python kodu"
#
# Betik yolu Linux yolu olarak verilebilir; Z:\ yoluna çevrilir.
set -euo pipefail
BASE="$HOME/.cache/nexremote-wine"
PY="$BASE/python/python.exe"
[[ -f "$PY" ]] || { echo "önce tests/wine/setup.sh çalıştırın" >&2; exit 1; }
export WINEPREFIX="$BASE/prefix" WINEDEBUG=-all WINEARCH=win64 \
       WINEDLLOVERRIDES="mscoree,mshtml=" PYTHONIOENCODING=utf-8 PYTHONUTF8=1
unset DISPLAY WAYLAND_DISPLAY
# UIA tür kitaplığı (setup.sh 6. adım) — Wine ortam değişkenlerini Windows sürecine geçirir
if [[ -s "$BASE/uiautomationclient.tlb" ]]; then
    export NEXREMOTE_UIA_TLB="Z:$(echo "$BASE/uiautomationclient.tlb" | tr / '\\')"
fi

args=()
if [[ $# -gt 0 && "$1" != -* && -f "$1" ]]; then
    args+=("Z:$(realpath "$1" | tr / '\\')"); shift
fi
args+=("$@")
# kendi wineserver'ımız: sanal ekranla birlikte başlar ve biter
status=0
xvfb-run -a -s "-screen 0 1280x800x24" bash -c '
    wine "$0" "$@"; rc=$?; wineserver -k 2>/dev/null; exit $rc' "$PY" "${args[@]}" || status=$?
exit $status
