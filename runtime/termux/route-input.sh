#!/data/data/com.termux/files/usr/bin/bash
# route-input.sh — asocia los mouses Bluetooth físicos del teléfono al display
# virtual del DeX con las APIs ocultas de InputManager (Android 14+).
#
# Reconstrucción del subsistema histórico de enrutado de mouse
# (docs/bluetooth-mouse-routing.md). Mecanismo: helpers Java (jvm/inputassoc.zip
# y jvm/inputfocus.zip, ejecutados vía app_process con UID shell, igual que
# scrcpy-server) llaman por reflexión:
#   IInputManager.addUniqueIdAssociationByDescriptor(descriptor, uniqueId)
#   IInputManager.setDisplayIdForPointerIcon(displayId)
# Android mismo reasigna el dispositivo al display virtual: SIN movimiento
# fantasma en la pantalla del teléfono y con el cursor Samsung REAL dentro del
# DeX (viaja por el stream de video; no es un SVG del navegador).
#
# Descriptor: descubrimiento DINÁMICO (guía de reimplementación §7, nunca
# hardcodeado): nodos tipo mouse detectados con getevent -lp (BTN_MOUSE +
# REL_X + REL_Y) y el Descriptor de cada nodo leído del Event Hub State de
# 'dumpsys input'. Se asocian TODOS los mouses detectados. Anulación manual:
# MOUSE_DESCRIPTOR en server.env asocia SOLO ese descriptor.
#
# La asociación debe re-aplicarse tras cada reinicio de scrcpy (el display
# virtual cambia de uniqueId/ID): dex.sh la ejecuta en cada arranque y el
# watchdog la re-aplica al revivir la sesión.
#
# Códigos de salida: 0 asociado; 1 error; 3 API oculta ausente;
# 4 ningún mouse detectado; 5 display virtual no encontrado; 6 faltan jars.
set -u

export PATH=/data/data/com.termux/files/usr/bin:/system/bin
export HOME="${HOME:-/data/data/com.termux/files/home}"
export PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"

HERE="$(cd "$(dirname "$0")" && pwd)"
ADB="$PREFIX/bin/adb"
JVM_DIR="$HERE/jvm"
PHONE_MODEL="${WTM_PHONE_MODEL:-SM-S931B}"
ASSOC_JAR="$JVM_DIR/inputassoc.zip"
FOCUS_JAR="$JVM_DIR/inputfocus.zip"
R_ASSOC="/data/local/tmp/inputassoc.jar"
R_FOCUS="/data/local/tmp/inputfocus.jar"

log(){ printf '[route-input] %s\n' "$*"; }

# --- jars -------------------------------------------------------------------
if [ ! -f "$ASSOC_JAR" ] || [ ! -f "$FOCUS_JAR" ]; then
    log "faltan $ASSOC_JAR y/o $FOCUS_JAR."
    log "Termux no tiene javac/d8: constrúyelos en una máquina con JDK con"
    log "jvm/build.sh, o cópialos de la instalación histórica (remote-display/jvm/)."
    exit 6
fi

# --- teléfono ---------------------------------------------------------------
PHONE=""
for serial in $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1}'); do
    model=$("$ADB" -s "$serial" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
    if [ "$model" = "$PHONE_MODEL" ]; then
        PHONE="$serial"
        break
    fi
done
if [ -z "$PHONE" ]; then
    log "no se encontró el teléfono ($PHONE_MODEL) por adb"
    exit 1
fi

# --- display virtual (objetivo de la asociación) ----------------------------
DUMPD=$("$ADB" -s "$PHONE" shell dumpsys display 2>/dev/null | tr -d '\r')
UNIQUE_ID=$(printf '%s' "$DUMPD" | grep -oE "uniqueId='virtual:[^']*" | sed "s/uniqueId='//" | grep -i scrcpy | head -n 1)
if [ -z "$UNIQUE_ID" ]; then
    # variante con comillas dobles (según versión de Android)
    UNIQUE_ID=$(printf '%s' "$DUMPD" | grep -oE 'uniqueId="virtual:[^"]*' | sed 's/uniqueId="//' | grep -i scrcpy | head -n 1)
fi
DISPLAY_ID=$(printf '%s' "$DUMPD" | grep -E 'type=VIRTUAL|type VIRTUAL' | head -n 1 | grep -oE 'displayId=[0-9]+|displayId [0-9]+' | grep -oE '[0-9]+' | head -n 1)
if [ -z "$DISPLAY_ID" ]; then
    # segunda pasada: displayId cerca de la línea del uniqueId virtual
    DISPLAY_ID=$(printf '%s' "$DUMPD" | grep -B2 -A2 -i "uniqueId.*virtual" | grep -oE 'displayId=[0-9]+|displayId [0-9]+' | grep -oE '[0-9]+' | head -n 1)
fi
if [ -z "$UNIQUE_ID" ] || [ -z "$DISPLAY_ID" ]; then
    log "no se encontró el display virtual scrcpy (uniqueId='$UNIQUE_ID' displayId='$DISPLAY_ID')."
    log "¿está el DeX iniciado? líneas relevantes de dumpsys display:"
    printf '%s' "$DUMPD" | grep -iE 'virtual|scrcpy' | head -n 10 | sed 's/^/[route-input]   /'
    exit 5
fi
log "display virtual: uniqueId=$UNIQUE_ID displayId=$DISPLAY_ID"

# --- mouses: nodos tipo mouse (lógica validada en el S25 real) ---------------
MOUSE_PATHS=$("$ADB" -s "$PHONE" shell getevent -lp 2>/dev/null | tr -d '\r' | awk '
    function flush() { if (path != "" && btn && rx && ry) print path; btn = rx = ry = 0 }
    /add device/ { flush(); path = ""; if (match($0, /\/dev\/input\/event[0-9]+/)) path = substr($0, RSTART, RLENGTH); next }
    path != "" && /BTN_MOUSE|BTN_LEFT|BTN_RIGHT|BTN_MIDDLE/ { btn = 1 }
    path != "" && /REL_X/ { rx = 1 }
    path != "" && /REL_Y/ { ry = 1 }
    END { flush() }')

# --- descriptores de esos nodos (Event Hub State de dumpsys input) -----------
DUMP_IN=$("$ADB" -s "$PHONE" shell dumpsys input 2>/dev/null | tr -d '\r')
if [ -n "${MOUSE_DESCRIPTOR:-}" ]; then
    DESCRIPTORS="$MOUSE_DESCRIPTOR"
    log "descriptor manual (MOUSE_DESCRIPTOR): $DESCRIPTORS"
else
    DESCRIPTORS=$(printf '%s' "$DUMP_IN" | awk -v paths="$MOUSE_PATHS" '
        BEGIN { n = split(paths, P, "\n"); for (i = 1; i <= n; i++) if (P[i] != "") wanted[P[i]] = 1 }
        /Event Hub State:/ { inhub = 1; next }
        /Input Reader State:/ { inhub = 0 }
        inhub {
            line = $0; sub(/^[ \t]+/, "", line)
            if (line ~ /^[0-9]+: /) { thispath = "" }
            else if (line ~ /^Path: /) { split(line, q, " "); thispath = q[2] }
            else if (line ~ /^Descriptor: /) {
                if (thispath != "" && wanted[thispath]) { split(line, e, " "); print e[2] }
            }
        }')
fi
if [ -z "$DESCRIPTORS" ]; then
    log "no se obtuvo ningún descriptor de mouse."
    log "nodos tipo mouse detectados por getevent: ${MOUSE_PATHS:-ninguno}"
    if [ -z "$MOUSE_PATHS" ]; then
        log "si el mouse está apagado, enciéndelo y reintenta."
        exit 4
    fi
    log "Event Hub State (dispositivo/Path/Descriptor) para diagnóstico:"
    printf '%s' "$DUMP_IN" | sed -n '/Event Hub State:/,/Input Reader State:/p' | grep -E '^[[:space:]]*[0-9]+:|Path:|Descriptor:' | head -n 40 | sed 's/^/[route-input]   /'
    exit 4
fi

# --- subir los jars ---------------------------------------------------------
# Android 14+ rechaza archivos dex con bits de permiso escribibles: chmod 400.
"$ADB" -s "$PHONE" push "$ASSOC_JAR" "$R_ASSOC" >/dev/null 2>&1 || { log "no se pudo subir $ASSOC_JAR"; exit 1; }
"$ADB" -s "$PHONE" shell "chmod 400 $R_ASSOC" >/dev/null 2>&1
"$ADB" -s "$PHONE" push "$FOCUS_JAR" "$R_FOCUS" >/dev/null 2>&1 || { log "no se pudo subir $FOCUS_JAR"; exit 1; }
"$ADB" -s "$PHONE" shell "chmod 400 $R_FOCUS" >/dev/null 2>&1

# --- asociar cada descriptor al display virtual -----------------------------
failed=0
for DESC in $DESCRIPTORS; do
    OUT=$("$ADB" -s "$PHONE" shell "CLASSPATH=$R_ASSOC app_process / InputAssoc $DESC $UNIQUE_ID" 2>&1 | tr -d '\r')
    case "$OUT" in
        *ASSOC_OK*)
            log "asociado: $DESC -> $UNIQUE_ID"
            ;;
        *ASSOC_API_MISSING*)
            log "API oculta ausente en esta versión de Android (addUniqueIdAssociationByDescriptor): $OUT"
            exit 3
            ;;
        *)
            log "fallo al asociar $DESC: $OUT"
            failed=1
            ;;
    esac
done
if [ "$failed" -ne 0 ]; then
    exit 1
fi

# --- fijar el icono del puntero al display virtual ---------------------------
OUT=$("$ADB" -s "$PHONE" shell "CLASSPATH=$R_FOCUS app_process / InputFocus $DISPLAY_ID" 2>&1 | tr -d '\r')
case "$OUT" in
    *FOCUS_OK*)
        log "icono del puntero fijado al display $DISPLAY_ID ($OUT)"
        ;;
    *FOCUS_API_MISSING*)
        log "AVISO: setDisplayIdForPointerIcon ausente; la asociación quedó aplicada: $OUT"
        ;;
    *)
        log "AVISO: no se pudo fijar el icono del puntero (la asociación quedó aplicada): $OUT"
        ;;
esac
log "mouse(s) enrutado(s) al DeX por asociación nativa: sin movimiento en la pantalla del teléfono."
exit 0
