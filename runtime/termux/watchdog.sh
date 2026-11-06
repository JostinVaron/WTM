#!/data/data/com.termux/files/usr/bin/bash
# watchdog nativo: mantiene el DeX vivo sin PRoot.
export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
cd "$(dirname "$0")"
ADB=/data/data/com.termux/files/usr/bin/adb
CONFIG="$HOME/.config/wtm/server.env"
if [ -r "$CONFIG" ]; then
    set -a
    . "$CONFIG"
    set +a
fi
PORT="${PORT:-8443}"
INTERVAL="${INTERVAL:-10}"
LOG=$PREFIX/tmp/rd-watchdog.log
log(){ echo "[watchdog] $(date +%H:%M:%S) $*" >>"$LOG"; }

bridge_up(){ curl -sk --max-time 3 "https://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; }
phone_adb(){
  local model
  for s in $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1}'); do
    m="$("$ADB" -s "$s" shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
    [ "$m" = "${WTM_PHONE_MODEL:-SM-S931B}" ] && { echo "$s"; return 0; }
  done
  return 1
}

while true; do
  if [ -e "$PREFIX/tmp/rd-dex-stopped" ]; then
    sleep "$INTERVAL"
    continue
  fi
  if ! phone_adb >/dev/null 2>&1; then
    FOUND=$(python3 find-adb-port.py 2>/dev/null || true)
    for p in $FOUND; do "$ADB" connect "127.0.0.1:$p" >/dev/null 2>&1 && log "adb conectado $p"; done
  fi
  if phone_adb >/dev/null 2>&1; then
    if ! bridge_up; then
      log "bridge caído -> arrancando"
     'server.env loaded; honors WTM_PORT' >>"$LOG" 2>&1
    fi
  fi
  sleep "$INTERVAL"
done
