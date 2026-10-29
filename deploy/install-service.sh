#!/usr/bin/env bash
# NexRemote HTTP adaptörünü systemd kullanıcı servisi olarak kurar.
#
#   deploy/install-service.sh                      yalnızca bu makine (127.0.0.1:8788)
#   deploy/install-service.sh --host 0.0.0.0 --tls-cert C --tls-key K
#                                                  ağa aç (token + TLS)
#   deploy/install-service.sh --uninstall          servisi kaldır (token dosyası kalır)
#
# Token ~/.config/nexremote/http.token içinde (yalnızca sizin okuyabileceğiniz
# 600 izniyle) üretilir; komut satırında görünmez. TLS vermeden ağa açarsanız
# token düz metin gider — onun yerine SSH tüneli kullanın:
#   ssh -N -L 8788:127.0.0.1:8788 kullanici@bu-makine
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=nexremote-http
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/nexremote"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
HOST=127.0.0.1 PORT=8788 CERT="" KEY="" UNINSTALL=0 START=1

die() { echo "hata: $*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST="${2:?}"; shift 2 ;;
        --port) PORT="${2:?}"; shift 2 ;;
        --tls-cert) CERT="$(realpath "${2:?}")"; shift 2 ;;
        --tls-key) KEY="$(realpath "${2:?}")"; shift 2 ;;
        --uninstall) UNINSTALL=1; shift ;;
        --no-start) START=0; shift ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) die "bilinmeyen seçenek: $1" ;;
    esac
done

command -v systemctl >/dev/null || die "systemd yok; sunucuyu elle çalıştırın: nexremote-http"

if [[ $UNINSTALL == 1 ]]; then
    systemctl --user disable --now "$NAME.service" 2>/dev/null || true
    rm -f "$UNIT_DIR/$NAME.service" "$CONF/http.env"
    systemctl --user daemon-reload
    echo "✓ $NAME kaldırıldı (token dosyası duruyor: $CONF/http.token)"
    exit 0
fi

[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT > 0 && PORT < 65536 )) || die "geçersiz port: $PORT"
[[ -n "$CERT" && -z "$KEY" || -z "$CERT" && -n "$KEY" ]] && die "--tls-cert ve --tls-key birlikte verilmeli"
for f in "$CERT" "$KEY"; do [[ -z "$f" || -r "$f" ]] || die "okunamıyor: $f"; done
PY="$(command -v python3)" || die "python3 yok"

umask 077
mkdir -p "$CONF" "$UNIT_DIR"
if [[ ! -s "$CONF/http.token" ]]; then
    "$PY" "$ROOT/adapters/http/server.py" --gen-token > "$CONF/http.token"
    echo "✓ yeni token üretildi: $CONF/http.token"
fi
chmod 600 "$CONF/http.token"
{
    echo "NEXREMOTE_HOST=$HOST"
    echo "NEXREMOTE_PORT=$PORT"
    echo "NEXREMOTE_TOKEN_FILE=$CONF/http.token"
    [[ -n "$CERT" ]] && echo "NEXREMOTE_TLS_CERT=$CERT" && echo "NEXREMOTE_TLS_KEY=$KEY"
    true
} > "$CONF/http.env"

umask 022
sed -e "s|@ROOT@|$ROOT|g" -e "s|@PYTHON@|$PY|g" "$ROOT/deploy/$NAME.service" \
    > "$UNIT_DIR/$NAME.service"
systemctl --user daemon-reload
echo "✓ birim kuruldu: $UNIT_DIR/$NAME.service"

if [[ $START == 1 ]]; then
    systemctl --user enable --now "$NAME.service" >/dev/null 2>&1
    systemctl --user restart "$NAME.service"
    for _ in $(seq 30); do
        systemctl --user is-active --quiet "$NAME.service" && \
            curl -fsk "http${CERT:+s}://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
        sleep 0.3
    done
    if ! curl -fsk "http${CERT:+s}://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        journalctl --user -u "$NAME" -n 20 --no-pager >&2 || true
        die "servis yanıt vermiyor (yukarıdaki günlük)"
    fi
    echo "✓ çalışıyor: http${CERT:+s}://$HOST:$PORT  (oturum açılınca kendiliğinden başlar)"
    echo "  deneme: curl${CERT:+ -k} -H \"Authorization: Bearer \$(cat $CONF/http.token)\" http${CERT:+s}://127.0.0.1:$PORT/caps"
fi
