#!/data/data/com.termux/files/usr/bin/bash
# device-smoke.sh — certificación de funcionamiento en el dispositivo real.
# Ejecutar EN TERMUX, en el teléfono fuente, con el servicio instalado
# (install.sh). Verifica la cadena completa: adb, scrcpy, bridge TLS,
# autenticación, estado y métricas. No modifica el sistema.
#
# Uso: bash device-smoke.sh [admin-token]
# Sin argumento, usa el token admin guardado en ~/.config/wtm/auth-tokens.json
# (requiere permisos del usuario). Salida: PASS/FAIL por verificación y
# resumen final. Guarda evidencia en $PREFIX/tmp/wtm-certification.log
set -uo pipefail

export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr

CONFIG="$HOME/.config/wtm/server.env"
AUTH_FILE="$HOME/.config/wtm/auth-tokens.json"
PORT="${PORT:-8443}"
LOG="$PREFIX/tmp/wtm-certification.log"
RESULTS=""
PASS=0
FAIL=0

[ -f "$CONFIG" ] && { set -a; . "$CONFIG"; set +a; }
[ -f "$AUTH_FILE" ] || { echo "Falta $AUTH_FILE; ejecuta install.sh primero."; exit 2; }

exec >>"$LOG" 2>&1
log(){ printf '[cert] %s %s\n' "$(date '+%F %T')" "$*"; }
record(){ RESULTS="$RESULTS$1:$2\n"; if [ "$1" = PASS ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi; log "$1: $2"; }
check(){ if eval "$2"; then record PASS "$1"; else record FAIL "$1"; fi; }

ADMIN_TOKEN="${1:-}"
[ -z "$ADMIN_TOKEN" ] && ADMIN_TOKEN="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["admin"])' "$AUTH_FILE" 2>/dev/null || true)"
[ -z "$ADMIN_TOKEN" ] && { echo "Pasa el token admin como argumento: bash device-smoke.sh <token>"; exit 2; }

log "=== Certificación WTM $(date -Iseconds) ==="

check "config server.env" '[ -f "$CONFIG" ] && [ "$(stat -c %a "$CONFIG")" = "600" ]'
check "auth-tokens permisos" '[ "$(stat -c %a "$AUTH_FILE")" = "600" ]'
check "tokens distintos y 256-bit" 'python3 - "$AUTH_FILE" <<PY
import json, sys
t = json.load(open(sys.argv[1]))
import re
ok = set(t) == {"viewer", "admin"} and t["viewer"] != t["admin"]
ok = ok and all(re.fullmatch(r"[a-f0-9]{64}", v) for v in t.values())
sys.exit(0 if ok else 1)
PY'
check "material TLS presente" '[ -f "$HOME/.config/wtm/cert.pem" ] && [ -f "$HOME/.config/wtm/key.pem" ]'
check "certificado no caducado" 'openssl x509 -in "$HOME/.config/wtm/cert.pem" -noout -checkend 86400 >/dev/null 2>&1'
check "huella del certificado guardada" 'grep -qE "([0-9A-F]{2}:){31}[0-9A-F]{2}" "$HOME/.config/wtm/cert-fingerprint.txt" 2>/dev/null'

check "adb disponible" 'command -v adb >/dev/null'
PHONE_MODEL="${WTM_PHONE_MODEL:-SM-S931B}"
PHONE=""
for serial in $(adb devices 2>/dev/null | awk 'NR>1 && $2=="device"{print $1}'); do
    model=$(adb -s "$serial" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
    [ "$model" = "$PHONE_MODEL" ] && PHONE="$serial" && break
done
check "teléfono $PHONE_MODEL por adb" '[ -n "$PHONE" ]'

check "scrcpy-server.jar en el teléfono" 'adb -s "${PHONE:-x}" shell test -f /data/local/tmp/scrcpy-server.jar 2>/dev/null'

# --- bridge vivo -----------------------------------------------------------
check "bridge responde /healthz" 'curl -sk --max-time 3 "https://127.0.0.1:$PORT/healthz" | grep -q "\"ok\": *true"'
check "healthz informa huella TLS" 'curl -sk --max-time 3 "https://127.0.0.1:$PORT/healthz" | grep -q "tls_fingerprint"'
FINGERPRINT_MATCH=""
SERVER_FP="$(curl -sk --max-time 3 "https://127.0.0.1:$PORT/healthz" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tls_fingerprint",""))' 2>/dev/null)"
LOCAL_FP="$(cat "$HOME/.config/wtm/cert-fingerprint.txt" 2>/dev/null | tr -d '\r')"
[ -n "$SERVER_FP" ] && [ "$SERVER_FP" = "$LOCAL_FP" ] && FINGERPRINT_MATCH=1
check "huella TLS coincide con la instalación" '[ -n "$FINGERPRINT_MATCH" ]'

# --- flujo de sesión --------------------------------------------------------
COOKIE_JAR="$PREFIX/tmp/wtm-cert-cookies.txt"
rm -f "$COOKIE_JAR"
check "login admin" 'curl -sk --max-time 5 -c "$COOKIE_JAR" -H "Origin: https://127.0.0.1:$PORT" -H "Content-Type: application/json" -d "{\"token\":\"$ADMIN_TOKEN\"}" -o /dev/null -w "%{http_code}" "https://127.0.0.1:$PORT/api/auth" | grep -q 200'
STATUS_JSON="$(curl -sk --max-time 5 -b "$COOKIE_JAR" "https://127.0.0.1:$PORT/api/status" 2>/dev/null)"
check "api/status con sesión" 'printf "%s" "$STATUS_JSON" | grep -q "scrcpy_alive"'
check "scrcpy activo según bridge" 'printf "%s" "$STATUS_JSON" | python3 -c "import json,sys;sys.exit(0 if json.load(sys.stdin).get(\"scrcpy_alive\") else 1)"'
check "config del stream (2000x1200@60)" 'printf "%s" "$STATUS_JSON" | python3 -c "
import json,sys
c = json.load(sys.stdin).get(\"config\") or {}
sys.exit(0 if c.get(\"width\") == 2000 and c.get(\"height\") == 1200 and c.get(\"fps\") == 60 else 1)"'
check "roles: viewer bloqueado en control" '
VIEWER_TOKEN=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[\"viewer\"])" "$AUTH_FILE")
rm -f "$PREFIX/tmp/wtm-cert-viewer.txt"
curl -sk --max-time 5 -c "$PREFIX/tmp/wtm-cert-viewer.txt" -H "Origin: https://127.0.0.1:$PORT" -H "Content-Type: application/json" -d "{\"token\":\"$VIEWER_TOKEN\"}" -o /dev/null "https://127.0.0.1:$PORT/api/auth"
[ "$(curl -sk --max-time 5 -b "$PREFIX/tmp/wtm-cert-viewer.txt" -H "Origin: https://127.0.0.1:$PORT" -H "Content-Type: application/json" -d "{\"action\":\"stop_dex\"}" -o /dev/null -w "%{http_code}" "https://127.0.0.1:$PORT/api/control")" = "403" ]'
check "control rechaza acciones no permitidas" '[ "$(curl -sk --max-time 5 -b "$COOKIE_JAR" -H "Origin: https://127.0.0.1:$PORT" -H "Content-Type: application/json" -d "{\"action\":\"nope\"}" -o /dev/null -w "%{http_code}" "https://127.0.0.1:$PORT/api/control")" = "400" ]'
check "sin sesión: /api/status es 401" '[ "$(curl -sk --max-time 5 -o /dev/null -w "%{http_code}" "https://127.0.0.1:$PORT/api/status")" = "401" ]'
check "WebSocket sin sesión es 401" '[ "$(curl -sk --max-time 5 -o /dev/null -w "%{http_code}" -H "Upgrade: websocket" -H "Connection: Upgrade" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Version: 13" "https://127.0.0.1:$PORT/ws")" = "401" ]'

rm -f "$COOKIE_JAR" "$PREFIX/tmp/wtm-cert-viewer.txt"

log "=== Resumen: $PASS PASS, $FAIL FAIL ==="
printf '%b' "$RESULTS"
[ "$FAIL" -eq 0 ] && { log "CERTIFICACIÓN: TODAS LAS VERIFICACIONES PASARON"; exit 0; }
log "CERTIFICACIÓN: HAY $FAIL VERIFICACIONES FALLIDAS"
exit 1
