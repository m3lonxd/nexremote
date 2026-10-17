#!/usr/bin/env bash
# NexRemote indirilebilir paketi üretir (site üzerinden dağıtım için).
# GitHub askıda olduğundan kullanıcılar projeyi doğrudan buradan indirir.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/site/download"
mkdir -p "$OUT"

NAME="nexremote"
STAGE="$(mktemp -d)"
DEST="$STAGE/$NAME"
mkdir -p "$DEST"

# Sürüm bilgisi (git varsa)
if git -C "$ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
  VER="$(git -C "$ROOT" describe --tags --always 2>/dev/null || git -C "$ROOT" rev-parse --short HEAD)"
else
  VER="$(date +%Y%m%d)"
fi

# Kaynak dosyaları kopyala (site, .git, önbellek hariç)
cd "$ROOT"
tar --exclude='./.git' \
    --exclude='./site' \
    --exclude='./.claude' \
    --exclude='*/__pycache__' \
    --exclude='*.pyc' \
    --exclude='./download' \
    -cf - . | (cd "$DEST" && tar -xf -)

# Sürüm dosyası ekle
printf '%s\n' "$VER" > "$DEST/VERSION"

# zip ve tar.gz üret
# zip var olan arşive ekler; eski sürümden kalan dosyalar pakete karışmasın
rm -f "$OUT/$NAME.zip" "$OUT/$NAME.tar.gz"
( cd "$STAGE" && zip -qr "$OUT/$NAME.zip" "$NAME" && tar -czf "$OUT/$NAME.tar.gz" "$NAME" )
rm -rf "$STAGE"

SIZE_ZIP=$(du -h "$OUT/$NAME.zip" | cut -f1)
SIZE_TGZ=$(du -h "$OUT/$NAME.tar.gz" | cut -f1)
echo "üretildi (sürüm $VER):"
echo "  $OUT/$NAME.zip     ($SIZE_ZIP)"
echo "  $OUT/$NAME.tar.gz  ($SIZE_TGZ)"
