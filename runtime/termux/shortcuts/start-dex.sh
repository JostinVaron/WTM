#!/data/data/com.termux/files/usr/bin/bash
set -uo pipefail

export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr

DEX_DIR="$HOME/dex"
LOG="$PREFIX/tmp/rd-start.log"
PORT="${PORT:-8443}"
mkdir -p "$PREFIX/tmp"
# Cargar server.env para conocer el BIND_HOST real del bridge (bug #7):
# sin esto, WTM_BIND_HOST quedaba vacío y el health-check apuntaba a
# 127.0.0.1, donde el servidor endurecido no escucha → falso negativo.
CONFIG="$HOME/.config/wtm/server.env"
if [ -r "$CONFIG" ]; then
    set -a
    . "$CONFIG"
    set +a
fi
HEALTH_URL="https://${WTM_BIND_HOST:-127.0.0.1}:$PORT/healthz"
exec >>"$LOG" 2>&1
log(){ printf '[start-dex] %s %s\n' "$(date '+%F %T')" "$*"; }
notify(){
    if command -v termux-toast >/dev/null 2>&1; then
        if command -v pm >/dev/null 2>&1 && pm path com.termux.api >/dev/null 2>&1; then
            timeout 3 termux-toast "$1" || log "La notificación falló o excedió 3 segundos."
        else
            log "Termux:API no está instalado; no se puede mostrar un toast."
        fi
    else
        log "termux-toast no está disponible; consulta $LOG."
    fi
}

log "Solicitando inicio nativo de DeX."
if ! cd "$DEX_DIR"; then
    log "No existe el directorio $DEX_DIR."
    notify "No se pudo iniciar DeX: falta el directorio del servicio."
    exit 1
fi

if command -v termux-wake-lock >/dev/null 2>&1; then
    timeout 3 termux-wake-lock || log "No se pudo establecer el wake-lock o excedió 3 segundos."
else
    log "termux-wake-lock no está disponible."
fi

if ! bash ./dex.sh; then
    log "dex.sh terminó con error."
    notify "No se pudo iniciar DeX. Revisa $LOG."
    exit 1
fi

ready_samples=0
for _ in $(seq 1 30); do
    response=$(curl -sk --max-time 2 "$HEALTH_URL" 2>/dev/null || true)
    if [ -n "$response" ] && printf '%s' "$response" | python3 -c \
        'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("scrcpy_alive") else 1)' \
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

if [ "$ready_samples" -lt 3 ]; then
    log "No hubo 3 verificaciones consecutivas de scrcpy_alive=true dentro de 30 segundos."
    curl -sk --max-time 2 "$HEALTH_URL" >>"$LOG" 2>&1 || true
    notify "DeX no quedó listo. Revisa $LOG."
    exit 1
fi

log "DeX listo; bridge y scrcpy confirmados."
notify "DeX listo: el stream está conectado."

# --- Enrutado automático del mouse físico Bluetooth del teléfono ------------
# mouse-route.py lee los eventos del mouse emparejado con ESTE teléfono
# (adb shell getevent) y los reenvía al display virtual a través del endpoint
# local /api/mouse del bridge: el cursor aparece dentro del DeX (en la tablet).
# stop-dex.sh lo detiene y el mouse vuelve a comportarse con normalidad en la
# pantalla física del teléfono. Si el mouse está apagado, el enrutador espera
# a que se conecte. Desactivar por completo: MOUSE_ROUTE=0 en server.env.
if [ "${MOUSE_ROUTE:-1}" = "1" ] && [ -f "$DEX_DIR/mouse-route.py" ]; then
    MOUSE_ROUTER_PID=""
    if [ -r "$PREFIX/tmp/rd-mouse-route.pid" ]; then
        MOUSE_ROUTER_PID=$(tr -cd '0-9' <"$PREFIX/tmp/rd-mouse-route.pid")
        if [ -z "$MOUSE_ROUTER_PID" ] || ! kill -0 "$MOUSE_ROUTER_PID" 2>/dev/null; then
            MOUSE_ROUTER_PID=""
        fi
    fi
    if [ -n "$MOUSE_ROUTER_PID" ]; then
        log "Enrutador de mouse ya activo (PID $MOUSE_ROUTER_PID)."
    else
        nohup "$PREFIX/bin/python3" "$DEX_DIR/mouse-route.py" \
            >>"$PREFIX/tmp/rd-mouse-route.log" 2>&1 &
        echo $! >"$PREFIX/tmp/rd-mouse-route.pid"
        log "Enrutador de mouse iniciado (PID $!); el cursor del mouse BT aparecerá en el DeX."
    fi
else
    log "Enrutado del mouse físico desactivado o mouse-route.py ausente (MOUSE_ROUTE=${MOUSE_ROUTE:-1})."
fi
