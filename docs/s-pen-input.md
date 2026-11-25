# S Pen de extremo a extremo por UHID — diseño y verificación

Estado: **verificado en el par S25 (SM-S931B, Android 17) + Galaxy Tab S6 Lite
(SM-P613, API 34) el 2026-11-25**. Es una verificación de ingeniería sobre ese
par, no una certificación de hardware ni una garantía para otros dispositivos.

## 1. Resumen

La tablet captura todos los punteros del panel y los manda al relay. Hay dos
caminos a propósito:

| Puntero | Camino en el S25 | Resultado |
|---|---|---|
| Dedos | `TYPE_INJECT_TOUCH` (protocolo clásico de scrcpy) | multi-touch, toolType FINGER |
| S Pen / borrador | dispositivo HID virtual por `TYPE_UHID_CREATE/INPUT` | toolType STYLUS/ERASER, presión 0..4095, tilt, hover |

El lápiz **no** se inyecta como touch: Android lo trata como un digitizador Pen
real (`INPUT_PROP_DIRECT`), que es lo que permite presión, inclinación,
orientación y borrador en apps de dibujo. Se usa el `scrcpy-server` 5.0.1
**stock**; no hay fork GPLv3 ni parche al servidor.

Archivos:

- `viewer/.../PenForwarder.kt` — captura MotionEvents y habla el protocolo.
- `viewer/.../PenProbeActivity.kt` — app de medición en el display virtual.
- `runtime/termux/pen_handler.py` — descriptor HID, reportes y rechazo de palma.
- `runtime/termux/server.py` — dispatch `pd/pm/pu/ph` y `TYPE_START_APP`.
- `runtime/termux/tests/test_pen_handler.py` — 24 tests del camino UHID.

## 2. Cadena completa

```
Tablet: S Pen/borrador/dedo
  └─ PenForwarder.kt (onTouch / onGenericMotion)
      └─ WS TLS  wss://<hotspot>:8443/ws   {"t":"pd|pm|pu|ph", ...}
          └─ server.py  →  pen_handler.handle()
              ├─ dedo  → scrcpy control TYPE_INJECT_TOUCH
              └─ pluma → scrcpy control TYPE_UHID_CREATE / UHID_INPUT
                  └─ /dev/uhid (uid shell) → kernel hid-input
                      └─ InputReader del S25 → app del display virtual
```

El display virtual se crea con `NEW_DISPLAY=2000x1200/240` y se puebla con
`TYPE_START_APP` (16) para evitar la carrera que dejaba el display vacío y el
viewer en verde.

## 3. Protocolo viewer → relay

Un mensaje JSON por puntero y por muestra:

```json
{"t":"pd","a":0,"pid":0,"x":800,"y":500,"p":0.6250,
 "pt":"stylus","tool":"stylus","tl":0.3,"or":-1.0}
```

- `t`: `pd` down, `pm` move, `pu` up/cancel, `ph` hover.
- `a`: acción scrcpy. `0` down, `1` up, `2` move, `3` cancel,
  `7` hover enter/move, `10` **hover exit**.
- `pt`: `stylus` o `finger` (compatibilidad con el protocolo anterior).
- `tool`: `stylus`, `eraser` o `finger`.
- `p`: presión normalizada 0..1 (el relay la escala a 0..4095).
- `tl`: tilt en radianes (0..π/2 desde la perpendicular).
- `or`: orientación/azimut en radianes.

`PenForwarder` reenvía también las muestras históricas de `MotionEvent`
(~120 Hz) para que el trazo no dependa de los 60 fps del video.

## 4. Descriptor HID y mapeo en Android

Descriptor: Usage Page `0x0D` (Digitizers), Usage `0x02` (Pen), VID/PID
`0x1209/0x0001`, nombre `WTM Pen`.

`hid-input` lo mapea a:

| Campo HID | Eje/tecla Linux |
|---|---|
| Tip Switch | `BTN_TOUCH` |
| In Range | `BTN_TOOL_PEN` |
| Eraser | `BTN_TOOL_RUBBER` |
| Barrel Switch | `BTN_STYLUS` |
| Tip Pressure | `ABS_PRESSURE` 0..4095 |
| X/Y Tilt | `ABS_TILT_X/Y` −90..90 |
| X/Y absolutas | `ABS_X/ABS_Y` con máximos independientes |

El `application=Pen` es lo que hace que el kernel marque
`INPUT_PROP_DIRECT`; sin él Android lo trataría como touchpad. En Android ≥ 15
el `UhidManager` del servidor asocia el dispositivo al display virtual por el
puerto `scrcpy:<pid>`. En el S25 aparece como:

```
WTM Pen
  Sources: KEYBOARD | TOUCHSCREEN | STYLUS
  AssociatedDisplayUniqueIdByPort: virtual:com.android.shell,2000,scrcpy,...
  Touch Input Mapper ... mode - DIRECT
```

## 5. Ciclo de vida del hover y el bug del táctil bloqueado

Síntoma de campo (2026-11-25): tras usar el S Pen, el táctil y el mouse del
display virtual quedaban muertos hasta cambiar de app. Causa: el hover de la
pluma mantenía el dispositivo UHID **in-range para siempre**. Android suprime
táctil y mouse mientras un digitizador Pen está en rango (el mismo
comportamiento de una Galaxy Tab real con EMR).

Solución en dos capas:

1. **Camino rápido**: `PenForwarder` reenvía `ACTION_HOVER_EXIT` como
   `ph a=10`; `pen_handler` manda un reporte con `in_range=0` **al instante**.
2. **Respaldo**: cada hover/UP arma un temporizador
   `WTM_PEN_OOR_MS` (1000 ms por defecto). Un down/move lo cancela. Si el
   `HOVER_EXIT` se pierde, el táctil vuelve igual.

Evidencia `getevent -lt /dev/input/event14` del S25:

```
[57714.676181] EV_KEY BTN_DIGI DOWN     # hover enter
[57715.477969] EV_KEY BTN_DIGI UP       # HOVER_EXIT (inmediato)
[57716.280448] EV_KEY BTN_DIGI DOWN     # segundo hover
[57717.300590] EV_KEY BTN_DIGI UP       # respaldo, 1.02 s después
```

Mientras la pluma se mueve en hover, el táctil se bloquea: es el
comportamiento nativo de una Galaxy Tab. Al alejarla, `HOVER_EXIT` lo libera
de inmediato.

Variables:

- `WTM_PEN_OOR_MS` (default `1000`): respaldo de fuera-de-rango.
- `WTM_PALM_WINDOW_MS` (default `600`): ventana del rechazo de palma del relay.

## 6. Tilt y orientación

Android reconstruye (`frameworks/native/.../TouchInputMapper.cpp`):

```
orientation = atan2(-sin(tiltX), sin(tiltY))
tilt        = acos(cos(tiltX) * cos(tiltY))
```

El relay invierte exactamente ese sistema. Con `a=sin(tiltX)`, `b=sin(tiltY)`
y `R²=a²+b²`:

```
a = -R·sin(orientación)
b =  R·cos(orientación)
denom·R⁴ - R² + sin²(tilt) = 0,  denom = sin²(or)·cos²(or)
```

Se toma la raíz pequeña; si pide `R>1` (tilt casi plano en diagonal) se satura
conservando la orientación, porque dos ejes HID no alcanzan ese punto.

Verificación en PenProbe: pedido `tl=0.5 rad (28.65°)`, `or=-1.0 rad
(−57.30°)`; medido `tilt=28.06°`, `orientation=−57.53°`. El error de 0.58° es
el redondeo a grados enteros del HID.

## 7. Rechazo de palma (tres capas)

1. **Viewer**: descarta `TOOL_TYPE_PALM` (5), honra `FLAG_CANCELED` (API 33+),
   ignora dedos 800 ms tras el último contacto de stylus y libera con
   `a=3` los dedos que ya estaban abajo.
2. **Relay**: si un dedo se mueve con stylus activo, lo suelta con un UP y no
   reenvía sus movimientos (`WTM_PALM_WINDOW_MS`, 600 ms).
3. **Android**: el hover activo de la pluma suprime el táctil, igual que en
   hardware Samsung.

Verificación local: `input stylus` en la tablet (x=500) llegó al relay con
`tool=stylus`; un `input touchscreen tap` inmediato (x=700) **no llegó**
(0 mensajes). En el relay, un dedo en (150,150) con stylus activo fue
rechazado y PenProbe mostró el último evento `STYLUS MOVE` sin puntero de dedo.

## 8. PenProbe

Activity exportada del APK viewer, para medir en el display virtual:

```sh
DID=$(adb -s <phone> shell dumpsys display \
  | grep -oE "displayId=[0-9]+, uniqueId='virtual:[^']*scrcpy[^']*'" \
  | head -1 | grep -oE '[0-9]+' | head -1)
adb -s <phone> shell am start --display "$DID" \
  -n ai.ashna.wtmviewer/.PenProbeActivity
```

Muestra action, toolType, presión 0..4095, tilt, orientación, flags,
`CANCELED`, punteros e historial, y dibuja el trazo con ancho por presión.

Captura del display virtual (usar el id de **SurfaceFlinger**, no el lógico):

```sh
adb -s <phone> shell dumpsys SurfaceFlinger --display-id | grep -i scrcpy
adb -s <phone> exec-out screencap -d <sf-id> -p > vd.png
```

Test UHID sin dibujar: enviar `{"t":"pen_test"}` por `/ws` autenticado. El relay
crea el dispositivo y emite 180 reportes sintéticos.

## 9. Firma estable del APK

Cada run de CI generaba un keystore debug distinto, así que actualizar el APK
exigía desinstalar y restaurar prefs. Ahora el keystore vive **fuera del repo**:

- `~/.config/wtm/wtm-viewer.p12` (PKCS12, 600) y `keystore.env` (600).
- Secrets de GitHub: `WTM_KEYSTORE_BASE64`, `WTM_KEYSTORE_PASSWORD`,
  `WTM_KEY_ALIAS`, `WTM_KEY_PASSWORD`.
- `viewer-apk.yml` lo decodifica a `$RUNNER_TEMP` y `app/build.gradle` firma
  `debug` con `signingConfigs.stable` si `WTM_KEYSTORE_FILE` existe.
- `apksigner verify --print-certs` en CI imprime el SHA-256 del certificado.

La primera instalación tras el cambio de firma requiere desinstalar una vez.
Después, `adb install -r` funciona entre builds. En la tablet, para no perder
token/URL: respaldar `shared_prefs/wtm.xml` con `run-as`, desinstalar,
instalar, lanzar una vez, force-stop, restaurar el XML y relanzar.

## 10. Verificación reproducible

```sh
# 1) Tests del relay (sin teléfono)
cd runtime/termux
python3 -m unittest tests.test_pen_handler tests.test_server_security tests.test_server_live

# 2) Que Android vea el stylus
adb -s <phone> shell dumpsys input | grep -A25 'WTM Pen'
adb -s <phone> shell getevent -pl | grep -A12 'WTM Pen'

# 3) Que el hover salga de rango
adb -s <phone> shell 'timeout 8 getevent -lt /dev/input/event14'
# (mientras corre, enviar ph a=7 y ph a=10 por /ws)

# 4) PenProbe: toolType STYLUS, presión, tilt y orientación
```

## 11. Limitaciones conocidas

- El hover activo bloquea táctil/mouse por diseño (comportamiento Galaxy Tab).
- Un hover estático sin `HOVER_EXIT` cae por el respaldo de 1 s; no es
  exactamente el bloqueo indefinido de una EMR física, pero evita el bug.
- La latencia instrumentada está en [latency.md](latency.md): `lat` (PTS
  scrcpy→llegada) y `dec` (llegada→render). El pen-to-photon completo
  (incluye scanout y dibujo de la app) aún requiere ground truth con cámara.
- La verificación es sobre el par S25 + SM-P613; otros dispositivos pueden
  mapear tilt/eraser distinto.
