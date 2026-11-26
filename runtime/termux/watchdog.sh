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

bridge_up(){ curl -sk --max-time 3 "https://${WTM_BIND_HOST:-127.0.0.1}:$PORT/healthz" >/dev/null 2>&1; }
phone_adb(){
  local model
  for s in $("$ADB" devices | awk 'NR>1 && $2=="device"{print $1}'); do
    m="$("$ADB" -s "$s" shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
    [ "$m" = "${WTM_PHONE_MODEL:-SM-S931B}" ] && { echo "$s"; return 0; }
  done
  return 1
}
cur_display(){
  "$ADB" -s "$(phone_adb)" shell dumpsys display 2>/dev/null | tr -d '\r' \
    | grep -oE "displayId=[0-9]+, uniqueId='virtual:[^']*scrcpy[^']*'" \
    | head -1 | grep -oE '[0-9]+' | head -1
}
# DisplayId real del primer mouse (Input Reader State). Vacio = no hay mouse
# o el parser no pudo leerlo: en ese caso NO se toca la asociacion existente.
mouse_display(){
  "$ADB" -s "$(phone_adb)" shell dumpsys input 2>/dev/null | tr -d '\r' | awk '
    /^ *Device [0-9]+:/ {mouse=0}
    /Sources:.*MOUSE/ {mouse=1}
    /DisplayId: [0-9]+/ && mouse {print $2; exit}'
}
routed_ok(){ [ -n "${1:-}" ] && [ "$(cat "$PREFIX/tmp/rd-route.display" 2>/dev/null)" = "$1" ]; }

CYCLE=0
while true; do
  CYCLE=$((CYCLE + 1))
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
      # dex.sh re-aplica la asociación nativa del mouse en cada arranque
      # (requisito §7: la asociación es per-display y muere con scrcpy).
      ./dex.sh >>"$LOG" 2>&1
      rm -f "$PREFIX/tmp/rd-route.display"
    elif [ $((CYCLE % 6)) -eq 0 ] && [ -x ./route-input.sh ]; then
      # Verificación read-only cada 60 s. Solo se re-asocia si el display
      # cambió (marcador) o si el mouse declara otro DisplayId. Re-aplicar
      # una asociación sana cada minuto puede interferir con el ratón real.
      DID=$(cur_display || true)
      if [ -n "$DID" ]; then
        MDISP=$(mouse_display || true)
        if ! routed_ok "$DID" || { [ -n "$MDISP" ] && [ "$MDISP" != "$DID" ]; }; then
          if out=$(MOUSE_DESCRIPTOR="${MOUSE_DESCRIPTOR:-}" ./route-input.sh 2>&1); then
            printf '%s\n' "$out" | sed 's/^/[watchdog-route] /' >>"$LOG"
            log "mouse re-asociado al display $DID (estaba: ${MDISP:-?})"
            echo "$DID" >"$PREFIX/tmp/rd-route.display"
          else
            rc=$?
            log "route-input falló (rc=$rc); reintenta en 60 s"
          fi
        fi
      fi
    fi
  fi
  sleep "$INTERVAL"
done
