#!/data/data/com.termux/files/usr/bin/bash
# update-network.sh — re-detect the LAN IP and refresh the listener config.
# Útil cuando el teléfono cambia de red/IP (DHCP, hotspot, otra casa): el
# servidor escucha solo en la IP configurada en la instalación.
set -euo pipefail

export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr

CONFIG="$HOME/.config/wtm/server.env"
log(){ printf '[wtm-network] %s %s\n' "$(date '+%F %T')" "$*"; }

[ -f "$CONFIG" ] || { log "No existe $CONFIG; ejecuta install.sh primero."; exit 1; }

NEW_IP="${1:-}"
if [ -z "$NEW_IP" ]; then
    NEW_IP="$(ip -4 addr show wlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print $2}' | tr -d '\r' || true)"
    [ -z "$NEW_IP" ] && NEW_IP="$(ip -4 addr show swlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print $2}' | tr -d '\r' || true)"
fi
[ -z "$NEW_IP" ] && { log "No se detectó IP; pásala como argumento: $0 192.168.x.y"; exit 1; }

OLD_IP="$(sed -n 's/^WTM_ALLOWED_HOSTS=//p' "$CONFIG" | tr -d '\r')"
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
