# WTM bridge - cola de misiones. La mision termina SIEMPRE con la marca.
# Formato (opcional): MISSION-ID: <id> / TIMEOUT: <segundos> / cuerpo bash.
# El agente auto-detecta el dispositivo: usa $WTM_DEV (o "adb -s $WTM_DEV").
# Solo se ejecutan misiones cuyo commit es del autor autorizado (JostinVaron).

echo "=== MISION W1-SMOKE: inventario de salud del S25 $(date -u +%FT%TZ) ==="
echo "--- adb devices ---"
adb devices
echo "--- modelo/version ---"
adb -s "$WTM_DEV" shell getprop ro.product.model 2>&1 | tr -d '\r'
adb -s "$WTM_DEV" shell getprop ro.build.version.release 2>&1 | tr -d '\r'
echo "--- termux wake + bateria/temperatura ---"
adb -s "$WTM_DEV" shell dumpsys battery 2>&1 | tr -d '\r' | grep -E "level|temperature" | head -4
echo "--- procesos clave del pipeline (bridge, scrcpy, server) ---"
adb -s "$WTM_DEV" shell "ps -A 2>/dev/null | grep -E 'scrcpy|server.py' | head -6" 2>&1 | tr -d '\r'
echo "--- IP del hotspot (para verificacion de bind) ---"
adb -s "$WTM_DEV" shell "ip addr show swlan0 2>/dev/null | grep 'inet '" 2>&1 | tr -d '\r'
echo "--- fin del inventario ---"
### BRIDGE MARKER ###