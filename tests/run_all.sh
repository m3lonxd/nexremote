#!/usr/bin/env bash
# Tüm testler. Girdi enjeksiyonu yapan testler yalnızca izole sandbox'ta koşar;
# gerçek masaüstünde yalnızca salt-okuma testleri çalışır.
set -u
cd "$(dirname "$0")/.."
fail=0
run() { echo; echo "▶ $1"; shift; "$@" 2>&1 | grep -E '✓|✗|⊘|SONUÇ|KABUL|SANDBOX|MCP:|WLROOTS|WINDOWS|PLATFORM' ; [[ ${PIPESTATUS[0]} -eq 0 ]] || fail=1; }
run "duman (salt okuma)"        python3 tests/test_smoke.py
run "kabul (salt okuma)"        python3 tests/test_acceptance.py
run "MCP stdio (salt okuma)"    python3 tests/test_mcp_stdio.py
run "Sway/Hyprland (sahte IPC)"  python3 tests/test_wlroots_fake.py
run "X11 — Xvfb sandbox"        tests/x11_sandbox.sh python3 tests/test_x11_sandbox.py
run "giriş — sandbox"           tests/sandbox.sh python3 tests/test_input_sandbox.py
run "CLI uçtan uca — sandbox"   tests/sandbox.sh bash -c 'cd tests && python3 test_cli_sandbox.py'
echo; [[ $fail -eq 0 ]] && echo "TÜMÜ GEÇTİ" || echo "BAŞARISIZ TEST VAR"
exit $fail
