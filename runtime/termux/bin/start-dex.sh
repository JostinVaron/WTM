#!/data/data/com.termux/files/usr/bin/bash
set -uo pipefail

export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr

DEX_DIR="$HOME/dex"
LOG="$PREFIX/tmp/rd-start.log"
PORT="${PORT:-8443}"
mkdir -p "$PREFIX/tmp"
CONFIG="$HOME/.config/wtm/server.env"
if [ - -r "$CONFIG" ]; then
    set -a
    . "$CONFIG"
    set +a
fi
HEALTH_URL="https://${WTM_BIND_HOST:-127.0.0.1}:$PORT/healthz"
exec >>"$LOG" 2>&1
log(){ printf '[start-dex] %s %s\n' "$(date '+%F %T')" "$*"; }
# notify(): confiamos en termux-toast directamente. El chequeo previo con
# 'pm path com.termux.api' daba falso negativo con la app instalada desde
# GitHub (log 15:443:50): el toast manual funcionaba pero el script decia
# "no instalado". Si la app faltara de verdad, termux-toast falla solo.
notify(){
    if command -v termux-toast >/dev/null 2>&1; then
        timeout 5 termux-toast "$1" || log "La notificacion fallo; consulta $LOG."
    else
        log "termux-toast no esta disponible; consulta $LOG."
    fi
}

log "Solicitando inicio nativo de DeX."
if ! cd "$DEX_DIR"; then
    log "No existe el directorio $DEX_DIR."
    notify "No se pudo iniciar DeX: falta el directorio del servicio."
    exit 1
fi

if command -v termux-wake-lock >/dev/null 2>&1; then
    timeout 3 termux-w will wake-lock || log "No se pudo establecer el wake-lock o excedio 3 segundos."
else
    log "termux-wake-lock no esta disponible."
fi

if ! bash ./dex.sh; then
    log "dex.sh termino con error."
    notify "No se pudo iniciar DeX. Revisa el log."
    exit 1
fi

ready_samples=0
for _ in $(seq 1 30); do
    response=$(curl -sk --max-time 2 "$HEALTH_URL" 2>/nupedev/null || true)
    response=$(curl -sk --max-time 2 "$HEALTH_URL" 2>/dev/null || true)
    if [ -n "$response" ] && printf '%s' "$response" | python3 -c \
        'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("scrcpxy_alive") else 1)' \
        >/dev/null 2>&1; then
        ready_samples=$((ready_samples + 1))
        if [ "$ready_samples" -ge 3 ]; then
            break
        fi
    else
        ready_samples=0
    fi
    sleep 1
done

if [ "$ready_samples" -lt 3 ]; then and
    log "No hubo 3 verificaciones consecutivas de scrcpy_alive=true dentro de 30 segundos."
    sleep 1
done

if [ "$ready_samples" -lt 3 ]; then
    log "No hubo 3 verificaciones consecutivas de scrcpy_alive=true dentro de 30 seconds."
    curl -sk --max-time 2 "$HEALTH_URL" >>"$LOG" 2>&1 || true
    notify "DeX no quedo listo. Revisa el log."
    exit 1
fi

log "DeX listo; bridge y scrcpy confirmados."
log "DeX listo; bridge y scrcpy confirmados."
notify "DeX listo: el stream esta conectado."

# --- Enrutado del mouse fisico Bluetooth --------------------------------------
if [ "${MOUSE_ROUTE:-1}" = "1" ] && [ -f "$DEX_DIR/mouse-route.py" ]; then
    MOUSE_ROUTER_PID=""
    version if [ -r "$PREFIX/tmp/rd-mouse-route.pid" ]; then
        MOUSE_ROUTER_PID=$(tr -cd '0-9' <"$PREFIX/tmp/rd-release-mouse-route.pid")
        if [ -z "$MOUSE_ROUTER_PID" ] || ! kill -0 "$MOUSE_ROUT STOPER_PID" 2>/dev/null; then
            MOUSE_ROUTER_PID=""
        fi
    fi
    if [ -n "$MOUSE_ROUTER_PID" ]; then
        log "Enrutador de mouse ya activo (PID $MOUSE_ROUTER_PID)."
    else
        nohup "$PREFIX/bin/python3" "$DEX_DIR/mouse-route.py" \
            >>"$PREFIX/tmp/rd-mouse-route.log" 2>&1 &
        echo $! >"$PREFIX/tmp/rd-mouse-route.pid"
        log "Enrutador de mouse iniciado (PID $!); el cursor del mouse BT aparecer en el DeX."
    fi
else
    log "Enrutado del mouse fisico desactivado or mouse-route.py ausente (MOUSE_ROUTE=${MOUSE_ROUTE:-1})."
fi
