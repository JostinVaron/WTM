#!/data/data/com.termux/files/usr/bin/bash
# WTM install.sh — instalador idempotente para Termux en el teléfono fuente.
#
# Qué hace:
#  1. Instala las dependencias de Termux (pkg) si faltan.
#  2. Copia las fuentes del paquete a la carpeta de despliegue.
#  3. Descarga el scrcpy-server oficial v5.0.1 y verifica su SHA-256.
#  4. Genera credenciales ÚNICAS por instalación (fuera de este repositorio):
#       - tokens de acceso viewer/admin (256 bits cada uno)
#       - certificado y llave TLS propias
#       - server.env con puerto/host autorizado
#  5. Instala los atajos de Termux:Widget y el arranque de Termux:Boot.
#
# Es idempotente: nunca sobrescribe tokens, TLS o configuración existentes.
# No incluye secretos: todo el material sensible se crea en ~/.config/wtm.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${WTM_PREFIX:-/data/data/com.termux/files/usr}"
HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
INSTALL_DIR="${WTM_INSTALL_DIR:-$HOME_DIR/dex}"
CONFIG_DIR="$HOME_DIR/.config/wtm"
PORT="${WTM_PORT:-8443}"
BIND_HOST="${WTM_BIND_HOST:-}"
PHONE_MODEL="${WTM_PHONE_MODEL:-SM-S931B}"
SCRCPY_VERSION="5.0.1"
AIOHTTP_PIN="3.14.5"
SCRCPY_URL="https://github.com/Genymobile/scrcpy/releases/download/v${SCRCPY_VERSION}/scrcpy-server-v${SCRCPY_VERSION}"
# SHA-256 del artefacto OFICIAL de Genymobile/scrcpy v5.0.1.
SCRCPY_SHA256="764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"

log(){ printf '[wtm-install] %s %s\n' "$(date '+%F %T')" "$*"; }
fail(){ log "ERROR: $*"; exit 1; }

is_termux(){ [ -d "$PREFIX/usr" ] && [ -x "$PREFIX/bin/pkg" ] && [ -d "$HOME_DIR/.termux" ]; }

# --- 0. validación del entorno --------------------------------------------
is_termux || fail "Este instalador debe ejecutarse en Termux en el teléfono fuente."
[ -d "$SOURCE_DIR/web" ] || fail "No se encontró web/ junto a install.sh."

# --- 1. dependencias -------------------------------------------------------
# Nota: en Termux 'openssl' son solo las librerías; el comando es 'openssl-tool'.
# iproute2 se instala por completitud, pero Android moderno restringe los sockets
# netlink para apps normales, así que 'ip' falla con "Permission denied" en
# teléfonos recientes; la detección de IP usa entonces el fallback de Python.
for pkgname in python aiohttp openssl openssl-tool curl iproute2; do
    if ! "$PREFIX/bin/pkg" list-installed 2>/dev/null | grep -q "^$pkgname\$"; then
        log "Instalando dependencia: $pkgname"
        "$PREFIX/bin/pkg" install -y "$pkgname" >/dev/null || fail "No se pudo instalar $pkgname."
    fi
done
command -v "$PREFIX/bin/python3" >/dev/null 2>&1 || fail "python3 no está disponible."
if ! "$PREFIX/bin/python3" -c 'import aiohttp' >/dev/null 2>&1; then
    log "Instalando aiohttp $AIOHTTP_PIN con pip (versión fijada)."
    "$PREFIX/bin/pip" install "aiohttp==${AIOHTTP_PIN}" >/dev/null 2>&1 || "$PREFIX/bin/pip3" install "aiohttp==${AIOHTTP_PIN}" || \
        fail "No se pudo instalar aiohttp $AIOHTTP_PIN."
fi

# --- 2. IP autorizada ------------------------------------------------------
if [ -n "${WTM_ALLOWED_HOSTS:-}" ]; then
    ALLOWED_HOSTS="$WTM_ALLOWED_HOSTS"
else
    ALLOWED_HOSTS="$(ip -4 addr show wlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print $2}' | tr -d '\r' || true)"
    [ -z "$ALLOWED_HOSTS" ] && ALLOWED_HOSTS="$(ip -4 addr show swlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1 | awk '{print $2}' | tr -d '\r' || true)"
    if [ -z "$ALLOWED_HOSTS" ]; then
        # Fallback sin netlink: un socket UDP no envía paquetes, solo consulta
        # al enrutador de Android qué IP local usaría hacia 8.8.8.8. Funciona
        # en Android moderno donde netlink está restringido para apps.
        ALLOWED_HOSTS="$("$PREFIX/bin/python3" - <<'PY'
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    if ip and not ip.startswith("0.") and ip != "127.0.0.1":
        print(ip)
except Exception:
    pass
PY
)"
    fi
    [ -z "$ALLOWED_HOSTS" ] && fail "No se detectó una IP LAN; define WTM_ALLOWED_HOSTS manualmente."
fi
log "IP autorizada para el navegador: $ALLOWED_HOSTS"
if [ -z "$BIND_HOST" ]; then
    BIND_HOST="$ALLOWED_HOSTS"
    log "El servicio escuchará solo en $BIND_HOST (para cambiarlo, define WTM_BIND_HOST)."
fi

# --- 3. copiar fuentes (idempotente) ---------------------------------------
mkdir -p "$INSTALL_DIR/web" "$INSTALL_DIR/jvm"
for item in server.py dex.sh scrcpy-phone.sh watchdog.sh find-adb-port.py start_phone.sh update-network.sh; do
    [ -f "$SOURCE_DIR/$item" ] || fail "Falta $item en el paquete."
    if ! cmp -s "$SOURCE_DIR/$item" "$INSTALL_DIR/$item" 2>/dev/null; then
        cp "$SOURCE_DIR/$item" "$INSTALL_DIR/$item"
        log "Copiado $item"
    fi
done
for item in index.html panel.html; do
    [ -f "$SOURCE_DIR/web/$item" ] || fail "Falta web/$item en el paquete."
    if ! cmp -s "$SOURCE_DIR/web/$item" "$INSTALL_DIR/web/$item" 2>/dev/null; then
        cp "$SOURCE_DIR/web/$item" "$INSTALL_DIR/web/$item"
        log "Copiado web/$item"
    fi
done
chmod +x "$INSTALL_DIR/dex.sh" "$INSTALL_DIR/scrcpy-phone.sh" \
        "$INSTALL_DIR/watchdog.sh" "$INSTALL_DIR/start_phone.sh" \
        "$INSTALL_DIR/update-network.sh"

# --- 4. scrcpy-server oficial con checksum ---------------------------------
JAR="$INSTALL_DIR/jvm/scrcpy-server"
if [ -f "$JAR" ]; then
    CURRENT="$("$PREFIX/bin/python3" - <<'PY'
import hashlib, sys
try:
    print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
except Exception:
    print("unreadable")
PY
    "$JAR")"
    if [ "$CURRENT" = "$SCRCPY_SHA256" ]; then
        log "scrcpy-server v$SCRCPY_VERSION ya presente con checksum correcto."
    else
        fail "scrcpy-server existente tiene SHA-256 $CURRENT; reemplázalo por el artefacto oficial o bórralo."
    fi
else
    log "Descargando scrcpy-server oficial v$SCRCPY_VERSION..."
    curl -fsSL --max-time 120 -o "$JAR" "$SCRCPY_URL" || fail "No se pudo descargar scrcpy-server."
    ACTUAL="$("$PREFIX/bin/python3" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$JAR")"
    [ "$ACTUAL" = "$SCRCPY_SHA256" ] || { rm -f "$JAR"; fail "SHA-256 del descargado no coincide con el oficial."; }
    log "scrcpy-server verificado (SHA-256 ok)."
fi

# --- 5. credenciales por instalación (nunca se sobrescriben) ---------------
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
AUTH_FILE="$CONFIG_DIR/auth-tokens.json"
if [ ! -f "$AUTH_FILE" ]; then
    VIEWER="$("$PREFIX/bin/python3" -c 'import secrets;print(secrets.token_hex(32))')"
    ADMIN="$("$PREFIX/bin/python3" -c 'import secrets;print(secrets.token_hex(32))')"
    "$PREFIX/bin/python3" - "$AUTH_FILE" "$VIEWER" "$ADMIN" <<'PY'
import json, os, sys
path, viewer, admin = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({"viewer": viewer, "admin": admin}, handle, indent=2)
os.chmod(path, 0o600)
print("token viewer:", viewer)
print("token admin :", admin)
PY
    log "Tokens generados (guardados en $AUTH_FILE, permisos 600)."
    log "Guarda el token admin en un lugar seguro ANTES de continuar."
else
    [ "$(stat -c %a "$AUTH_FILE")" = "600" ] || chmod 600 "$AUTH_FILE"
    log "Se conserva $AUTH_FILE existente."
fi

CERT="$CONFIG_DIR/cert.pem"
KEY="$CONFIG_DIR/key.pem"
FINGERPRINT_FILE="$CONFIG_DIR/cert-fingerprint.txt"
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    log "Generando certificado y llave TLS propios de esta instalación."
    openssl req -x509 -newkey rsa:2048 -keyout "$KEY" -out "$CERT" \
        -days 825 -nodes -subj "/CN=$ALLOWED_HOSTS" \
        -addext "subjectAltName=IP:$ALLOWED_HOSTS,DNS:localhost,IP:127.0.0.1" \
        >/dev/null 2>&1 || fail "No se pudo generar el material TLS."
    chmod 600 "$KEY"; chmod 644 "$CERT"
    log "Material TLS generado en $CONFIG_DIR."
else
    log "Se conserva el material TLS existente en $CONFIG_DIR."
fi
FINGERPRINT="$("$PREFIX/bin/python3" - <<'PY'
import hashlib, sys
digest = hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest().upper()
print(":".join(digest[i:i+2] for i in range(0, 64, 2)))
PY
"$CERT")"
printf '%s\n' "$FINGERPRINT" >"$FINGERPRINT_FILE"
chmod 644 "$FINGERPRINT_FILE"
log "Huella SHA-256 del certificado: $FINGERPRINT"
log "Guarda esta huella: al abrir la página en la tablet, compárala con la mostrada en /login."
log "Si no coincide, NO introduzcas ningún token: la conexión puede estar interceptada."

ENV_FILE="$CONFIG_DIR/server.env"
if [ ! -f "$ENV_FILE" ]; then
    umask 077
    cat >"$ENV_FILE" <<EOF
# Configuración generada por install.sh — no editar a ciegas.
PORT=$PORT
WTM_BIND_HOST=$BIND_HOST
WTM_ALLOWED_HOSTS=$ALLOWED_HOSTS
WTM_AUTH_FILE=$AUTH_FILE
CERT=$CERT
KEY=$KEY
WTM_PHONE_MODEL=$PHONE_MODEL
EOF
    chmod 600 "$ENV_FILE"
    log "Configuración escrita en $ENV_FILE."
else
    log "Se conserva $ENV_FILE existente."
fi

# --- 6. atajos de Termux:Widget y Termux:Boot ------------------------------
if [ -d "$HOME_DIR/.shortcuts" ]; then
    for shortcut in start-dex.sh stop-dex.sh; do
        if ! cmp -s "$SOURCE_DIR/shortcuts/$shortcut" "$HOME_DIR/.shortcuts/$shortcut" 2>/dev/null; then
            cp "$SOURCE_DIR/shortcuts/$shortcut" "$HOME_DIR/.shortcuts/$shortcut"
            chmod +x "$HOME_DIR/.shortcuts/$shortcut"
            log "Atajo instalado: .shortcuts/$shortcut"
        fi
    done
else
    log "No existe .shortcuts (instala Termux:Widget para atajos de un toque)."
fi
if [ -d "$HOME_DIR/.termux" ]; then
    mkdir -p "$HOME_DIR/.termux/boot"
    if ! cmp -s "$SOURCE_DIR/boot/01-dex.sh" "$HOME_DIR/.termux/boot/01-dex.sh" 2>/dev/null; then
        cp "$SOURCE_DIR/boot/01-dex.sh" "$HOME_DIR/.termux/boot/01-dex.sh"
        chmod +x "$HOME_DIR/.termux/boot/01-dex.sh"
        log "Arranque instalado: .termux/boot/01-dex.sh"
    fi
else
    log "No existe .termux (instala Termux:Boot para arranque automático)."
fi

# --- 7. resumen ------------------------------------------------------------
log "Instalación completa en $INSTALL_DIR."
log "Inicia el servicio: bash $HOME_DIR/.shortcuts/start-dex.sh"
log "Abre en la tablet: https://$ALLOWED_HOSTS:$PORT/"
log "En el primer acceso, VERIFICA que la huella del certificado mostrada"
log "en la página coincide con $FINGERPRINT ($FINGERPRINT_FILE)."
log "Si no coincide, no introduzcas tokens y revisa la red."
log "Verificación local: curl -sk https://127.0.0.1:$PORT/healthz"
