#!/data/data/com.termux/files/usr/bin/bash
# Compila una Craft app con el puente S Pen de la crate compartida `ui-shell`.
# Uso: wtm-pen-shared.sh <photocraft|vectorcraft|filmcraft|effectcraft>
# El binario original queda como /usr/bin/<app>.pre-pen (restaurable).
set -euo pipefail
APP="${1:?uso: wtm-pen-shared.sh <app>}"
BASE="${WTM_APPS_SRC:-$HOME/artcraft-src}"
SRC="$BASE/$APP"
CL="$BASE/craft-libs"
RMT="$BASE/rust-motion-tablet-1.1.0"
TPL="$HOME/.config/wtm/bin/pen_bridge_ui_shell.rs"
PATCHER="$HOME/.config/wtm/bin/wtm-pen-shared-patch.py"
LOG="$PREFIX/tmp/wtm-pen-$APP.log"

[ -f "$TPL" ] || { echo "falta $TPL"; exit 1; }
[ -f "$PATCHER" ] || { echo "falta $PATCHER"; exit 1; }

if [ ! -d "$SRC/.git" ]; then
  echo "clonando storytold/$APP (shallow)..."
  gh repo clone "storytold/$APP" "$SRC" -- --depth=1 >>"$LOG" 2>&1
fi

REV=$(sed -nE 's/.*storytold\/craft-libs", rev = "([0-9a-f]{7,40})".*/\1/p' "$SRC/Cargo.toml" | head -1)
[ -n "${REV:-}" ] || { echo "no encuentro el rev de craft-libs en $SRC/Cargo.toml"; exit 1; }

if [ ! -d "$CL/.git" ]; then git init -q "$CL"; fi
git -C "$CL" remote get-url origin >/dev/null 2>&1 || git -C "$CL" remote add origin https://github.com/storytold/craft-libs
echo "craft-libs @ $REV"
git -C "$CL" fetch -q --depth=1 origin "$REV"
git -C "$CL" checkout -q FETCH_HEAD
git -C "$CL" reset -q --hard FETCH_HEAD
git -C "$CL" clean -qfd

# rust-motion-tablet 1.1 fija objc2 =0.6.4 (solo macOS), que choca con la app
# (objc2 =0.6.5). Se vendoriza con la misma API y el pin relajado.
if [ ! -f "$RMT/Cargo.toml" ]; then
  RS="$PREFIX/var/lib/proot-distro/containers/ubuntu-gnome/rootfs"
  SRC_RMT=$(ls -d "$RS"/root/.cargo/registry/src/*/rust-motion-tablet-1.1.0 2>/dev/null | head -1 || true)
  [ -n "${SRC_RMT:-}" ] || { echo "no está rust-motion-tablet-1.1.0 en el registry; ejecuta primero el build de LightCraft"; exit 1; }
  cp -r "$SRC_RMT" "$RMT"
fi
sed -i 's/^version = "=0.6.4"$/version = "0.6"/' "$RMT/Cargo.toml"

python3 "$PATCHER" "$CL" "$TPL" || { echo "parche falló (ver arriba)"; exit 1; }

python3 - "$SRC" "$CL" "$RMT" <<'PY'
import pathlib, re, sys
app = pathlib.Path(sys.argv[1])
cl = pathlib.Path(sys.argv[2])
rmt = pathlib.Path(sys.argv[3])
root = app / "Cargo.toml"
s = root.read_text()
s = re.sub(r"\n# >>> wtm-pen shared patch.*?# <<< wtm-pen shared patch\n", "\n", s, flags=re.S)

# objc2: el crate vendorizado acepta 0.6 (la app fija 0.6.5).
entry = f'rust-motion-tablet = {{ path = "{rmt}" }}'
if "rust-motion-tablet = { path" not in s:
    if "[patch.crates-io]" in s:
        s = s.replace("[patch.crates-io]", "[patch.crates-io]\n" + entry, 1)
    else:
        s = s.rstrip() + f"\n\n[patch.crates-io]\n{entry}\n"

lines = [
    "# >>> wtm-pen shared patch (craft-libs local, S Pen)",
    '[patch."https://github.com/storytold/craft-libs"]',
]
n = 0
for p in sorted((cl / "crates").glob("*/Cargo.toml")):
    t = p.read_text()
    m = re.search(r'^name\s*=\s*"([^"]+)"', t, re.M)
    if not m:
        continue
    lines.append(f'{m.group(1)} = {{ path = "{p.parent}" }}')
    n += 1
lines.append("# <<< wtm-pen shared patch")
if n < 50:
    sys.exit(f"tabla de patch incompleta ({n} crates)")
root.write_text(s.rstrip() + "\n\n" + "\n".join(lines) + "\n")
print(f"patch table: {n} crates de craft-libs")
PY

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
