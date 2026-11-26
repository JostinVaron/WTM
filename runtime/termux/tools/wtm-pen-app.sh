#!/data/data/com.termux/files/usr/bin/bash
# Aplica el puente S Pen a una Craft app, compila aarch64 y la instala.
# Uso: wtm-pen-app.sh <lightcraft|photocraft|vectorcraft|filmcraft|effectcraft>
# El binario original queda como /usr/bin/<app>.pre-pen (restaurable).
set -euo pipefail
APP="${1:?uso: wtm-pen-app.sh <app>}"
SRC="${WTM_APPS_SRC:-$HOME/artcraft-src}/$APP"
TPL="$HOME/.config/wtm/bin/pen_bridge.rs"
PATCH="$HOME/.config/wtm/bin/wtm-pen-patch.py"
LOG="$PREFIX/tmp/wtm-pen-$APP.log"

[ -f "$TPL" ] || { echo "falta $TPL"; exit 1; }
[ -f "$PATCH" ] || { echo "falta $PATCH"; exit 1; }

if [ ! -d "$SRC/.git" ]; then
  echo "clonando storytold/$APP (shallow)..."
  gh repo clone "storytold/$APP" "$SRC" -- --depth=1 >>"$LOG" 2>&1
fi

cp -f "$TPL" "$SRC/apps/$APP/src/pen_bridge.rs"

proot-distro login ubuntu-gnome --shared-tmp -- bash -c "
set -euo pipefail
cd '$SRC'
python3 '$PATCH' '$APP'
export PATH=/root/.cargo/bin:\$PATH
cargo build -p '$APP'
BIN=\"target/debug/$APP\"
[ -x \"\$BIN\" ]
if [ ! -e '/usr/bin/$APP.pre-pen' ]; then cp -p '/usr/bin/$APP' '/usr/bin/$APP.pre-pen'; fi
cp -f \"\$BIN\" '/usr/bin/$APP'
chmod 755 '/usr/bin/$APP'
echo 'instalado /usr/bin/$APP (backup .pre-pen)'
" >>"$LOG" 2>&1

echo "OK: /usr/bin/$APP — log $LOG"
