#!/data/data/com.termux/files/usr/bin/bash
set -uo pipefail

export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr

DEX_DIR="$HOME/dex"
ADB="$PREFIX/bin/adb"
PORT=1234
LOG="$PREFIX/tmp/rd-stop.log"
STOP_FLAG="$PREFIX/tmp/rd-dex-stopped"
PHONE_MODEL="${WTM_PHONE_MODEL:-SM-S931B}"

mkdir -p "$PREFIX/tmp"
exec >>"$LOG" 2>&1
log(){ printf '[stop-dex] %s %s\n' "$(date '+%F %T')" "$*"; }
notify(){
    if command -v termux-toast >/dev/null 2>&1; then
        if command -v pm >/dev/null 2>&1 && pm path com.termux.api >/dev/null 2>&1; then
            timeout 3 termux-toast "$*" || log "La notificación falló o excedió 3 segundos."
        else
            log "Termux:API no está instalado; no se puede mostrar un toast."
        fi
    else
        log "termux-toast no está disponible."
    fi
}

log "Solicitando detención del bridge y del display scrcpy."
printf '1\n' >"$STOP_FLAG" || { log "No se pudo escribir $STOP_FLAG"; notify "No se pudo detener DeX: error al crear la marca."; exit 1; }

remote_scrcpy_pid(){
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
remote_pid_is_scrcpy(){
    local pid="$1" cmd
    cmd=$("$ADB" -s "$PHONE" shell "cat /proc/$pid/cmdline" 2>/dev/null | tr '\000' ' ' | tr -d '\r' || true)
    case "$cmd" in *com.genymobile.scrcpy.Server*) return 0 ;; esac
    return 1
}
local_pid_is_active(){
    local state
    state=$(ps -p "$1" -o stat= 2>/dev/null | tr -d '[:space:]')
    case "$state" in ""|Z*) return 1 ;; esac
    return 0
}

PHONE=""
SCRCPY_PID=""
for serial in $("$ADB" devices | awk 'NR > 1 && $2 == "device" {print $1}'); do
    model=$("$ADB" -s "$serial" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
    if [ "$model" = "$PHONE_MODEL" ]; then
        PHONE="$serial"
        break
    fi
done

if [ -n "$PHONE" ]; then
    SCRCPY_PID=$(remote_scrcpy_pid || true)
    "$ADB" -s "$PHONE" forward --remove "tcp:$PORT" >/dev/null 2>&1 || true
    if [ -n "$SCRCPY_PID" ]; then
        log "Enviando TERM al proceso scrcpy identificado (PID $SCRCPY_PID)."
        "$ADB" -s "$PHONE" shell "kill -TERM $SCRCPY_PID" >/dev/null 2>&1 || true
        sleep 2
    fi
else
    log "S25 no encontrado por ADB; se detendrá el bridge local igualmente."
fi

BRIDGE_PIDS=""
if [ -r "$PREFIX/tmp/rd-bridge.pid" ]; then
    pid=$(tr -cd '0-9' <"$PREFIX/tmp/rd-bridge.pid")
    if [ -n "$pid" ]; then
        args=$(tr '\000' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)
        cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || true)
        case "$args" in
            *"server.py"*) [ "$cwd" = "$DEX_DIR" ] && BRIDGE_PIDS=" $pid" ;;
        esac
    fi
fi

if [ -z "$BRIDGE_PIDS" ]; then
    for proc in /proc/[0-9]*; do
        pid=${proc##*/}
        [ "$pid" = "$$" ] && continue
        [ "$(readlink "$proc/cwd" 2>/dev/null)" = "$DEX_DIR" ] || continue
        args=$(tr '\000' ' ' <"$proc/cmdline" 2>/dev/null || true)
        case "$args" in
            *"server.py"*) BRIDGE_PIDS="$BRIDGE_PIDS $pid" ;;
        esac
    done
fi

if [ -n "$BRIDGE_PIDS" ]; then
    for pid in $BRIDGE_PIDS; do
        log "Enviando TERM al bridge PID $pid."
        kill -TERM "$pid" 2>/dev/null || true
    done
fi

if [ -n "$SCRCPY_PID" ] && [ -n "$PHONE" ] && remote_pid_is_scrcpy "$SCRCPY_PID"; then
    log "El proceso scrcpy sigue activo tras cerrar el forward."
    notify "DeX: el bridge se detuvo, pero scrcpy sigue activo; revisa $LOG."
    exit 1
fi
rm -f "$PREFIX/tmp/rd-scrcpy.pid"
if [ -n "$PHONE" ] && "$ADB" -s "$PHONE" shell "dumpsys display | grep -F scrcpy" >/dev/null 2>&1; then
    log "scrcpy terminó, pero sigue registrado un display virtual con nombre scrcpy."
    notify "DeX se detuvo, pero quedó un display virtual; revisa $LOG."
    exit 1
fi

if [ -n "$BRIDGE_PIDS" ]; then
    for pid in $BRIDGE_PIDS; do
        for _ in $(seq 1 5); do
            local_pid_is_active "$pid" || break
            sleep 1
        done
        if local_pid_is_active "$pid"; then
            log "El bridge PID $pid sigue activo tras TERM."
            notify "DeX no se detuvo por completo; revisa $LOG."
            exit 1
        fi
    done
fi
rm -f "$PREFIX/tmp/rd-bridge.pid"

if command -v termux-wake-unlock >/dev/null 2>&1; then
    timeout 3 termux-wake-unlock || log "No se pudo liberar el wake-lock o excedió 3 segundos."
fi

log "Bridge detenido; marca de parada guardada."
notify "DeX detenido."
