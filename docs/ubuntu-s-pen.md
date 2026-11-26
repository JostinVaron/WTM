# S Pen y táctil en Ubuntu (Termux:X11) — estado medido

Verificado en el par S25 (SM-S931B) + Galaxy Tab S6 Lite (SM-P613),
2026-11-26. Termux:X11 `1.03.01-fa3a8b4` corriendo en el display virtual
WTM (2000x1200), GNOME 49 X11 en el proot `ubuntu-gnome`.

## Cadena

```
S Pen tablet → viewer → relay WTM → UHID (display virtual)
  → Android InputReader → Termux:X11 (Lorie pen/eraser)
  → XInput2 → GNOME/Krita/ArtCraft
Dedo tablet → viewer → relay → INJECT_TOUCH → Termux:X11 (Lorie touch;
  touchMode=1: trackpad dentro de X, toque directo en apps Android del display)
Mouse BT   → (directo en el S25) → route-input → Lorie mouse
```

## Medido

- `Lorie pen`: Abs X/Y, **Pressure 0..65535**, **Tilt X/Y −64..63**,
  **Wheel/rotación**, botón tip. Sin clase Proximity (X11 no expone rango).
- Trazo UHID sintético (tiltX 30°, orientación −90°): presión variable,
  Tilt X=33, Wheel=−91, 170 muestras, tip press/release.
- Hover UHID: motion sin presión ni botones.
- Dedo con `touchMode=3` (Direct touch): 78 eventos XI2
  `TouchBegin/Update/End` en `Lorie touch`. **Producción usa `touchMode=1`**
  (trackpad) por estabilidad con GNOME; el toque directo sigue en las apps
  Android del display (fuera de X).
- Krita: trazo con **grosor variable por presión** (captura
  `krita-stroke.png`).
- Mouse BT: ruta `TOOL_TYPE_MOUSE` independiente de `touchMode`; no se ve
  afectado.

## Rechazo de palma (2 capas activas; guard retirado)

1. **Android (medido)**: mientras el stylus **toca**, suprime el touch
   inyectado — test stylus DOWN + swipe de dedo → **0 TouchBegin**.
2. **Viewer + relay (solo contacto)**: el viewer rechaza dedos solo si
   `now < stylusActiveUntil`, que se arma al **tocar** (down/up/cancel) y
   dura `STYLUS_HOLDOFF_MS` = 250 ms. El relay hace lo mismo:
   `_stylus_active_until` se arma solo en `a=0/1/2/3`, **no en hover
   (`a=7`)**. El hover solo mueve el cursor de la pluma y emite `ph a=7`;
   `HOVER_EXIT` (`a=10`) limpia la ventana al instante. `penInRange` del
   viewer ya no descarta dedos; es solo estado/timeout. La palma se filtra
   por `TOOL_TYPE_PALM`/`FLAG_CANCELED`.
3. ~~**Ubuntu — `wtmpen guard`**~~ **RETIRADO (2026-11-26)**: toglear
   `Lorie touch` (`xinput disable/enable`) con la sesión viva dispara
   `MetaInputDeviceX11 ... has been already disposed` en GNOME Shell
   (`keyboard.js:1105`) y, con `blur-my-shell`, buguea el shell. `wtmpen
   guard` no debe arrancarse; se conserva solo como diagnóstico.

### Regresión 2026-11-26 (por qué dejó de funcionar el táctil)

La versión anterior trataba el **hover** como "stylus activo" en el viewer
(`penInRange`) y en el relay (`a=7`): mientras el S Pen estaba cerca,
**todos** los dedos se descartaban. Como el S Pen casi siempre está en la
mano/cerca, el táctil dejó de funcionar; el lápiz (UHID) y el mouse (ruta
física) no pasan por ese filtro, por eso solo ellos respondían. El APK del
viewer quitó su capa, pero el relay seguía con el módulo viejo en memoria
hasta reiniciarlo. Fix definitivo: **el hover no bloquea; solo el contacto
de la pluma + 250 ms**.

## Herramientas

```sh
wtmpen status      # dispositivos y estado
wtmpen test        # monitor en vivo (presión/tilt)
wtmpen guard       # NO usar: retirado (buguea GNOME); solo diagnóstico
wtmpen enable|disable
```

## Arranque e integración

- Widget `~/.shortcuts/Ubuntu`: prefs (`displayScale=100`, `touchMode=1`),
  relay si falta, display virtual, `termux-x11 :0`, activity en el display,
  sesión GNOME con verificación de estabilidad (mismo gnome-shell 4 s) y
  `ubuntu-watchdog`.
- `WTM_START_APP=com.termux.x11` en `server.env`: el relay lanza ese
  **paquete** en el display al crearlo. scrcpy `TYPE_START_APP` espera un
  paquete, **no** `paquete/actividad`: con `com.termux.x11/.MainActivity`
  el log dice `No app found for package "..."`, el display nace en el
  launcher y el relay entra en bucle de reconexión. Verificado:
  `START_APP com.termux.x11` → `Starting app "Termux:X11" [com.termux.x11]
  on display N`. (`am start ... -n com.termux.x11/.MainActivity` sí usa la
  forma `paquete/actividad`; son APIs distintas.)
- **Bus de sistema nuevo**: el socket `system-bus49` sobrevive a la sesión
  pero el daemon/fake-logind mueren con el namespace del proot. Reutilizarlo
  hace que GNOME crashee en `LoginManagerSystemd` ("Connection refused").
  El widget borra socket y stubs muertos antes de arrancar.
- **Pre-creación UHID**: el relay crea el dispositivo en cuanto conoce el
  tamaño de video. Termux:X11 solo expone `Lorie pen` si detecta el stylus
  **al arrancar la activity**; sin esto el primer trazo tras un reinicio se
  perdía y el lápiz no aparecía.
- **Watchdog de activity**: el X server y la sesión viven en procesos
  aparte; si LMKD mata la activity, el escritorio queda congelado. El
  watchdog la relanza en el display actual y protege server/sesión con
  `oom_score_adj=0`. `pgrep` desde Termux no ve la app (UID distinto +
  hidepid); el watchdog usa `adb shell dumpsys`/`pidof`.

## ArtCraft (FilmCraft/LightCraft/VectorCraft/PhotoCraft/EffectCraft)

- Son Rust + **egui/winit + Vulkan**.
- **Causa raíz del lápiz sin presión**: **winit descarta los eventos
  XInput2 de stylus en Linux**. Documentado en los propios issues:
  `storytold/lightcraft#4647` ("winit has no tablet support on Linux.
  nothing egui can see") y `storytold/photocraft#79` (la presión solo llega
  en Windows vía WM_POINTER).
- La presión **sí llega al X server** (medido arriba); se pierde en
  winit/egui. Ningún shim X11 puede inyectarla en un binario que no la lee.
- Solución profesional: integrar **una vez** en el core común de
  `storytold/artcraft` un lector de tableta que winit no expone (crate
  `rust_motion_tablet`, o un lector XInput2 propio) y exponerlo al motor de
  pincel. Requiere **compilar** las apps; los binarios actuales no cambian.
- La palma de ArtCraft está cubierta por las capas 1-3 (Android, viewer,
  relay), sin tocar las apps; el guard de Ubuntu está retirado.

### Hallazgo de código (LightCraft clonado, 2026-11-26)

- La app **ya tiene el camino de presión**:
  `crates/ui-egui/src/panels/detail/masks.rs::pen_force()` lee
  `egui::Event::Touch { force }`; `brush_live.rs` aplica
  `pressure_radius/pressure_flow` por muestra.
- En Linux **winit no emite ningún evento de stylus** (ni puntero ni
  touch), así que `pen_force` devuelve `None` y el pincel va a presión
  plena. En Windows sí llega como touch force.
- Arquitectura compartida: las craft apps usan el **fork `storytold/egui`**
  (`egui-winit`) y `storytold/craft-libs`; el parche va **una vez** en esa
  capa de eventos común (inyectar `egui::Event::Touch` con `force` desde
  XInput2/`rust_motion_tablet`, y los eventos de puntero), no en cada app.
- Los repos son públicos y activos; la cuenta actual solo tiene `pull` en
  `storytold/artcraft`. El camino es fork + PR + build en su CI; compilar
  la suite en el teléfono no es viable.

### POC de presión en LightCraft (2026-11-26)

- PR **storytold/lightcraft#4751**, rama `pen-x11-bridge` en
  `JostinVaron/lightcraft`. CI del fork: run `45756780451` (x86_64).
- `apps/lightcraft/src/pen_bridge.rs`: `rust-motion-tablet` 1.1 abre una
  segunda conexión X11 pura (XI2 raw, funciona durante drags) y entrega
  `Sample { pressure, tilt_x, tilt_y, rotation, eraser }`; en
  `raw_input_hook` se inyecta `egui::Event::Touch { force }`, que la app ya
  consume (`pen_force`/`brush_live`).
- **Build aarch64**: el nightly oficial solo publica `linux-x86_64`; el S25
  es aarch64. Se compila local con rustup **1.100** (apt trae 1.93 < MSRV
  1.95) + deps X11/Vulkan/GTK y `cargo build -p lightcraft`.
  `cargo check` **OK** (4m43s) y `cargo build` **OK** (2m33s): el binario
  corre en la tablet con **Adreno 830v1 (Vulkan)**, biblioteca Camera/616
  fotos y panel de revelado. El log del puente: `XInput2 tablet bridge
  started`.
- **Pre-creación UHID corregida** (WTM `9be209d`): `_precreate_pen` llamaba
  `_ensure(state)` pero la firma es `_ensure(srv, state)`; el UHID no se
  creaba al arrancar y el primer trazo podía perderse. Ahora usa
  `_srv_module()`. El primer toque del S Pen también crea `Lorie pen`.
- **Entrega compartida**: el PR `storytold/egui#61` (egui-winit + XInput2)
  se cerró: `rust-motion-tablet` 1.1 fija `objc2 =0.6.4` y el `eframe` del
  fork main exige `^0.6.5`. Las apps fijan el fork en `6d518fc` (donde
  `^0.6.4` es válido), así que la entrega que funciona es el **módulo
  app-level** (`pen_bridge` + 3 líneas) o un crate `craft-pen` con la
  misma rev; no requiere tocar el motor de pincel.
- **Tilt/rotación**: la muestra los trae y Ubuntu los entrega, pero
  `LiveParams` no tiene ángulo (los dabs son circulares): el pincel actual
  solo usa presión. Para inclinación/rotación hay que modificar el brush
  engine (radio/flow por tilt y rotación de textura), un segundo cambio.

## Pendiente medido

- Verificación con S Pen real en Krita (tu prueba).
- PR/módulo de pen en `storytold/artcraft` + build de la suite.
- Línea recta sintética para descartar curvatura del pipeline (el trazo
  curvado que viste en Krita era el patrón **seno** de `pen_test`, no el
  pipeline).
- Latencia input→tinta dentro de Ubuntu y ground truth con cámara.

## Estabilidad (2026-11-26, verificado en dispositivo)

### Causa raíz de los congelamientos

- El **guard de palma seguía vivo** después de retirarlo del widget: el primer
  `kill` alcanzó solo el wrapper proot y no se verificó; el python interno
  (`python3 /usr/local/bin/wtmpen guard`) siguió togleando `Lorie touch`
  (disable/enable) con cada evento del lápiz.
- Ese cambio de dispositivo dispara en GNOME Shell
  `MetaInputDeviceX11 ... has been already disposed` (`keyboard.js:1105`) y,
  con la extensión `blur-my-shell@aunetx` v78, termina en errores de
  `connections.js`/`popup/index.js` y shell bugueado (medido en
  `shell49.log`, 2026-11-26 16:17 hora proot).
- Prueba reproducible: `xinput disable/enable "Lorie touch"` ×3 → 3
  `Gjs-CRITICAL` nuevos; sin `blur-my-shell` el shell sobrevive (ping OK).

### Cambios aplicados y medidos

- `blur-my-shell@aunetx` **desactivada** (`gnome-extensions disable ...`).
  Cosmética; reactivable con `gnome-extensions enable ...` asumiendo el riesgo.
- Guard **matado y verificado** (sin `python3 /usr/local/bin/wtmpen guard`);
  el widget ya no lo arranca. La palma se filtra en 2 capas: Android
  (stylus suprime touch mientras toca) y viewer/relay (solo contacto de la
  pluma + 250 ms; **el hover no bloquea**).
- `touchMode=1` (Trackpad): el dedo mueve el cursor; el lápiz sigue directo
  con presión; el mouse sigue siendo mouse.
- Mouse: el watchdog **verifica cada 60 s en modo read-only** el `DisplayId`
  real del mouse (`dumpsys input`, parser `mouse_display`); solo re-aplica
  `route-input.sh` si el display virtual cambió o si el mouse declara otro
  display. No se re-aplica una asociación sana: hacerlo cada minuto podía
  interferir con el ratón en uso. Marcador `rd-route.display`; `dex.sh` ya
  no aborta antes de enrutar en arranques lentos.
- Builds `-j3` (`WTM_CARGO_JOBS`) para no disparar el OOM killer.

### Prueba reproducible (sin el asistente)

`bash ~/.config/wtm/bin/wtm-stability-check.sh` (Termux). Mide: healthz, X,
GNOME por D-Bus, ausencia del guard, display scrcpy, mouse asociado,
alternancia 3 taps touch + 3 trazos stylus con presión (conteo XI2),
`Gjs-CRITICAL` nuevos y captura. Corrida 2026-11-26 13:35 (display 16):
**PASS=12 FAIL=0 WARN=0** (6/6 touch, 15 eventos de lápiz, 0 CRITICALs
nuevos, captura 1.33 MB). **Ojo**: el chequeo inyecta con
`input -d <display>`; si el PenProbeActivity u otra app Android está en
primer plano en el display, la inyección va a esa app y el X da 0/0
(falso negativo). Deja `com.termux.x11` al frente antes de correrlo.
Limpieza y rendimiento: `docs/ubuntu-performance-cleanup.md`.
