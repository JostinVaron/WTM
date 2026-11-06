#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
export TMPDIR="${TMPDIR:-$PREFIX/tmp}"

cd "$(dirname "$0")"
CONFIG="$HOME/.config/wtm/server.env"
if [ -r "$CONFIG" ]; then
    set -a
    . "$CONFIG"
    set +a
fi
: "${WTM_AUTH_FILE:=$HOME/.config/wtm/auth-tokens.json}"
: "${CERT:=$HOME/.config/wtm/cert.pem}"
: "${KEY:=$HOME/.config/wtm/key.pem}"
: "${WTM_BIND_HOST:=127.0.0.1}"
: "${WTM_ALLOWED_HOSTS:?Set WTM_ALLOWED_HOSTS in $CONFIG to the approved HTTPS host of the phone}"
: "${WTM_PHONE_MODEL:=SM-S931B}"
ADB="$PREFIX/bin/adb"
NEW_DISPLAY="${NEW_DISPLAY:-2000x1200/240}"
PORT="${PORT:-8443}"
LOG="$PREFIX/tmp/rd-bridge.log"

log(){ printf '[dex] %s %s\n' "$(date '+%F %T')" "$*"; }
bridge_up(){ curl -sk --max-time 2 "https://${WTM_BIND_HOST:-127.0.0.1}:$PORT/healthz" >/dev/null 2>&1; }
stream_ready(){
    curl -sk --max-time 2 "https://${WTM_BIND_HOST:-127.0.0.1}:$PORT/healthz" 2>/dev/null |
        python3 -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("scrcpy_alive") else 1)' \
            >/dev/null 2>&1
}
find_phone(){
    local matches="" serial model
    for serial in $("$ADB" devices | awk 'NR > 1 && $2 == "device" {print $1}'); do
        model=$("$ADB" -s "$serial" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
        if [ "$model" = "${WTM_PHONE_MODEL:-SM-S931B}" ]; then
            [ -z "$matches" ] || matches+=$'\n'
            matches+="$serial"
        fi
    done
    [ -n "$matches" ] || return 1
    [ "$(printf '%s\n' "$matches" | wc -l)" -eq 1 ] || {
        log "Hay varios dispositivos ${WTM_PHONE_MODEL:-SM-S931B}; configura el serial para evitar seleccionar uno ambiguo."
        return 1
    }
    printf '%s\n' "$matches"
}
scrcpy_pid(){
    local pid cmd
    if [ -r "$PREFIX/tmp/rd-scrcpy.pid" ]; then
        pid=$(tr -cd '0-9' <"$PREFIX/tmp/rd-scrcpy.pid")
        if [ -n "$pid" ]; then
            cmd=$("$ADB" -s "$PHONE" shell "cat /proc/$pid/cmdline" 2>/dev/null | tr '\000' ' ' | tr -d '\r' || true)
            case "$cmd" in
                *com.genymobile.scrcpy.Server*) printf '%s\n' "$pid"; return 0 ;;
            esac
        fi
    fi
    for pid in $("$ADB" -s "$PHONE" shell 'ps -A' | awk '$NF == "app_process" {print $2}' | tr -d '\r'); do
        cmd=$("$ADB" -s "$PHONE" shell "cat /proc/$pid/cmdline" 2>/dev/null | tr '\000' ' ' | tr -d '\r' || true)
        case "$cmd" in
            *com.genymobile.scrcpy.Server*) printf '%s\n' "$pid"; return 0 ;;
        esac
    done
    return 1
}

mkdir -p "$TMPDIR"
rm -f "$TMPDIR/rd-dex-stopped"
PHONE=$(find_phone || true)
if [ -z "$PHONE" ]; then
    for port in $(python3 find-adb-port.py 2>/dev/null || true); do
        "$ADB" connect "127.0.0.1:$port" >/dev/null 2>&1 || continue
        PHONE=$(find_phone || true)
        [ -n "$PHONE" ] && break
    done
fi
if [ -z "$PHONE" ]; then
    log "No se encontró el S25 por ADB. No se modificó la depuración inalámbrica."
    exit 1
fi
log "Teléfono confirmado por modelo: $PHONE"

if ! SCRCPY_PID=$(scrcpy_pid) || [ -z "$SCRCPY_PID" ]; then
    log "scrcpy no estaba activo; iniciándolo."
    NEW_DISPLAY="$NEW_DISPLAY" ./scrcpy-phone.sh
else
    log "scrcpy ya está activo (PID $SCRCPY_PID); se conserva el display existente."
fi

if ! bridge_up; then
    log "Bridge no responde; iniciándolo."
    BACKEND=scrcpy PORT="$PORT" FPS=60 \
        WTM_AUTH_FILE="$WTM_AUTH_FILE" CERT="$CERT" KEY="$KEY" \
        WTM_BIND_HOST="$WTM_BIND_HOST" WTM_ALLOWED_HOSTS="$WTM_ALLOWED_HOSTS" \
        nohup python3 server.py >"$LOG" 2>&1 &
    echo "$!" >"$TMPDIR/rd-bridge.pid"
fi

ready_samples=0
for _ in $(seq 1 30); do
    SCRCPY_PID=$(scrcpy_pid || true)
    if [ -n "$SCRCPY_PID" ] && stream_ready; then
        ready_samples=$((ready_samples + 1))
        if [ "$ready_samples" -ge 3 ]; then
            break
        fi
    else
        ready_samples=0
    fi
    sleep 1
done
if [ "$ready_samples" -lt 3 ]; then
    log "No hubo 3 verificaciones consecutivas de scrcpy y bridge dentro de 30 segundos."
    tail -n 40 "$LOG" 2>/dev/null || true
    exit 1
fi

IP=$("$ADB" -s "$PHONE" shell "ip -4 addr show wlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print \$2}'" 2>/dev/null | tr -d '\r')
[ -z "$IP" ] && IP=$("$ADB" -s "$PHONE" shell "ip -4 addr show swlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print \$2}'" 2>/dev/null | tr -d '\r')
log "LISTO: bridge y scrcpy activos. Tablet: https://${IP:-<IP>}:$PORT/#auto"
