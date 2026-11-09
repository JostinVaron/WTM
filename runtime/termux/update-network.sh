#!/data/data/com.termux/files/usr/bin/bash
# update-network.sh — re-detect the LAN IP and refresh the listener config.
# Útil cuando el teléfono cambia de red/IP (DHCP, hotspot, otra casa): el
# servidor escucha solo en la IP configurada en la instalación.
#
# Detección de IP (orden de prioridad, hallazgo de campo 2026-11-09):
#   1) Argumento explícito ($1) — siempre gana.
#   2) swlan0 (hotspot) vía `adb shell ip` — el usuario shell de Android SÍ
#      puede leer netlink; el `ip` de Termux falla con "Permission denied"
#      en Android moderno, así que la consulta se hace a través de adbd.
#      swlan0 se consulta ANTES que wlan0: en la arquitectura oficial del
#      proyecto (hotspot como transporte), un teléfono con hotspot activo
#      y Wi-Fi de casa conectado a la vez tiene AMBAS interfaces con IP,
#      y la del hotspot es la que debe usar el servicio.
#   3) wlan0 (Wi-Fi normal) vía `adb shell ip`.
#   4) Fallback UDP de Termux (ruta por defecto) — ÚLTIMO recurso: con
#      hotspot + Wi-Fi + datos activos devuelve la IP de la ruta por
#      defecto (Wi-Fi de casa / datos móviles), no la del hotspot.
set -euo pipefail

export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr

CONFIG="$HOME/.config/wtm/server.env"
ADB="$PREFIX/bin/adb"
LOOPBACK_SERIAL="127.0.0.1:5555"
log(){ printf '[wtm-network] %s %s\n' "$(date '+%F %T')" "$*"; }

[ -f "$CONFIG" ] || { log "No existe $CONFIG; ejecuta install.sh primero."; exit 1; }

NEW_IP="${1:-}"
if [ -z "$NEW_IP" ]; then
    # swlan0 primero (hotspot = transporte oficial), consultado vía adb shell
    # porque el `ip` de Termux no tiene permiso de netlink en Android moderno.
    if "$ADB" devices 2>/dev/null | grep -q "^$LOOPBACK_SERIAL[[:space:]].*device$"; then
        NEW_IP="$("$ADB" -s "$LOOPBACK_SERIAL" shell "ip -4 addr show swlan0 2>/dev/null" 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print $2}' | tr -d '\r' || true)"
        [ -z "$NEW_IP" ] && NEW_IP="$("$ADB" -s "$LOOPBACK_SERIAL" shell "ip -4 addr show wlan0 2>/dev/null" 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print $2}' | tr -d '\r' || true)"
    fi
    # Último recurso: ruta por defecto (falla la prioridad de hotspot; avisa).
    if [ -z "$NEW_IP" ]; then
        NEW_IP="$("$PREFIX/bin/python3" -c 'import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    if ip and not ip.startswith("0.") and ip != "127.0.0.1":
        print(ip)
except Exception:
    pass')"
        [ -n "$NEW_IP" ] && log "AVISO: IP detectada por el fallback de ruta por defecto ($NEW_IP);"
        [ -n "$NEW_IP" ] && log "  puede no ser la del hotspot. Pásala explícita: $0 <ip>"
    fi
fi
[ -z "$NEW_IP" ] && { log "No se detectó IP; pásala como argumento: $0 192.168.x.y"; exit 1; }

OLD_IP="$(sed -n 's/^WTM_ALLOWED_HOSTS=//p' "$CONFIG" | head -1 | tr -d '\r')"
if [ "$OLD_IP" = "$NEW_IP" ]; then
    log "La IP sigue siendo $NEW_IP; no hay cambios."
    exit 0
fi

# Reescribir solo las líneas de host permitido y bind; conservar el resto.
TMP_FILE="$(mktemp)"
sed -e "s/^WTM_ALLOWED_HOSTS=.*/WTM_ALLOWED_HOSTS=$NEW_IP/" \
    -e "s/^WTM_BIND_HOST=.*/WTM_BIND_HOST=$NEW_IP/" \
    "$CONFIG" >"$TMP_FILE"
mv "$TMP_FILE" "$CONFIG"
chmod 600 "$CONFIG"
log "Configuración actualizada: $OLD_IP -> $NEW_IP"

# El certificado se generó con SAN=IP de la IP original; regenerarlo para la nueva.
openssl req -x509 -newkey rsa:2048 \
    -keyout "$HOME/.config/wtm/key.pem" \
    -out "$HOME/.config/wtm/cert.pem" \
    -days 825 -nodes -subj "/CN=$NEW_IP" \
    -addext "subjectAltName=IP:$NEW_IP,DNS:localhost,IP:127.0.0.1" \
    >/dev/null 2>&1 || { log "No se pudo regenerar el certificado; conserva el anterior."; }
chmod 600 "$HOME/.config/wtm/key.pem" 2>/dev/null || true
chmod 644 "$HOME/.config/wtm/cert.pem" 2>/dev/null || true
FINGERPRINT="$(python3 - "$HOME/.config/wtm/cert.pem" <<'PY'
import hashlib, sys
digest = hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest().upper()
print(":".join(digest[i:i+2] for i in range(0, 64, 2)))
PY
)"
printf '%s\n' "$FINGERPRINT" >"$HOME/.config/wtm/cert-fingerprint.txt"
log "Nuevo certificado. Huella SHA-256: $FINGERPRINT"

if [ -r "$PREFIX/tmp/rd-bridge.pid" ]; then
    pid=$(tr -cd '0-9' <"$PREFIX/tmp/rd-bridge.pid")
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "Reiniciando el bridge (PID $pid) para aplicar la nueva IP."
        kill -TERM "$pid" 2>/dev/null || true
        sleep 2
    fi
fi
log "Listo. Inicia de nuevo el servicio o deja que el watchdog lo haga."
log "Tablet: https://$NEW_IP:8443/ — verifica la nueva huella del certificado."
