#!/data/data/com.termux/files/usr/bin/bash
# DEPRECADO desde v0.4.0: el punto de entrada unico es el wizard DeX
# (.shortcuts/DeX, issue #15). Este script se conserva solo como envoltorio
# de compatibilidad: invocaciones existentes que todavia llaman a
# start-dex.sh se redirigen al wizard, que ademas recupera ADB tras un
# reboot (cosa que este script por si solo no hacia: fallaba en silencio).
export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=/data/data/com.termux/files/home
exec bash "$HOME/.shortcuts/DeX" "$@"
