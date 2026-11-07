#!/data/data/com.termux/files/usr/bin/bash
# scrcpy-phone.sh — captura la pantalla COMPLETA del teléfono y la expone como H.264 crudo.
# Requiere depuración inalámbrica + adb conectado al propio teléfono.
# Selecciona el dispositivo por modelo (WTM_PHONE_MODEL, por defecto SM-S931B), no la tablet.
set -euo pipefail
cd "$(dirname "$0")"

ADB=/data/data/com.termux/files/usr/bin/adb
PREFIX=/data/data/com.termux/files/usr
SERVER_JAR="$PWD/jvm/scrcpy-server"

MAX_SIZE="${MAX_SIZE:-2000}"
BITRATE="${BITRATE:-12000000}"
FPS="${FPS:-60}"
SCID="${SCID:-7777}"
SCRCPY_PORT="${SCRCPY_PORT:-1234}"

# Mouse UHID: desde scrcpy 3.3 el server puede crear un mouse virtual UHID y
# asociarlo al display virtual (permiso ASSOCIATE_INPUT_DEVICE_TO_DISPLAY,
# Android 15+). Esto da un CURSOR REAL (hover, clic derecho) en el display
# virtual en vez de inyección de toques. MOUSE_MODE=uhid por defecto;
# MOUSE_MODE=inject (o vacío) restaura la inyección clásica si hiciera falta.
MOUSE_MODE="${MOUSE_MODE:-uhid}"

log(){ echo "[scrcpy] $*"; }

PHONE_MODEL="${WTM_PHONE_MODEL:-SM-S931B}"

# --- 1. elegir el serial del TELÉFONO (no la tablet) -----------------------
PHONE=""
for s in $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1}'); do
  m="$("$ADB" -s "$s" shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
  log "dispositivo $s = $m"
  if [ "$m" = "$PHONE_MODEL" ]; then PHONE="$s"; break; fi
done
[ -z "$PHONE" ] && { log "no se encontró el teléfono ($PHONE_MODEL)."; exit 1; }
log "usando teléfono: $PHONE"

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

# --- 2. subir server si falta ----------------------------------------------
if ! "$ADB" -s "$PHONE" shell "test -f /data/local/tmp/scrcpy-server.jar" 2>/dev/null; then
  log "subiendo scrcpy-server.jar..."
  "$ADB" -s "$PHONE" push "$SERVER_JAR" /data/local/tmp/scrcpy-server.jar
fi

# --- 3. forward del socket de vídeo ----------------------------------------
ABSTRACT="scrcpy_$(printf '%08d' "$SCID")"
SCRCPY_PID=$(scrcpy_pid || true)
if [ -n "$SCRCPY_PID" ]; then
  log "scrcpy ya está activo (PID $SCRCPY_PID); se restablece solo el forward tcp:${SCRCPY_PORT}."
  printf '%s\n' "$SCRCPY_PID" >"$PREFIX/tmp/rd-scrcpy.pid"
  "$ADB" -s "$PHONE" forward --remove "tcp:${SCRCPY_PORT}" >/dev/null 2>&1 || true
else
  "$ADB" -s "$PHONE" forward --remove "tcp:${SCRCPY_PORT}" >/dev/null 2>&1 || true
fi
"$ADB" -s "$PHONE" forward "tcp:${SCRCPY_PORT}" "localabstract:${ABSTRACT}"

if [ -n "$SCRCPY_PID" ]; then
  log "socket listo en 127.0.0.1:${SCRCPY_PORT}; no se reinició el servidor."
  exit 0
fi

# --- 4. arrancar scrcpy-server ---------------------------------------------
# NEW_DISPLAY="2000x1200/240" crea un display virtual a la resolución de la tablet
# (en vez de espejar la pantalla del teléfono): nitidez 1:1 y sin barras negras.
NEW_DISPLAY_OPT=""
if [ -n "${NEW_DISPLAY:-}" ]; then
  NEW_DISPLAY_OPT="new_display=${NEW_DISPLAY}"
  log "modo display virtual: ${NEW_DISPLAY} (ignora max_size)"
fi
MOUSE_OPT=""
if [ -n "$MOUSE_MODE" ] && [ "$MOUSE_MODE" != "inject" ]; then
  MOUSE_OPT="mouse=${MOUSE_MODE}"
  log "mouse: ${MOUSE_MODE} (cursor UHID asociado al display virtual, Android 15+)"
fi
log "arrancando scrcpy-server (${MAX_SIZE}px, ${BITRATE} bps, ${FPS}fps${NEW_DISPLAY:+", display virtual ${NEW_DISPLAY}"})..."
# IMPORTANTE: usar 'env CLASSPATH=... app_process ... &' directamente. El patrón
# 'nohup sh -c "CLASSPATH=... app_process ..."' hace que app_process muera al cerrar
# el shell (dejaba el DeX congelado). 'env' + '&' mantiene el proceso vivo.
start_server() {
  "$ADB" -s "$PHONE" shell "env CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server 5.0.1 \
  scid=${SCID} tunnel_forward=true audio=false control=true cleanup=true \
  raw_stream=true ${NEW_DISPLAY_OPT} ${MOUSE_OPT} max_size=${MAX_SIZE} stay_awake=true max_fps=${FPS} \
  video_bit_rate=${BITRATE} video_codec_options=i-frame-interval=1,bitrate-mode=2 \
  >/data/local/tmp/scrcpy.log 2>&1 &" 2>&1
}
start_server

wait_ready() {
  local i
  for i in $(seq 1 10); do
    SCRCPY_PID=$(scrcpy_pid || true)
    if [ -n "$SCRCPY_PID" ]; then return 0; fi
    sleep 1
  done
  return 1
}

if ! wait_ready && [ -n "$MOUSE_OPT" ]; then
  log "el server no arrancó con mouse=${MOUSE_MODE}; reintentando sin ese parámetro (inyección clásica)."
  MOUSE_OPT=""
  start_server
fi
if ! wait_ready; then
  log "scrcpy-server no apareció; revisa /data/local/tmp/scrcpy.log."
  exit 1
fi
printf '%s\n' "$SCRCPY_PID" >"$PREFIX/tmp/rd-scrcpy.pid"

# --- 5. poblar el display virtual (bug #8) ----------------------------------
# Un display virtual recién creado arranca NEGRO hasta que algo se renderiza en
# él. Se intenta lanzar el HOME al display nuevo; PERO en Samsung One UI el
# launcher (com.sec.android.app.launcher) rechaza arrancar en un display virtual
# desde shell: "SecurityException: Permission Denial ... launchDisplayId=N".
# Si HOME falla, se lanza Ajustes (actividad exportada, arranca sin problema)
# para poblar el display. Apps con FLAG_SECURE (bancos, Netflix) renderizan
# negro por diseño y no se pueden capturar.
if [ -n "${NEW_DISPLAY:-}" ]; then
  VD_ID=$("$ADB" -s "$PHONE" shell "dumpsys display" 2>/dev/null | grep -oE 'mDisplayId=[0-9]+' | sort -u | grep -v 'mDisplayId=0' | head -1 | grep -oE '[0-9]+' || true)
  if [ -n "$VD_ID" ]; then
    if "$ADB" -s "$PHONE" shell "am start --display ${VD_ID} -a android.intent.action.MAIN -c android.intent.category.HOME" >/dev/null 2>&1; then
      log "HOME lanzado en el display virtual ${VD_ID}."
    else
      log "el launcher del fabricante bloquea HOME en displays virtuales; lanzando Ajustes como contenido inicial (${VD_ID})."
      "$ADB" -s "$PHONE" shell "am start --display ${VD_ID} -n com.android.settings/.Settings" \
        >/dev/null 2>&1 || log "no se pudo poblar el display ${VD_ID}; lanza una app con: adb shell am start --display ${VD_ID} -n <paquete>/<actividad>"
    fi
  else
    log "no se detectó el id del display virtual; lanza una app manualmente."
  fi
fi

log "H.264 crudo en 127.0.0.1:${SCRCPY_PORT}; server scrcpy confirmado."
