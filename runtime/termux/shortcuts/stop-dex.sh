#!/data/data/com.termux/files/usr/bin/bash
# DEPRECADO desde v0.4.0: usa el atajo Exit-Dex. Este script se conserva
# solo como envoltorio de compatibilidad y delega en el stop verificado
# del runtime (bin/stop-dex.sh), que detiene bridge + scrcpy + enrutado
# de mouse y deja la marca de parada para el watchdog.
export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
exec bash "$HOME/.config/wtm/bin/stop-dex.sh" "$@"
