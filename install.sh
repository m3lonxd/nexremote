#!/usr/bin/env bash
# NexRemote kurulum — wrapper'ları kurar, bağımlılıkları kontrol eder.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${NEXREMOTE_BIN:-$HOME/bin}"
mkdir -p "$BIN"

echo "NexRemote kurulumu"
echo "  proje : $ROOT"
echo "  bin   : $BIN"
echo

# --- wrapper: nexremote (CLI) ---
cat > "$BIN/nexremote" <<EOF
#!/bin/bash
export PYTHONPATH="$ROOT/core:$ROOT/adapters/cli:\$PYTHONPATH"
exec python3 "$ROOT/adapters/cli/nexremote_cli.py" "\$@"
EOF
chmod +x "$BIN/nexremote"

# --- wrapper: nexremote-agent (metin protokolü) ---
cat > "$BIN/nexremote-agent" <<EOF
#!/bin/bash
export PYTHONPATH="$ROOT/core:$ROOT/adapters/cli:\$PYTHONPATH"
exec python3 "$ROOT/adapters/cli/agent_loop.py" "\$@"
EOF
chmod +x "$BIN/nexremote-agent"

# --- wrapper: nexremote-http ---
cat > "$BIN/nexremote-http" <<EOF
#!/bin/bash
export PYTHONPATH="$ROOT/core:\$PYTHONPATH"
exec python3 "$ROOT/adapters/http/server.py" "\$@"
EOF
chmod +x "$BIN/nexremote-http"

# --- wrapper: nexremote-mcp ---
cat > "$BIN/nexremote-mcp" <<EOF
#!/bin/bash
export PYTHONPATH="$ROOT/core:$ROOT/adapters/mcp:\$PYTHONPATH"
exec python3 "$ROOT/adapters/mcp/server.py" "\$@"
EOF
chmod +x "$BIN/nexremote-mcp"

echo "✓ wrapper'lar kuruldu: nexremote, nexremote-agent, nexremote-http, nexremote-mcp"
echo

# --- bağımlılık kontrolü ---
chk() { if command -v "$1" >/dev/null 2>&1; then echo "  ✓ $1"; else echo "  ✗ $1  ($2)"; fi; }
echo "Bağımlılıklar:"
chk tmux         "terminal backend için (zorunlu değil, PTY fallback var)"
chk wtype        "Wayland metin/tuş girişi için (Debian: sudo apt install wtype)"
chk spectacle    "KDE ekran görüntüsü (veya grim)"
chk grim         "Wayland ekran görüntüsü"
python3 - <<'PY' 2>/dev/null || echo "  ✗ AT-SPI (Debian: sudo apt install python3-gi gir1.2-atspi-2.0)"
import gi; gi.require_version('Atspi','2.0')
from gi.repository import Atspi
print("  ✓ AT-SPI (GUI görme)")
PY
python3 - <<'PY' 2>/dev/null || echo "  ✗ mcp paketi (opsiyonel: pip install mcp)"
import mcp; print("  ✓ mcp (MCP adaptörü)")
PY

echo
if ! echo ":$PATH:" | grep -q ":$BIN:"; then
  echo "⚠  $BIN PATH'te değil. Kabuk profiline ekle:"
  echo "     export PATH=\"$BIN:\$PATH\""
fi
echo "Bitti. Test: nexremote caps"
