# F4a/F5 — Deploy del relay S Pen (runbook)

Estado al 2026-11-25: la APK v1.3-pen está instalada en la tablet y el dispatch
(`cap` + `pd/pm/pu/ph`) está en `server.py` (commit `3fc0ac0`, rama
`feat/cap-negotiation-f3`). Falta que el relay del S25 corra ese código.

## Contexto

- El viewer (tablet) se conecta a `https://10.99.18.238:8443/#auto` (relay en el S25, Termux).
- El clon git del S25 está en `~/WTM` (rama `feat/cap-negotiation-f3`).
- El relay **no corre desde `~/WTM`**: corre desde `/data/data/com.termux/files/home/dex`
  (verificado con `readlink /proc/$PID/cwd`, PID 24447). `~/dex` **no es repo git**
  y no tiene `runtime/termux/` — los archivos están sueltos ahí.
- El launcher probable es `~/dex/dex.sh` (el widget DeX arranca el flujo; el
  watchdog descrito en `watchdog.sh` del repo parece no estar desplegado:
  `$PREFIX/tmp/rd-watchdog.log` no existe).
- Mensajes nuevos que el relay debe manejar: `cap` -> `cap_handler.handle(state, ws, data)`
  (responde `cap_ack` con el perfil elegido) y `pd`/`pm`/`pu`/`ph` ->
  `pen_handler.handle(state, data)` (trazos S Pen y multi-touch).

## Síntomas observados

| Síntoma | Causa |
|---|---|
| Viewer log: `perfil: esperando respuesta del relay...` eterno | Relay corriendo código viejo: no conoce `cap`, nunca responde `cap_ack` |
| Ni lápiz ni táctil trazan en el S25 | v1.3-pen envía todo como `pd/pm/pu/ph`; el relay viejo los ignora |
| `error: null` / `Failed to connect` tras reinicios | Ventanas en que el relay estuvo muerto (o IP del teléfono cambió) |

## Paso 1 — Sincronizar código al directorio del relay

```sh
cd ~/dex
cp ~/WTM/runtime/termux/server.py .
cp ~/WTM/runtime/termux/cap_handler.py .
cp ~/WTM/runtime/termux/pen_handler.py .
grep -c pen_handler server.py    # debe imprimir un numero > 0 (dispatch presente)
```

## Paso 2 — Arrancar el relay (el launcher real)

El widget DeX es lo que arranca todo. Alternativa manual:

```sh
cd ~/dex
./dex.sh
```

O, si `dex.sh` solo configura el flujo DeX (no es el que levanta el server):

```sh
cd ~/dex
set -a && . ~/.config/wtm/server.env && set +a
exec python3 server.py
```

Verificación de que levantó:

```sh
pgrep -f "python3 server.py"
curl -sk https://10.99.18.238:8443/healthz && echo " RELAY OK"
```

## Paso 3 — Verificar con el viewer

Reconectar la app. En el log debe desaparecer `perfil: esperando respuesta del relay...`
y aparecer el perfil aplicado (HUD verde). Luego:

- Táctil debe trazar en el S25.
- S Pen debe trazar; en apps sensibles a presión, trazo suave vs fuerte cambia grosor.
- Si un trazo no sale: relay imprime `[pen] error: ...` — esa línea es el diagnóstico.

## Troubleshooting

- **`OSError: [Errno 98] address already in use`** al arrancar manual: ya hay otro
  relay corriendo (bien). Mátalo con `pkill -f "python3 server.py"` y relanza.
- **`No such file or directory: ~/dex/capd`**: solo ocurre con `backend=hw`;
  el launcher real usa scrcpy. No es bloqueante si arrancas por `dex.sh`.
- **Viewer `Failed to connect`**: relay muerto (paso 2) o la IP `10.99.18.238`
  cambió (verificar en el S25: `ip addr`; actualizar URL en la app).
- **Ver código que corre un PID**: `readlink /proc/<PID>/cwd` y
  `grep -c pen_handler <cwd>/server.py`.
- **`sv down wtm`**: no aplica — runit no tiene ese servicio en esta instalación.

## Pendientes conocidos

- Fase 3: parchear scrcpy-server para input stylus real (presión nativa en apps
  Android tipo Krita).
- X11 + IA para ArtCraft en proot (otra sesión).
- Desplegar de forma duradera el update de `~/dex` (hoy: copia manual de 3 archivos).
