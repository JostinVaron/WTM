#!/data/data/com.termux/files/usr/bin/bash
# WTM bridge agent v1 - ejecuta misiones de diagnostico despachadas via GitHub.
# Despliegue: copiar a $HOME/.config/wtm/bridge/ y correr dentro de Termux.
# Requiere: gh autenticado, adb (android-tools), timeout (coreutils).
# La rama bridge es operativa; NO se empaqueta en el tarball de releases.

set -u
REPO="JostinVaron/WTM"
BRANCH="bridge"
WORK="$HOME/.config/wtm/bridge"
MARKER="### BRIDGE MARKER ###"
HEARTBEAT=600

mkdir -p "$WORK/out" "$WORK/tmp"; cd "$WORK"

[ -f v1.lock ] && kill -0 "$(cat v1.lock 2>/dev/null)" 2>/dev/null && { echo "bridge v1 ya activo"; exit 1; }
echo $$ > v1.lock
say(){ echo "[wtm-bridge $(date -u +%H:%M:%S)] $*"; }

# --- helpers GitHub -----------------------------------------------------
ghget(){ gh api "repos/$REPO/contents/$1?ref=$BRANCH" -q .content 2>/dev/null | base64 -d; }
ghput(){ # ghput <path> <message> <localfile>  (usa --input: seguro para archivos grandes)
  local sha
  sha=$(gh api "repos/$REPO/contents/$1?ref=$BRANCH" -q .sha 2>/dev/null)
  printf '{"message":"%s","branch":"%s","content":"%s"' "$2" "$BRANCH" "$(base64 -w0 "$3")" > tmp.json
  if [ -n "$sha" ] && [ "$sha" != "null" ]; then printf ',"sha":"%s"' "$sha" >> tmp.json; fi
  printf ',"committer":{"name":"wtm-bridge","email":"bridge@localhost"}}\n' >> tmp.json
  gh api -X PUT "repos/$REPO/contents/$1" --input tmp.json >/dev/null 2>&1
}

# --- estado -------------------------------------------------------------
STATE="idle"
push_state(){
  { echo "ts: $(date -u +%FT%TZ)"; echo "estado: $STATE"; echo "device: ${WTM_DEV:-none}"; echo "heartbeat: $HEARTBEAT s"; } > "$WORK/status.txt"
  ghput status.txt "bridge: estado $STATE" "$WORK/status.txt" || true
}

# --- ejecucion de mision -------------------------------------------------
run_mission(){
  local mission timeout=300
  mission=$(grep -m1 '^MISSION-ID:' "$WORK/run.sh" | cut -d' ' -f2); [ -z "$mission" ] && mission="(sin-id)"
  timeout=$(grep -m1 '^TIMEOUT:' "$WORK/run.sh" | cut -d' ' -f2); [ -z "$timeout" ] && timeout=300
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
  { echo "$(date -u +%FT%TZ) $mission $STATE"; } >> "$WORK/out/index.txt"
  STATE="idle"; push_state
  say "mision $mission completa"
}

# --- main ----------------------------------------------------------------
say "v1 iniciado"
LAST=""; LASTBEAT=0
while true; do
  commands=$(ghget commands.sh)
  if [ -n "$commands" ] && printf '%s' "$commands" | grep -F "$MARKER" >/dev/null; then
    if [ "$commands" != "$LAST" ]; then
      author=$(gh api "repos/$REPO/commits?path=commands.sh&sha=$BRANCH&per_page=1" -q '.[0].author.login' 2>/dev/null)
      if [ "$author" = "JostinVaron" ]; then
        if printf '%s' "$commands" | grep -q '^STOP_LOOP=yes'; then
          say "STOP recibido; apagando"
          STATE="detenido"; push_state
          rm -f v1.lock; exit 0
        fi
        printf '%s' "$commands" | grep -v -F "$MARKER" > "$WORK/run.sh"
        WTM_DEV=$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device"{print $1; exit}'); export WTM_DEV
        run_mission
        LAST="$commands"
      else
        say "autor no autorizado: ${author:-unknown} (ignorando)"
        LAST="$commands"
      fi
    fi
  fi
  now=$(date +%s)
  if [ $((now - LASTBEAT)) -ge "$HEARTBEAT" ]; then push_state; LASTBEAT=$now; fi
  sleep 15
done