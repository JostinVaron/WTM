#!/bin/bash
# WTM: Krita 6.0.1 en zink/Adreno produce artefactos negros con el lápiz.
# Fuerza canvas software y OpenGL software SOLO para Krita.
#
# Ejecutar DENTRO del proot ubuntu-gnome (como root). Desde Termux:
#   cp WTM/runtime/termux/ubuntu/wtm-krita-software.sh "$PREFIX/tmp/"
#   proot-distro login ubuntu-gnome --shared-tmp -- bash /tmp/wtm-krita-software.sh
set -e

CFG=/home/dexuser/.config
DISPLAYRC="$CFG/kritadisplayrc"
KRITARC="$CFG/kritarc"

mkdir -p "$CFG"

# 1) kritadisplayrc: desactiva el canvas OpenGL de forma explícita.
cp -a "$DISPLAYRC" "$DISPLAYRC.wtm-bak-$(date +%Y%m%d)" 2>/dev/null || true
cat > "$DISPLAYRC" <<'EOF'
[General]
canvasState=OPENGL_SUCCESS
OpenGLRenderer=none
EOF

# 2) kritarc: canvasAcceleration=false bajo [General] (sin duplicar).
cp -a "$KRITARC" "$KRITARC.wtm-bak-$(date +%Y%m%d)" 2>/dev/null || true
if [ -f "$KRITARC" ] && ! grep -q '^canvasAcceleration=' "$KRITARC"; then
  if grep -q '^\[General\]' "$KRITARC"; then
    awk 'BEGIN{done=0} /^\[General\]/{print; if(!done){print "canvasAcceleration=false"; done=1}; next} {print}' \
      "$KRITARC" > "$KRITARC.wtm-tmp"
  else
    { echo "[General]"; echo "canvasAcceleration=false"; cat "$KRITARC"; } > "$KRITARC.wtm-tmp"
  fi
  mv "$KRITARC.wtm-tmp" "$KRITARC"
elif [ ! -f "$KRITARC" ]; then
  printf '[General]\ncanvasAcceleration=false\n' > "$KRITARC"
fi

# 3) Wrapper: garantiza GL software incluso si alguien lanza /usr/bin/krita.
cat > /usr/local/bin/krita <<'EOF'
#!/bin/bash
# WTM: evita artefactos negros de Krita 6 con zink/Adreno.
export QT_OPENGL=software
export LIBGL_ALWAYS_SOFTWARE=1
export MESA_LOADER_DRIVER_OVERRIDE=llvmpipe
export GALLIUM_DRIVER=llvmpipe
exec /usr/bin/krita "$@"
EOF
chmod 755 /usr/local/bin/krita

chown dexuser:dexuser "$DISPLAYRC" "$KRITARC" 2>/dev/null || true

echo "=== kritadisplayrc ==="
cat "$DISPLAYRC"
echo "=== kritarc (canvasAcceleration) ==="
grep -n '^canvasAcceleration=' "$KRITARC" || true
echo "=== wrapper ==="
ls -l /usr/local/bin/krita
echo "=== which -a krita ==="
which -a krita
