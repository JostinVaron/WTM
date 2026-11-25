# F4a/F5 — Deploy del relay S Pen (runbook)

Estado al 2026-11-25: la APK v1.3-pen está instalada en la tablet y el dispatch
(`cap` + `pd/pm/pu/ph`) está en `server.py` (commit `3fc0ac0`, rama
`feat/cap-negotiation-f3`). Falta que el relay del S25 corra ese código.

## Contexto

- El viewer (tablet) se conecta a `https://<phone-ip>:8443/#auto` (relay en el S25, Termux).
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
curl -sk https://<phone-ip>:8443/healthz && echo " RELAY OK"
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
- **Viewer `Failed to connect`**: relay muerto (paso 2) o la IP `<phone-ip>`
  cambió (verificar en el S25: `ip addr`; actualizar URL en la app).
- **Ver código que corre un PID**: `readlink /proc/<PID>/cwd` y
  `grep -c pen_handler <cwd>/server.py`.
- **`sv down wtm`**: no aplica — runit no tiene ese servicio en esta instalación.

## Pendientes conocidos

- Fase 3: parchear scrcpy-server para input stylus real (presión nativa en apps
  Android tipo Krita).
- X11 + IA para ArtCraft en proot (otra sesión).
- Desplegar de forma duradera el update de `~/dex` (hoy: copia manual de 3 archivos).

## Actualización 2026-11-25 (sesión de campo)

- **APK compilado en CI**: `viewer-apk.yml` (workflow_dispatch) construye
  `app-debug.apk` con el SDK de GitHub Actions. Ya no hace falta SDK en Termux.
  Versión `0.1.1-pen2` (versionCode 2): `sendRaw` sincronizado, tool-type
  UNKNOWN aceptado y auto-conexión al abrir (sin pulsar "Conectar").
- **`ScrcpyLauncher` corregido**: el `scrcpy.Server` vive en el TELÉFONO, no en
  Termux; antes se buscaba con `pgrep` local y la negociación `cap` siempre
  respondía `applied:false`. Ahora lee `/proc/<pid>/cmdline` por `adb -s <phone>`.
- **Perfil fijado**: `WTM_PIN_PROFILE=1` en `server.env` aplica
  `NEW_DISPLAY`/`BITRATE`/`FPS` del operador (2000x1200@60, 35 Mbps) y verifica
  el cmdline real antes de responder `cap_ack`.
- **`Origin` sin puerto**: el viewer manda `https://<ip>` en `/api/auth`; el
  middleware tolera el puerto implícito solo para el mismo esquema+host.
- **Mouse BT**: `server.py` re-ejecuta `route-input.sh` al recibir la config de
  cada sesión scrcpy (la asociación es per-display y moría en cada relanzamiento).
- **Pantalla verde inicial**: `scrcpy-phone.sh` reintenta la detección del
  display virtual (~10 s) y lanza HOME/Ajustes para poblarlo.
- **Auto-relanzamiento**: si la sesión scrcpy muere en <3 s (carrera de
  arranque), el relay relanza `scrcpy-phone.sh` en vez de reconectar a un
  forward muerto.

### Causa raíz del "ni táctil ni S Pen" (resuelta 2026-11-25)

El viewer emitía `pd/pm/pu/ph` (verificado con logcat) pero **nada llegaba al
relay**. `PenForwarder.onTouch` corre en el hilo de UI y `WsClient.sendRaw`
escribía el socket TLS desde ahí: Android lanza `NetworkOnMainThreadException`,
que el `catch (_: Exception) {}` descartaba en silencio. Fix en `WsClient`:
`sendInput` usa un executor de un solo hilo (`wtm-input`, orden conservado) y
`sendRaw` registra errores en logcat (`sendRaw error: ...`).

Cadena verificada tras el fix (`logcat -s WTM` + log del relay):

```
onTouch masked=0 x=600 y=500 tool=1 sw=2000 sh=1200
emit act=0 tool=1 pen=false
[ws] msg pd {"t":"pd",...,"x":600,"y":500,"p":1.0000,"pt":"finger"}
[ws] msg pd {"t":"pd",...,"x":140,"y":210,...}   # tap -> abre Ajustes en el S25
[ws] msg ph {"t":"ph","a":7,...,"pt":"stylus","or":-1.5708}   # hover del S Pen
```

También corregido: `WsClient.login` manda `Origin` sin puerto (tolerado por el
relay), `sendRaw` sincronizado, `onConfig` asigna `streamW/H`, tool-type UNKNOWN
aceptado y auto-conexión al abrir la app. APK: `viewer-apk.yml` en Actions.

### Nota operativa: dos redes

El relay escucha en `0.0.0.0` con `WTM_ALLOWED_HOSTS` para hotspot y router. La
URL de la app debe ser la del interfaz por el que entra la tablet:
`https://<ip-hotspot>:8443` (tablet en el hotspot) o `https://<ip-router>:8443`
(tablet en el WiFi de casa). Si la app muestra "failed to connect", casi siempre
es esto.
