#!/usr/bin/env bash
# Windows testleri için ayrı Wine ortamı: kendi prefix'i + resmi gömülebilir
# (embeddable) Windows Python'u. Kullanıcının ~/.wine'ına ve ekranına dokunmaz.
#
#   tests/wine/setup.sh          kur (tekrar çalıştırmak güvenli — idempotent)
#
# Dizinler:
#   ~/.cache/nexremote-wine/prefix        WINEPREFIX (yalnızca bu testlere ait)
#   ~/.cache/nexremote-wine/python        gömülebilir Python (Z:\ yolundan çalışır)
#   ~/.cache/nexremote-wine/dl            indirilen zip/wheel/deb önbelleği
#   ~/.cache/nexremote-wine/uiautomationclient.tlb   UIA tür kitaplığı (aşağıda, 6. adım)
#
# Gömülebilir Python'da python312._pth dosyası varken PYTHONPATH YOK SAYILIR.
# Bu yüzden ._pth dosyasına deponun core/ dizininin Z:\ yolunu ve
# Lib\site-packages'ı ekliyoruz, "import site" satırını açıyoruz.
set -euo pipefail

PY_VER="${NEXR_WINE_PY:-3.12.10}"
PY_TAG="python$(echo "$PY_VER" | cut -d. -f1,2 | tr -d .)"      # python312
BASE="$HOME/.cache/nexremote-wine"
PREFIX="$BASE/prefix"
PYDIR="$BASE/python"
DL="$BASE/dl"
HERE="$(cd "$(dirname "$0")" && pwd)"
CORE="$(cd "$HERE/../../core" && pwd)"
COMTYPES_VER="${NEXR_COMTYPES:-1.4.11}"

need() { command -v "$1" >/dev/null || { echo "eksik araç: $1" >&2; exit 1; }; }
need wine; need wineserver; need xvfb-run; need curl; need unzip

mkdir -p "$BASE" "$DL"
export WINEPREFIX="$PREFIX" WINEDEBUG=-all WINEARCH=win64 WINEDLLOVERRIDES="mscoree,mshtml="

# 1) prefix
if [[ ! -f "$PREFIX/system.reg" ]]; then
    echo "▶ Wine prefix oluşturuluyor: $PREFIX"
    xvfb-run -a wineboot --init
    wineserver -w
else
    echo "✓ prefix hazır"
fi

# 2) gömülebilir Python
ZIP="$DL/python-$PY_VER-embed-amd64.zip"
if [[ ! -s "$ZIP" ]]; then
    echo "▶ python-$PY_VER-embed-amd64.zip indiriliyor"
    curl -fsSL --retry 3 -o "$ZIP.part" \
        "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-embed-amd64.zip"
    mv "$ZIP.part" "$ZIP"
fi
unzip -tq "$ZIP" >/dev/null || { echo "bozuk zip: $ZIP" >&2; rm -f "$ZIP"; exit 1; }
if [[ ! -f "$PYDIR/python.exe" ]]; then
    rm -rf "$PYDIR"; mkdir -p "$PYDIR"
    unzip -q "$ZIP" -d "$PYDIR"
fi
echo "✓ Python $PY_VER: $PYDIR"

# 3) ._pth: core/ (Z:\ yolu) + site-packages + import site
WCORE="Z:$(echo "$CORE" | tr / '\\')"
cat > "$PYDIR/$PY_TAG._pth" <<PTH
$PY_TAG.zip
.
Lib\\site-packages
$WCORE
import site
PTH
mkdir -p "$PYDIR/Lib/site-packages"
echo "✓ $PY_TAG._pth → $WCORE"

# 4) comtypes (UI Automation için; saf Python wheel = zip)
if [[ ! -d "$PYDIR/Lib/site-packages/comtypes" ]]; then
    WHL="$DL/comtypes-$COMTYPES_VER-py3-none-any.whl"
    if [[ ! -s "$WHL" ]]; then
        echo "▶ comtypes $COMTYPES_VER indiriliyor"
        curl -fsSL --retry 3 -o "$WHL.part" \
            "https://files.pythonhosted.org/packages/py3/c/comtypes/comtypes-$COMTYPES_VER-py3-none-any.whl"
        mv "$WHL.part" "$WHL"
    fi
    unzip -q -o "$WHL" -d "$PYDIR/Lib/site-packages"
fi
echo "✓ comtypes kurulu"

# 6) UI Automation tür kitaplığı. Wine'ın uiautomationcore.dll'i yalnızca özel
#    (UIA_wine_private) tür kitaplığı taşır; comtypes'ın IUIAutomation sarmalayıcısı
#    için Wine'ın kendi uiautomationclient.idl'ini widl ile derliyoruz. widl ve IDL
#    root gerektirmeden `apt-get download` ile alınıp önbelleğe açılır.
TLB="$BASE/uiautomationclient.tlb"
if [[ ! -s "$TLB" ]]; then
    if command -v apt-get >/dev/null && command -v dpkg-deb >/dev/null; then
        mkdir -p "$DL/deb"
        (cd "$DL/deb" && ls wine64-tools_*.deb >/dev/null 2>&1 \
            || apt-get download -q wine64-tools libwine-dev)
        for f in "$DL"/deb/*.deb; do dpkg-deb -x "$f" "$DL/deb/x"; done
        IDL_DIR="$DL/deb/x/usr/include/wine/wine/windows"
        "$DL/deb/x/usr/lib/wine/widl" -t -m64 -I "$IDL_DIR" -o "$TLB" "$IDL_DIR/uiautomationclient.idl"
    else
        echo "⊘ apt-get/dpkg-deb yok: UIA tür kitaplığı derlenemedi (UIA testleri atlanır)"
    fi
fi
GEN="$PYDIR/Lib/site-packages/comtypes/gen"
if [[ -s "$TLB" ]] && ! ls "$GEN"/UIAutomationClient.py >/dev/null 2>&1; then
    WTLB="Z:$(echo "$TLB" | tr / '\\')"
    # İlk üretim içe aktarmada düşer: Wine IDL'i SAFEARRAY(IUIAutomationCondition)
    # yazıyor (Microsoft SDK: ...Condition*), comtypes işaretçisiz arayüz dizisini
    # tanımıyor. Üretilen sarmalayıcıyı SDK'daki tanıma göre düzeltip yeniden yüklüyoruz.
    "$HERE/run.sh" -c "import comtypes.client as c; c.GetModule(r'$WTLB')" >/dev/null 2>&1 || true
    sed -i -E 's/_midlSAFEARRAY\((IUIAutomation[A-Za-z]*)\)/_midlSAFEARRAY(POINTER(\1))/g' \
        "$GEN"/_944DE083_8FB8_45CF_BCB7_C477ACB2F897_*.py
    rm -rf "$GEN/__pycache__"
    "$HERE/run.sh" -c "import comtypes.client as c; m = c.GetModule(r'$WTLB'); assert hasattr(m, 'IUIAutomation')"
fi
[[ -s "$TLB" ]] && echo "✓ UIA tür kitaplığı: $TLB"

# 7) duman testi
"$HERE/run.sh" -c "import sys, nexremote, comtypes; print('python', sys.version.split()[0], sys.platform)"
