#!/usr/bin/env bash
# start_phone.sh — transmite la pantalla COMPLETA del teléfono (Android, vía
# scrcpy-server) a la tablet. Requiere scrcpy-server ya corriendo en el teléfono
# (ver scrcpy-phone.sh).
#
# La captura la hace scrcpy (SurfaceControl + codificador HW) y expone H.264
# crudo en 127.0.0.1:1234; este server lo republica a WebCodecs.
set -euo pipefail
cd "$(dirname "$0")"

CONFIG="${HOME:-/data/data/com.termux/files/home}/.config/wtm/server.env"
if [ -r "$CONFIG" ]; then
    set -a
    . "$CONFIG"
    set +a
fi

export BACKEND=scrcpy
export SCRCPY_HOST="${SCRCPY_HOST:-127.0.0.1}"
export SCRCPY_PORT="${SCRCPY_PORT:-1234}"
export PORT="${PORT:-8443}"
export FPS="${FPS:-60}"
: "${WTM_AUTH_FILE:=$HOME/.config/wtm/auth-tokens.json}"
: "${CERT:=$HOME/.config/wtm/cert.pem}"
: "${KEY:=$HOME/.config/wtm/key.pem}"
: "${WTM_BIND_HOST:=0.0.0.0}"
: "${WTM_ALLOWED_HOSTS:?Set WTM_ALLOWED_HOSTS in $CONFIG to the approved HTTPS host of the phone}"

echo $$ > "$PWD/stream.pid"
exec python3 server.py
