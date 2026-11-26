#!/data/data/com.termux/files/usr/bin/bash
# Aplica el puente S Pen a una Craft app, compila aarch64 y la instala.
# Uso: wtm-pen-app.sh <photocraft|vectorcraft|filmcraft|effectcraft|lightcraft>
# El binario original queda como /usr/bin/<app>.pre-pen (restaurable).
set -euo pipefail
APP="${1:?uso: wtm-pen-app.sh <app>}"
BASE="${WTM_APPS_SRC:-$HOME/artcraft-src}"
SRC="$BASE/$APP"
RMT="$BASE/rust-motion-tablet-1.1.0"
TPL="$HOME/.config/wtm/bin/pen_bridge.rs"
PATCHER="$HOME/.config/wtm/bin/wtm-pen-patch.py"
LOG="$PREFIX/tmp/wtm-pen-$APP.log"

[ -f "$TPL" ] || { echo "falta $TPL"; exit 1; }
[ -f "$PATCHER" ] || { echo "falta $PATCHER"; exit 1; }

if [ ! -d "$SRC/.git" ]; then
  echo "clonando storytold/$APP (shallow)..."
  gh repo clone "storytold/$APP" "$SRC" -- --depth=1 >>"$LOG" 2>&1
fi

# rust-motion-tablet 1.1 fija objc2 =0.6.4 (solo macOS) y la app =0.6.5:
# Cargo resuelve la rama macOS aunque compile para Linux. Se vendoriza con
# la misma API y el pin relajado.
if [ ! -f "$RMT/Cargo.toml" ]; then
  RS="$PREFIX/var/lib/proot-distro/containers/ubuntu-gnome/rootfs"
  SRC_RMT=$(ls -d "$RS"/root/.cargo/registry/src/*/rust-motion-tablet-1.1.0 2>/dev/null | head -1 || true)
  [ -n "${SRC_RMT:-}" ] || { echo "no está rust-motion-tablet-1.1.0 en el registry; compila LightCraft primero"; exit 1; }
  cp -r "$SRC_RMT" "$RMT"
fi
sed -i 's/^version = "=0.6.4"$/version = "0.6"/' "$RMT/Cargo.toml"

python3 - "$SRC" "$RMT" <<'PY'
import pathlib, re, sys
app = pathlib.Path(sys.argv[1])
rmt = sys.argv[2]
root = app / "Cargo.toml"
s = root.read_text()
# Quita la tabla del parche compartido si un run anterior la dejó.
s = re.sub(r"\n# >>> wtm-pen shared patch.*?# <<< wtm-pen shared patch\n", "\n", s, flags=re.S)
entry = f'rust-motion-tablet = {{ path = "{rmt}" }}'
if "rust-motion-tablet = { path" not in s:
    if "[patch.crates-io]" in s:
        s = s.replace("[patch.crates-io]", "[patch.crates-io]\n" + entry, 1)
    else:
        s = s.rstrip() + f"\n\n[patch.crates-io]\n{entry}\n"
root.write_text(s)
print("objc2: rust-motion-tablet vendorizado en [patch.crates-io]")
PY

python3 "$PATCHER" "$SRC" "$TPL" || { echo "parche falló (ver arriba)"; exit 1; }

proot-distro login ubuntu-gnome --shared-tmp -- bash -c "
set -euo pipefail
cd '$SRC'
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
