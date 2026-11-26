#!/data/data/com.termux/files/usr/bin/bash
# Vigila la activity de Termux:X11 en el display virtual WTM y protege la
# app/server del LMKD. La sesion X/GNOME vive en el server aparte: si la
# activity muere (OOM), el escritorio queda congelado; este watchdog la
# relanza en el display actual y el UHID precreado restaura Lorie pen.
set -uo pipefail
export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr

ADB="$PREFIX/bin/adb"
PHONE="${WTM_PHONE_ADB:-127.0.0.1:5555}"
RT="$PREFIX/tmp/rt-dex"
LOG="$RT/ubuntu-watchdog.log"
mkdir -p "$RT"
exec >>"$LOG" 2>&1

log(){ printf '[ubuntu-wd] %s %s\n' "$(date '+%F %T')" "$*"; }
log "watchdog arriba pid=$$"

while true; do
  if [ -e "$PREFIX/tmp/rd-dex-stopped" ] || [ -e "$RT/ubuntu.stop" ]; then
    log "flag de parada -> saliendo"
    exit 0
  fi

  # Proteger la app Termux:X11 (UID distinto: vía run-as, es debuggable).
  # hidepid oculta sus procesos a Termux, por eso se usa adb shell pidof.
  APP_PIDS=$("$ADB" -s "$PHONE" shell 'pidof com.termux.x11' 2>/dev/null | tr -d '\r')
  for p in $APP_PIDS; do
    "$ADB" -s "$PHONE" shell \
      "run-as com.termux.x11 sh -c 'echo 0 > /proc/$p/oom_score_adj'" \
      >/dev/null 2>&1 || true
  done
  # Server X y apps de la sesion (mismo UID Termux): escritura directa.
  for p in $(pgrep -f 'termux-x11 com.termux.x11' 2>/dev/null) \
           $(pgrep -f 'gnome-shell' 2>/dev/null) \
           $(pgrep -f 'krita' 2>/dev/null); do
    [ -n "$p" ] && echo 0 > "/proc/$p/oom_score_adj" 2>/dev/null
  done

  DID=$("$ADB" -s "$PHONE" shell dumpsys display 2>/dev/null | tr -d '\r' \
    | grep -oE "displayId=[0-9]+, uniqueId='virtual:[^']*scrcpy[^']*'" \
    | head -1 | grep -oE '[0-9]+' | head -1)
  if [ -n "$DID" ]; then
    if ! "$ADB" -s "$PHONE" shell dumpsys window displays 2>/dev/null \
        | tr -d '\r' | grep -A25 "mDisplayId=$DID" \
        | grep -q 'com.termux.x11'; then
      log "activity ausente en display $DID; relanzando"
      "$ADB" -s "$PHONE" shell \
        "am start --display $DID -n com.termux.x11/.MainActivity" >/dev/null 2>&1 || true
    fi
  fi
  sleep 5
done
