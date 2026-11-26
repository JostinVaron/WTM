#!/data/data/com.termux/files/usr/bin/bash
# wtm-stability-check.sh — prueba reproducible de estabilidad del escritorio
# Ubuntu (Termux:X11 + GNOME) con touch trackpad, lapiz y mouse.
#
# Uso:  bash ~/.config/wtm/bin/wtm-stability-check.sh
# Requisitos: sesion Ubuntu/GNOME arriba, relay/viewer arriba, ADB al telefono.
# Efectos: inyecta taps y trazos sinteticos (mueve el puntero y puede dibujar
# en la ventana activa); toma una captura. No reinicia ni mata nada.
set -uo pipefail
export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=${HOME:-/data/data/com.termux/files/home}
export PREFIX=${PREFIX:-/data/data/com.termux/files/usr}
PHONE="${WTM_PHONE_ADB:-127.0.0.1:5555}"
ADB="$PREFIX/bin/adb"
RT="${TMPDIR:-$PREFIX/tmp}/rt-dex"
SHELLLOG="$RT/shell49.log"
XI2LOG="$RT/xi2-stability.log"
SHOT="$RT/stability.png"
PASS=0; FAIL=0; WARN=0
ok(){ printf '  PASS  %s\n' "$*"; PASS=$((PASS+1)); }
bad(){ printf '  FAIL  %s\n' "$*"; FAIL=$((FAIL+1)); }
warn(){ printf '  WARN  %s\n' "$*"; WARN=$((WARN+1)); }
inproot(){ proot-distro login ubuntu-gnome --shared-tmp -- bash -c "$1" 2>/dev/null; }
shell_pid(){ ps -eo pid,args 2>/dev/null | awk '$2=="gnome-shell"{print $1; exit}'; }
dbus_addr(){ [ -r "/proc/$1/environ" ] && tr '\0' '\n' < "/proc/$1/environ" | sed -n 's/^DBUS_SESSION_BUS_ADDRESS=//p' | head -1; }
shell_ping(){
  local pid addr
  pid=$(shell_pid); [ -n "$pid" ] || return 1
  addr=$(dbus_addr "$pid"); [ -n "$addr" ] || return 1
  inproot "export DBUS_SESSION_BUS_ADDRESS='$addr'; timeout 5 gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell --method org.freedesktop.DBus.Peer.Ping" 2>/dev/null | grep -q '()'
}
count_of(){ local n; n=$(grep -c "$1" "$2" 2>/dev/null || true); echo "${n:-0}"; }
critical_count(){ count_of "Gjs-CRITICAL" "$SHELLLOG"; }

echo "== WTM estabilidad $(date '+%F %T') =="

# 1. Relay
if curl -sk --max-time 5 "https://127.0.0.1:8443/healthz" 2>/dev/null | grep -q '"ok": *true'; then
  ok "relay healthz"
else
  bad "relay healthz"
fi

# 2. X
if inproot 'DISPLAY=:0 timeout 5 xdpyinfo >/dev/null 2>&1'; then ok "X :0 responde"; else bad "X :0 no responde"; fi

# 3. GNOME
SPID=$(shell_pid)
if [ -n "$SPID" ]; then ok "gnome-shell vivo (pid $SPID)"; else bad "gnome-shell ausente"; fi
if shell_ping; then ok "gnome-shell responde D-Bus"; else bad "gnome-shell NO responde D-Bus"; fi

# 4. Guard de palma (debe estar retirado)
if ps -eo pid,args 2>/dev/null | awk '$2=="python3" && $3=="/usr/local/bin/wtmpen" {found=1} END{exit !found}'; then
  bad "wtmpen guard sigue vivo (debe estar retirado)"
else
  ok "sin wtmpen guard"
fi

# 5. Display scrcpy
DID=$("$ADB" -s "$PHONE" shell dumpsys display 2>/dev/null | tr -d '\r' \
  | grep -oE "displayId=[0-9]+, uniqueId='virtual:[^']*scrcpy[^']*'" | head -1 | grep -oE '[0-9]+' | head -1)
if [ -n "$DID" ]; then ok "display scrcpy=$DID"; else bad "display scrcpy no encontrado"; fi

# 6. Mouse asociado al display
if [ -n "$DID" ] && [ -x "$HOME/dex/route-input.sh" ]; then
  out=$(cd "$HOME/dex" && MOUSE_DESCRIPTOR="${MOUSE_DESCRIPTOR:-}" ./route-input.sh 2>&1); rc=$?
  got=$(printf '%s\n' "$out" | sed -n 's/.*getDisplayIdForPointerIcon() -> \([0-9]*\).*/\1/p' | tail -1)
  if [ "$rc" = 4 ]; then warn "sin mouse BT detectado"
  elif [ "$got" = "$DID" ]; then ok "mouse asociado al display $DID"
  else bad "mouse NO asociado (route rc=$rc, icono=${got:-?})"; fi
else
  warn "route-input.sh no disponible"
fi

# 7. Alternancia touch <-> lapiz
B=$(critical_count)
if [ -n "$DID" ]; then
  (inproot 'DISPLAY=:0 timeout 16 xinput test-xi2 --root' >"$XI2LOG" 2>/dev/null &)
  sleep 4
  for _ in 1 2 3; do "$ADB" -s "$PHONE" shell input -d "$DID" tap 800 500 >/dev/null 2>&1; sleep 1; done
  for _ in 1 2 3; do
    "$ADB" -s "$PHONE" shell input stylus -d "$DID" motionevent DOWN 1000 600 --axis PRESSURE,0.8 >/dev/null 2>&1
    "$ADB" -s "$PHONE" shell input stylus -d "$DID" motionevent MOVE 1080 640 --axis PRESSURE,0.6 >/dev/null 2>&1
    "$ADB" -s "$PHONE" shell input stylus -d "$DID" motionevent UP 1120 680 >/dev/null 2>&1
    sleep 1
  done
  sleep 10
  PB=$(count_of "EVENT type 4" "$XI2LOG")
  PR=$(count_of "EVENT type 5" "$XI2LOG")
  PM=$(count_of "device: 9" "$XI2LOG")
  if [ "$PB" -ge 3 ] && [ "$PR" -ge 3 ]; then ok "touch: $PB clicks, $PR releases"; else bad "touch sin clicks ($PB/$PR)"; fi
  if [ "$PM" -ge 3 ]; then ok "lapiz: $PM eventos XI2"; else bad "lapiz sin eventos ($PM)"; fi
  if shell_ping; then ok "shell sigue respondiendo tras alternar"; else bad "shell colgado tras alternar"; fi
else
  bad "alternancia omitida (sin display)"
fi

# 8. Sin CRITICALs nuevos
A=$(critical_count)
if [ "$A" -le "$B" ]; then ok "0 Gjs-CRITICAL nuevos ($B -> $A)"; else bad "$((A-B)) Gjs-CRITICAL nuevos ($B -> $A)"; fi

# 9. Captura del escritorio
if inproot "DISPLAY=:0 timeout 10 scrot -o /tmp/rt-dex/stability.png >/dev/null 2>&1" && [ -s "$SHOT" ]; then
  SZ=$(stat -c %s "$SHOT" 2>/dev/null || echo 0)
  if [ "$SZ" -gt 50000 ]; then ok "captura OK (${SZ} bytes)"; else warn "captura pequena (${SZ} bytes)"; fi
else
  bad "captura fallida"
fi

echo "== RESULTADO: PASS=$PASS FAIL=$FAIL WARN=$WARN =="
[ "$FAIL" -eq 0 ]
