#!/data/data/com.termux/files/usr/bin/bash
# scrcpy-phone.sh — captura la pantalla COMPLETA del teléfono y la expone como
# H.264/H.265 crudo (Annex-B) para el relay.
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
# Intervalo de keyframes en segundos (perf3, 2026-11-17): los IDR de ~700 KB a
# 35 Mbps son el ~90% del tráfico en ráfagas — el log perf3 mostro un atasco del
# encoder con lat 157 ms en una escena pesada. GOP mas largo = menos picos de
# bitrate y encoder con margen. IFRAME_INTERVAL=2 en server.env lo cambia SIN
# tocar codigo; default 1 = comportamiento historico. Recovery: la app pide
# IDR fresco ("t":"idr") al descartar, asi que un GOP largo no penaliza la
# recuperacion tras atascos.
IFRAME_INTERVAL="${IFRAME_INTERVAL:-1}"
SCID="${SCID:-7777}"
SCRCPY_PORT="${SCRCPY_PORT:-1234}"

# Codec de vídeo del encoder (issue #4, docs/hevc-study.md): desde v0.3.0 el
# DEFAULT es h265 (HEVC) — ganador del A/B medido (2026-11-09, §7) y validado
# en sesión larga de producción en este par. VIDEO_CODEC=h264 en server.env
# restaura el camino AVC. El relay detecta el codec de los NAL que llegan
# (VPS/SPS/PPS HEVC vs SPS/PPS AVC) sin configuración adicional.
VIDEO_CODEC="${VIDEO_CODEC:-h265}"

# Mouse UHID (OPTATIVO, experimental): con MOUSE_MODE=uhid el server crea un
# mouse virtual UHID asociado al display virtual (scrcpy >=3.3, permiso
# ASSOCIATE_INPUT_DEVICE_TO_DISPLAY de Android 15+). POR DEFECTO está apagado:
# la inyección clásica (POINTER_ID_MOUSE, hover con cursor en DeX) está
# probada y es el mismo canal que usa mouse-route.py para enrutar el mouse
# físico Bluetooth del teléfono; habilitar uhid además podría crear dos
# punteros en conflicto. Para probarlo: MOUSE_MODE=uhid en server.env.
MOUSE_MODE="${MOUSE_MODE:-}"

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
      cmd="$("$ADB" -s "$PHONE" shell "cat /proc/$pid/cmdline" 2>/dev/null | tr '\000' ' ' | tr -d '\r' || true)"
      case "$cmd" in
        *com.genymobile.scrcpy.Server*) printf '%s\n' "$pid"; return 0 ;;
      esac
    fi
  fi
  for pid in $("$ADB" -s "$PHONE" shell 'ps -A' | awk '$NF == "app_process" {print $2}' | tr -d '\r'); do
    cmd="$("$ADB" -s "$PHONE" shell "cat /proc/$pid/cmdline" 2>/dev/null | tr '\000' ' ' | tr -d '\r' || true)"
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
  log "socket listo en 127.0.0.1:${SCRCPY_PORT}; no se reinició el servidor (el codec activo es el de esa instancia)."
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
  log "mouse: ${MOUSE_MODE} (experimental; el mouse físico del teléfono se enruta aparte con mouse-route.py)"
fi
case "$VIDEO_CODEC" in
  h264) CODEC_OPT="video_codec=h264" ;;   # opt-out explícito (default h265 desde v0.3.0)
  h265) CODEC_OPT="video_codec=h265" ;;   # DEFAULT desde v0.3.0 (issue #4)
  *)    log "VIDEO_CODEC inválido: '$VIDEO_CODEC' (usa h264 o h265)"; exit 1 ;;
esac
log "arrancando scrcpy-server (${MAX_SIZE}px, ${BITRATE} bps, ${FPS}fps, i-frame cada ${IFRAME_INTERVAL}s${NEW_DISPLAY:+", display virtual ${NEW_DISPLAY}"}, codec ${VIDEO_CODEC})..."
# IMPORTANTE: usar 'env CLASSPATH=... app_process ... &' directamente. El patrón
# 'nohup sh -c "CLASSPATH=... app_process ..."' hace que app_process muera al cerrar
# el shell (dejaba el DeX congelado). 'env' + '&' mantiene el proceso vivo.
start_server() {
  "$ADB" -s "$PHONE" shell "env CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server 5.0.1 \
  scid=${SCID} tunnel_forward=true audio=false control=true cleanup=true \
  raw_stream=true ${NEW_DISPLAY_OPT} ${MOUSE_OPT} ${CODEC_OPT} max_size=${MAX_SIZE} stay_awake=true max_fps=${FPS} \
  video_bit_rate=${BITRATE} video_codec_options=i-frame-interval=${IFRAME_INTERVAL},bitrate-mode=2 \
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
  # Campo 2026-11-25: el display tarda en aparecer en dumpsys (carrera con el
  # arranque del server) y el script se rendia a la primera: el display quedaba
  # pintado de verde hasta que el usuario movia el mouse. Reintentar y poblar.
  VD_ID=""
  for _ in $(seq 1 20); do
    VD_ID="$("$ADB" -s "$PHONE" shell "dumpsys display" 2>/dev/null | grep -oE 'mDisplayId=[0-9]+' | sort -u | grep -v 'mDisplayId=0' | head -1 | grep -oE '[0-9]+' || true)"
    [ -n "$VD_ID" ] && break
    sleep 0.5
  done
  if [ -n "$VD_ID" ]; then
    if "$ADB" -s "$PHONE" shell "am start --display ${VD_ID} -a android.intent.action.MAIN -c android.intent.category.HOME" >/dev/null 2>&1; then
      log "HOME lanzado en el display virtual ${VD_ID}."
    else
      log "el launcher del fabricante bloquea HOME en displays virtuales; lanzando Ajustes como contenido inicial (${VD_ID})."
      "$ADB" -s "$PHONE" shell "am start --display ${VD_ID} -n com.android.settings/.Settings" \
        >/dev/null 2>&1 || log "no se pudo poblar el display ${VD_ID}; lanza una app con: adb shell am start --display ${VD_ID} -n <paquete>/<actividad>"
    fi
    # Un display recien creado puede seguir sin superficie hasta que algo lo
    # compone: un toque inofensivo en la esquina fuerza el primer frame.
    sleep 1
  else
    log "no se detectó el id del display virtual; lanza una app manualmente."
  fi
fi

log "stream crudo (${VIDEO_CODEC}) en 127.0.0.1:${SCRCPY_PORT}; server scrcpy confirmado."
