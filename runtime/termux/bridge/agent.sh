#!/data/data/com.termux/files/usr/bin/bash
# WTM bridge agent v1 - ejecuta misiones de diagnostico despachadas via GitHub.
# Despliegue: copiar a $HOME/.config/wtm/bridge/ y correr dentro de Termux.
# Requiere: gh autenticado, adb (android-tools), jq, timeout (coreutils).
# La rama bridge es operativa; NO se empaqueta en el tarball de releases.

set -u
REPO="JostinVaron/WTM"
BRANCH="bridge"
WORK="$HOME/.config/wtm/bridge"
MARKER="### BRIDGE MARKER ###"
HEARTBEAT=600

mkdir -p "$WORK/out" "$WORK/tmp"; cd "$WORK"

lock(){ [ -f v1.lock ] && kill -0 "$(cat v1.lock 2>/dev/null)" 2>/dev/null && { echo "bridge v1 ya activo"; exit 1; }; echo $$ > v1.lock; }
say(){ echo "[wtm-bridge $(date -u +%H:%M:%S)] $*"; }

# --- helpers GitHub -----------------------------------------------------
ghget(){ gh api "repos/$REPO/contents/$1?ref=$BRANCH" -q .content 2>/dev/null | base64 -d; }
ghput(){ # ghput <path> <message> <localfile>
  local sha r
  sha=$(gh api "repos/$REPO/contents/$1?ref=$BRANCH" -q .sha 2>/dev/null)
  if [ -n "$sha" ] && [ "$sha" != "null" ]; then
    gh api -X PUT "repos/$REPO/contents/$1" -f message="$2" -f branch="$BRANCH" -f content="$(base64 -w0 "$3")" -f sha="$sha" -f "committer[name]=wtm-bridge" -f "committer[email]=bridge@localhost" >/dev/null 2>&1
  else
    gha=0; gh api -X PUT "repos/$REPO/contents/$1" -f message="$2" -f branch="$BRANCH" -f content="$(base64 -w0 "$3")" -f "committer[name]=wtm-bridge" -f "committer[email]=bridge@localhost" >/dev/null 2>&1
  fi
}

# --- estado -------------------------------------------------------------
STATE="idle"
push_state(){
  { echo "ts: $(date -u +%FT%TZ)"; echo "estado: $STATE"; echo "device: ${DEV:-none}"; echo "heartbeat: $HEARTBEAT s"; } > "$WORK/status.txt"
  ghput status.txt "bridge: estado $STATE" "$WORK/status.txt" || true
}

# --- ejecucion de mision -------------------------------------------------
run_mission(){
  local mission timeout=300
  mission=$(grep -m1 '^MISSION-ID:' "$WORK/run.sh" | cut -d' ' -f2)
  [ -z "$mission" ] && mission="(sin-id)"
  timeout=$(grep -m1 '^TIMEOUT:' "$WORK/run.sh" | cut -d' ' -f2)
  [ -z "$timeout" ] && timeout=300
  say "ejecutando $mission (timeout ${timeout}s)"
  STATE="corriendo"; push_state
  termux-wake-lock >/dev/null 2>&1 || true
  { echo "=== BRIDGE RUN $(date -u +%FT%TZ) mission=$mission ==="
    timeout "$timeout" bash "$WORK/run.sh" 2>&1
    echo "=== EXIT=$? ==="
  } > "$WORK/out/last.txt"
  termux-wake-unlock >/dev/null 2>&1 || true
  STATE="subiendo"; push_state
  ghput "out/last.txt" "bridge: output de $mission" "$WORK/out/last.txt" || true
  { date -u +%FT%TZ; echo "$mission"; echo "$STATE"; } >> "$WORK/out/index.txt"
  STATE="idle"; push_state
  say "mision $mission completa"
}

# --- main ----------------------------------------------------------------
lock; say "v1 iniciado"
LAST=""
while true; do
  commands=""
  commands=$(ghget commands.sh)
  if [ -n "$commands" ] && printf '%s' "$commands" | grep -F "$MARKER" >/dev/null; then
    if [ "$commands" != "$LAST" ]; then
      author=$(gh api "repos/$REPO/commits/bridge/commits?path=commands.sh" -q '.[0].author.login' 2>/dev/null)
      if [ "$author" = "JostinVaron" ]; then
        printf '%s' "$commands" | grep -v -F "$MARKER" > "$WORK/run.sh"
        run_mission
        LAST="$commands"
      else
        say "autor no autorizado: ${author:-unknown}"
      fi check
    fi
  fi
  sleep 15
done