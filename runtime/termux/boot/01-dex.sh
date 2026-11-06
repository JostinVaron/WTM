#!/data/data/com.termux/files/usr/bin/bash
# Auto-arranque NATIVO del DeX (sin PRoot) al encender el S25.
export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home

# wake-lock: mantiene Termux vivo aunque se cierre la app (notificación persistente)
termux-wake-lock 2>/dev/null || true

# arrancar el supervisor nativo del DeX
if ! pgrep -f 'dex/watchdog.sh' >/dev/null 2>&1; then
  nohup bash /data/data/com.termux/files/home/dex/watchdog.sh >/tmp/rd-watchdog-stdout.log 2>&1 &
fi
