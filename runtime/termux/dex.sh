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
# Enrutado del mouse FISICO Bluetooth: 1 = activo por defecto.
MOUSE_ROUTE="${MOUSE_ROUTE:-1}"

log(){ printf '[dex] %s %s\n' "$(date '+%F %T')" "$*"; }
bridge_up(){ curl -sk --max-time 2 "https://${WTM_BIND_HOST:-127.0.0.1}:$PORT/healthz" >/dev/null 2>&1; }
stream_ready(){
    curl -sk --max-time 2 "https://${WTM_BIND_HOST:-127.0.0.1}:$PORT/healthz" 2>/dev/null |
        python3 -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("scrcpy_alive") else 1)' \
        >/dev/null 2>&1
}
find_phone(){
    local matches="" serial model hw seen=" "
    for serial in $("$ADB" devices | awk 'NR > 1 && $2 == "device" {print $1}'); do
        model=$("$ADB" -s "$serial" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
        if [ "$model" = "${WTM_PHONE_MODEL:-SM-S931B}" ]; then
            # El mismo telefono puede aparecer por dos transportes (p.ej.
            # 127.0.0.1:5555 y emulator-5554). Se deduplica por serial de
            # hardware para no declarar ambiguedad con un solo telefono.
            hw=$("$ADB" -s "$serial" shell getprop ro.serialno 2>/dev/null | tr -d '\r')
            [ -n "$hw" ] || hw="$serial"
            case "$seen" in *" $hw "*) continue ;; esac
            seen+="$hw "
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

# --- P3 (hueco H3): escaneo con reintentos. El daemon de depuracion inalambrica
# rota su puerto durante ~1 min tras el boot; una sola pasada pierde la carrera.
if [ -z "$PHONE" ]; then
    for scan_round in $(seq 1 6); do
        for entry in $(python3 find-adb-port.py 2>/dev/null || true); do
            "$ADB" connect "$entry" >/dev/null 2>&1 || true
            PHONE=$(find_phone || true)
            [ -n "$PHONE" ] && break
        done
        [ -n "$PHONE" ] && break
        log "Escaneo ADB ronda $scan_round/6 sin resultado; esperando 5s."
        sleep 5
    done
fi
if [ -z "$PHONE" ]; then
    log "No se encontro el S25 tras 6 escaneos. Usa el widget 'recuperar-dex' con el IP:puerto de la pantalla."
    exit 1
fi
log "Telefono confirmado por modelo: $PHONE"

# --- P2 (hueco H2): sembrar adbd en TCP 5555 y migrar a loopback estable.
# El puerto rotativo muere con la radio; el 5555 es estable mientras adbd viva.
if [ "$PHONE" != "127.0.0.1:5555" ]; then
    "$ADB" -s "$PHONE" tcpip 5555 >/dev/null 2>&1 \
        || log "Aviso: 'tcpip 5555' fallo; se continua con $PHONE."
    sleep 3
    "$ADB" connect 127.0.0.1:5555 >/dev/null 2>&1 || true
    NEW_PHONE=$(find_phone 2>/dev/null | grep -Fx '127.0.0.1:5555' || true)
    if [ -n "$NEW_PHONE" ]; then
        log "Sesion migrada a loopback estable (127.0.0.1:5555)."
        PHONE="127.0.0.1:5555"
    fi
fi

if ! SCRCPY_PID=$(scrcpy_pid) || [ -z "$SCRCPY_PID" ]; then
    log "scrcpy no estaba activo; iniciandolo."
    NEW_DISPLAY="$NEW_DISPLAY" ./scrcpy-phone.sh
else
    log "scrcpy ya esta activo (PID $SCRCPY_PID); se conserva el display existente."
fi

# --- P1 (hueco H1): pre-flight de red. Si la IP del hotspot cambio tras un
# reboot, server.env apunta a una IP muerta y el bridge muere en el bind.
# Re-apuntar config + regenerar cert ANTES de levantar el bridge.
# swlan0 (hotspot) primero, wlan0 (WiFi router) de respaldo.
# Con WTM_BIND_HOST=0.0.0.0 el listener cubre todas las interfaces: reescribir
# a la IP del hotspot romperia a la tablet que entra por el router (2026-11-25).
if [ "$WTM_BIND_HOST" = "0.0.0.0" ]; then
    CUR_IP="$WTM_BIND_HOST"
else
    CUR_IP=$("$ADB" -s "$PHONE" shell "ip -4 addr show swlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print \$2}'" 2>/dev/null | tr -d '\r' || true)
    [ -z "$CUR_IP" ] && CUR_IP=$("$ADB" -s "$PHONE" shell "ip -4 addr show wlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print \$2}'" 2>/dev/null | tr -d '\r' || true)
fi
if [ -n "$CUR_IP" ] && [ "$CUR_IP" != "$WTM_BIND_HOST" ]; then
    log "IP cambio (${WTM_BIND_HOST} -> ${CUR_IP}); regenerando config y certificado."
    bash ./update-network.sh "$CUR_IP" || log "Aviso: update-network.sh fallo; se continua con la config actual."
    set -a; . "$CONFIG"; set +a
fi

if ! bridge_up; then
    log "Bridge no responde; iniciandolo."
    BACKEND=scrcpy PORT="$PORT" FPS="${FPS:-60}" \
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

# --- P4 (bug b): swlan0 (hotspot) ANTES que wlan0; antes imprimia la IP del
# router como URL para la tablet.
IP=$("$ADB" -s "$PHONE" shell "ip -4 addr show swlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print \$2}'" 2>/dev/null | tr -d '\r')
[ -z "$IP" ] && IP=$("$ADB" -s "$PHONE" shell "ip -4 addr show wlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print \$2}'" 2>/dev/null | tr -d '\r')
log "LISTO: bridge y scrcpy activos. Tablet: https://${IP:-<IP>}:$PORT/#auto"

# --- Enrutado del mouse FISICO Bluetooth --------------------------------------
if [ "$MOUSE_ROUTE" != "1" ]; then
    log "MOUSE_ROUTE distinto de 1; el mouse fisico seguira solo en la pantalla del telefono."
    exit 0
fi

if [ -x "$PWD/route-input.sh" ]; then
    associated=0
    for attempt in $(seq 1 10); do
        if out=$(MOUSE_DESCRIPTOR="${MOUSE_DESCRIPTOR:-}" "$PWD/route-input.sh" 2>&1); then
            printf '%s\n' "$out" | sed 's/^/[dex] /'
            log "asociacion nativa del mouse aplicada (intento $attempt)."
            associated=1
            break
        fi
        rc=$?
        case "$rc" in
            4)  sleep 1 ;;
            5)  sleep 1 ;;
            3)  printf '%s\n' "$out" | sed 's/^/[dex] /'
                log "API oculta de asociacion ausente; se usara el enrutador de respaldo."
                break ;;
            6)  printf '%s\n' "$out" | sed 's/^/[dex] /'
                log "jars de asociacion ausentes; se usara el enrutador de respaldo."
                break ;;
            *)  printf '%s\n' "$out" | sed 's/^/[dex] /'
                [ "$attempt" -lt 10 ] && sleep 1 ;;
        esac
    done
    if [ "$associated" = "1" ]; then
        # Asociacion nativa activa: el respaldo por grab/captura no es necesario.
        if [ -r "$TMPDIR/rd-mouse-route.pid" ]; then
            mpid=$(tr -cd '0-9' <"$TMPDIR/rd-mouse-route.pid")
            if [ -n "$mpid" ] && kill -0 "$mpid" 2>/dev/null; then
                kill -TERM "$mpid" 2>/dev/null || true
                log "respaldo mouse-route detenido (PID $mpid): asociacion nativa aplicada."
            fi
            rm -f "$TMPDIR/rd-mouse-route.pid"
        fi
        exit 0
    fi
else
    log "route-input.sh no presente; se usara el enrutador de respaldo."
fi

# RESPALDO: mouse-route.py (getevent -> bridge). En algunos dispositivos el
# acceso a /dev/input puede no estar permitido; si route-input.sh existe,
# se mantiene tambien un reintento nativo en segundo plano por si el mouse
# se conecta mas tarde o la asociacion tarda en ser posible.
if [ -f "$PWD/mouse-route.py" ]; then
    if [ -r "$TMPDIR/rd-mouse-route.pid" ] && kill -0 "$(tr -cd '0-9' <"$TMPDIR/rd-mouse-route.pid" 2>/dev/null)" 2>/dev/null; then
        log "enrutador de mouse ya estaba activo; no se duplica."
    else
        nohup "$PREFIX/bin/python3" "$PWD/mouse-route.py" >>"$TMPDIR/rd-mouse.log" 2>&1 &
        echo "$!" >"$TMPDIR/rd-mouse-route.pid"
        log "enrutador de respaldo iniciado (log: $TMPDIR/rd-mouse.log)."
    fi
else
    log "mouse-route.py no presente; el mouse fisico seguira solo en la pantalla del telefono."
fi

if [ -x "$PWD/route-input.sh" ]; then
    (
        while [ ! -f "$TMPDIR/rd-dex-stopped" ]; do
            sleep 5
            if out=$(MOUSE_DESCRIPTOR="${MOUSE_DESCRIPTOR:-}" "$PWD/route-input.sh" 2>&1); then
                printf '%s\n' "$out" | sed 's/^/[dex-bg] /'
                log "asociacion nativa del mouse aplicada en segundo plano."
                if [ -r "$TMPDIR/rd-mouse-route.pid" ]; then
                    mpid=$(tr -cd '0-9' <"$TMPDIR/rd-mouse-route.pid")
                    if [ -n "$mpid" ] && kill -0 "$mpid" 2>/dev/null; then
                        kill -TERM "$mpid" 2>/dev/null || true
                    fi
                    rm -f "$TMPDIR/rd-mouse-route.pid"
                fi
                exit 0
            fi
            rc=$?
            case "$rc" in
                3|6) exit 0 ;;
            esac
        done
    ) >/dev/null 2>&1 &
fi
