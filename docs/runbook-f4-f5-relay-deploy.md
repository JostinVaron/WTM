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

## Actualización 2026-11-25 (segunda sesión): UHID S Pen, PenProbe y rechazo de palma

Versión viewer: `0.1.2-pen3` (versionCode 3), CI run `45712399294`.
La Fase 3 ya **no** requiere parchear `scrcpy-server`: se usa el `scrcpy-server`
5.0.1 stock y el lápiz entra por UHID (se evita el fork GPLv3).

### F3 — pantalla verde eliminada con `TYPE_START_APP`

- `server.py` envía `TYPE_START_APP` (16) por el canal de control de scrcpy
  justo después de conectar, con `WTM_START_APP` (default
  `com.android.settings`).
- Evita la carrera de `dumpsys display` que dejaba el display virtual sin
  capa y el viewer mostraba verde.
- Medición: captura del display virtual (`screencap -d <sf-id>`) 2000x1200,
  **7740 colores únicos, 0.0% píxeles verdes**. Log:
  `[scrcpy] START_APP com.android.settings (display virtual)`.

### F1 — S Pen como digitizador UHID real

- `pen_handler.py` crea un dispositivo HID Usage Page `0x0D` / Usage Pen
  `0x02`, reporte de 9 bytes: flags, X, Y, presión 0..4095, tilt X/Y −90..90.
- VID/PID 0x1209/0x0001; nombre `WTM Pen`; `INPUT_PROP_DIRECT`.
- Dedos siguen por `INJECT_TOUCH`; el lápiz va por `UHID_CREATE/INPUT`.
- Medición en el S25 (Android 17):
  - `dumpsys input`: `WTM Pen`,
    `Sources: KEYBOARD | TOUCHSCREEN | STYLUS`,
    `AssociatedDisplayUniqueIdByPort: virtual:...scrcpy,...`,
    `Touch Input Mapper ... mode - DIRECT`.
  - `getevent -pl`: `ABS_PRESSURE 0..4095`, `ABS_TILT_X/Y -90..90`,
    `BTN_TOOL_PEN/RUBBER`, `INPUT_PROP_DIRECT`.
  - PenProbe: `tool: STYLUS (2)`, presión 4092/4095, tilt 30°, orientación
    −90° con el reporte sintético (tiltX=30, tiltY=0).

### F1b — calibración de tilt/orientación

- AOSP `TouchInputMapper.cpp`:
  `orientation=atan2(-sin(tiltX),sin(tiltY))`,
  `tilt=acos(cos(tiltX)*cos(tiltY))`.
- El mapeo anterior introducía **−90°** de offset. La inversa exacta (R²,
  ecuación cuadrática) conserva orientación y tilt; solo satura en tilt casi
  plano diagonal, donde los dos ejes HID no alcanzan.
- Verificación: pedido `tl=0.5 rad (28.65°)`, `or=-1.0 rad (−57.30°)`;
  PenProbe midió `tilt=28.06°`, `orientation=−57.53°` (el error de 0.58° es
  el redondeo a grados enteros del HID).

### F2 — rechazo de palma

- Viewer (`PenForwarder.kt`): descarta `TOOL_TYPE_PALM` (5), honra
  `FLAG_CANCELED` (API 33+), ignora dedos 800 ms tras el último evento de
  stylus y libera dedos ya abajo. Campo `tool` (`stylus`/`eraser`/`finger`).
- Relay (`pen_handler.py`): respaldo; si un dedo se mueve con stylus activo,
  lo suelta con un UP y no reenvía sus movimientos.
- Verificación local: `input stylus` en la tablet llegó al relay
  (`x=500, tool=stylus`); `input touchscreen tap` en `x=700` **no** llegó
  (0 mensajes) por el holdoff del viewer.
- Verificación relay: dedo en (150,150) con stylus activo fue rechazado;
  PenProbe mostró el último evento `STYLUS MOVE` y ningún puntero de dedo.

### PenProbe — app de verificación en el display virtual

- Activity exportada del APK viewer:
  ```
  adb shell am start --display <displayId> -n ai.ashna.wtmviewer/.PenProbeActivity
  ```
- Obtener el display lógico:
  ```
  adb shell dumpsys display | grep -oE "displayId=[0-9]+, uniqueId='virtual:[^']*scrcpy[^']*'"
  ```
- Capturar el display virtual (usar el id de SurfaceFlinger, no el lógico):
  ```
  adb shell dumpsys SurfaceFlinger --display-id | grep -i scrcpy
  adb exec-out screencap -d <sf-id> -p > vd.png
  ```
- Test UHID sin dibujar: enviar `{"t":"pen_test"}` por `/ws` con el token
  viewer (no guardar el token en el repo). El relay crea/usa el dispositivo
  y emite 180 reportes sintéticos.

### CI y firma del APK

- `viewer-apk.yml` compila en GitHub Actions; cada run genera un keystore
  debug nuevo, así que la firma cambia entre runs. Para actualizar la tablet
  sin perder token/URL:
  ```
  adb shell run-as ai.ashna.wtmviewer cat .../shared_prefs/wtm.xml > wtm.xml
  adb shell pm uninstall ai.ashna.wtmviewer
  adb install -r app-debug.apk
  # lanzar una vez, force-stop, restaurar wtm.xml con run-as, relanzar
  ```
- `pm uninstall -k` **no** sirve para cambiar de firma: deja el paquete
  registrado y el install sigue fallando con
  `INSTALL_FAILED_UPDATE_INCOMPATIBLE`.

### Variables nuevas

- `WTM_START_APP` (default `com.android.settings`): app que se abre en el
  display virtual para poblarlo (F3).
- `WTM_PALM_REJECT` (default `1`): rechazo de palma de respaldo en el relay.
- `WTM_PIN_PROFILE=1` sigue fijando 2000x1200@60 y 35 Mbps.

### Estado verificado (2026-11-25)

- Relay nuevo en `~/dex`; `healthz` OK; `scrcpy_alive:true`.
- Stream HEVC 2000x1200; tablet v3 reconectada y mostrando PenProbe.
- 51 tests unitarios OK (`tests.test_pen_handler`, `test_server_security`,
  `test_server_live`).

## Actualización 2026-11-25 (tercera sesión): táctil tras el S Pen, firma estable y arranque

### Bug de campo: táctil y mouse muertos después de usar el lápiz

El hover de la pluma dejaba el dispositivo UHID **in-range para siempre**:
`_out_of_range_later` solo se agendaba tras un UP y el viewer no reenviaba
`ACTION_HOVER_EXIT`. Android suprime táctil y mouse mientras un digitizador
Pen está en rango (el mismo comportamiento de una Galaxy Tab con EMR), así
que el display virtual quedaba sordo hasta cambiar de app.

Fix:

- `PenForwarder.kt`: `ACTION_HOVER_EXIT` -> `ph a=10`.
- `pen_handler.py`: `a=10` manda `in_range=0` al instante; cada hover/UP arma
  un respaldo `WTM_PEN_OOR_MS` (1000 ms) que se cancela con down/move; el
  timer queda atado a la sesión scrcpy.
- Ventana de palma del relay configurable: `WTM_PALM_WINDOW_MS` (600 ms).

Evidencia (`getevent -lt /dev/input/event14` del S25):

```
[57714.676181] EV_KEY BTN_DIGI DOWN     # hover enter
[57715.477969] EV_KEY BTN_DIGI UP       # HOVER_EXIT inmediato
[57716.280448] EV_KEY BTN_DIGI DOWN     # segundo hover
[57717.300590] EV_KEY BTN_DIGI UP       # respaldo, 1.02 s
```

Mientras la pluma se mueve en hover el táctil sigue bloqueado (nativo Galaxy
Tab); al alejarla, HOVER_EXIT lo libera sin esperar.

### APK 0.1.3-pen4 y firma estable

- `versionCode 4`, `versionName 0.1.3-pen4`; CI run `45718740904`.
- Keystore PKCS12 en `~/.config/wtm/wtm-viewer.p12` (600) y
  `keystore.env` (600); **nunca** en el repo.
- Secrets: `WTM_KEYSTORE_BASE64`, `WTM_KEYSTORE_PASSWORD`, `WTM_KEY_ALIAS`,
  `WTM_KEY_PASSWORD`. `viewer-apk.yml` lo decodifica y el build firma
  `debug` con `signingConfigs.stable`; CI imprime el SHA-256.
- Primera instalación tras el cambio de firma: desinstalar una vez. Después
  `adb install -r` funciona entre builds.
- Tablet: respaldo de `shared_prefs/wtm.xml` con `run-as`, uninstall, install,
  lanzar una vez, force-stop, restaurar XML y relanzar. Token/URL intactos.

### Wizard y widgets

- `~/.shortcuts/DeX` ahora usa el `PORT` de `server.env`, lanza el viewer
  nativo en la tablet si su ADB está conectado y actualiza su `base_url` con
  `run-as` al IP del hotspot.
- El diálogo final **ya no muestra el token** (seguridad); muestra la URL y
  la huella TLS. El token sigue en `~/.config/wtm/auth-tokens.json` para el
  fallback Chrome.
- `WTM_BIND_HOST=0.0.0.0` se respeta y `WTM_ALLOWED_HOSTS` se amplía sin
  recortar `localhost`/`127.0.0.1`.
- `WTM_WIZARD_NONINTERACTIVE=1` permite probar el wizard sin diálogos.
- Nuevo widget `PenTest` (`~/.shortcuts/PenTest`): lanza PenProbe en el
  display virtual y dispara 180 reportes UHID; log en `rd-pentest.log`.

### Ciclo de arranque verificado

1. `Exit-Dex`: relay detenido, `rd-dex-stopped` escrito, watchdog vivo.
2. `DeX` (no interactivo): relay nuevo (PID 9591), `healthz OK`,
   `scrcpy_alive:true`, flag borrado, tablet relanzada y reconectada.
3. Captura del display virtual tras el ciclo: 2000x1200, 5555 colores,
   0.0 % verde.
4. `PenTest`: PenProbe en display 25, 171 reportes UHID, `rc=0`.

### Variables nuevas

- `WTM_PEN_OOR_MS` (default `1000`): respaldo de fuera-de-rango.
- `WTM_PALM_WINDOW_MS` (default `600`): ventana de rechazo de palma del relay.
- `WTM_WIZARD_NONINTERACTIVE` (`1` para pruebas sin diálogos).
- `WTM_TABLET_MODEL` (default `SM-P613`): prefijo de modelo para el wizard.

### Tests

`54 tests OK` (`tests.test_pen_handler` 24, `test_server_security`,
`test_server_live`). CI `45718740904` compiló y firmó el APK con el
certificado estable `98:18:35:…:5F:16` (DN `CN=WTM Viewer`).

## Actualización 2026-11-25 (cuarta sesión): el dedo después del lápiz

### Reporte de campo

- Con el S Pen: se usa, se aleja, y el **táctil del dedo no responde**.
- El **mouse sí funciona**; lápiz y mouse a la vez funcionan perfecto.

### Causa

El mouse no pasa por el filtro de dedos; el dedo sí. La ventana era
demasiado larga en las tres capas: viewer 800 ms, relay 600 ms y pluma UHID
1000 ms en rango. Un toque deliberado poco después de levantar el lápiz caía
dentro y se descartaba. (La supresión global del S25 quedó descartada porque
el mouse funcionaba.)

### Fix v6

- `PenForwarder.kt`: `STYLUS_HOLDOFF_MS` 800 → **250 ms**.
- `pen_handler.py`: `WTM_PALM_WINDOW_MS` 600 → **250 ms**;
  `WTM_PEN_OOR_MS` 1000 → **250 ms**.
- `HOVER_EXIT` sigue sacando la pluma de rango al instante.
- La palma real la siguen filtrando `TOOL_TYPE_PALM`, `FLAG_CANCELED` y el
  bloqueo mientras el stylus toca o se mueve en hover activo.

### Evidencia

`getevent -lt /dev/input/event14` del S25:

```
[59659.091838] EV_KEY BTN_DIGI DOWN     # hover enter
[59659.193346] EV_KEY BTN_DIGI UP       # HOVER_EXIT, 101 ms
[59659.695687] EV_KEY BTN_DIGI DOWN     # segundo hover
[59659.947614] EV_KEY BTN_DIGI UP       # respaldo, 252 ms
```

Prueba del dedo: stylus, 0.4 s después un tap en (1000,700); el relay recibió
`tool=finger` y PenProbe mostró `tool: FINGER` con el punto verde.

### APK y verificación

- `0.1.5-pen6` (versionCode 6), CI `45722739695`.
- `adb install -r` sobre la v5 funcionó en tablet y S25 (firma estable,
  prefs y token intactos).
- 54 tests unitarios OK.
- Relay nuevo en `~/dex`; healthz OK; scrcpy_alive true.
