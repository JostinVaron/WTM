# S Pen en las craft apps — entrega app-level y operativa

Estado 2026-11-26. Objetivo: que el S Pen funcione con presión en las apps
que usan lápiz y que la operación no dependa de nadie más.

## Por qué app-level (y no solo sistema)

- Linux/X11 **sí** reconoce el lápiz: `Lorie pen` con Abs Pressure 0..65535,
  Tilt X/Y y Abs Rotary Z (medido).
- Quien lo descarta es **winit**: entrega el puntero pero no los valuadores
  del tableta a egui. Las apps ya tienen el camino de presión
  (`pen_force()` lee `egui::Event::Touch { force }`; el brush usa
  `pressure_radius/flow`). Falta la entrega del evento.
- El puente `pen_bridge` (XInput2 con `rust-motion-tablet` 1.1) inyecta ese
  `Touch { force }` en el `raw_input_hook` de la app. No se reescribe el
  brush para presión.
- El PR compartido `storytold/egui#61` (egui-winit) se cerró por conflicto
  de `objc2` (rust-motion-tablet fija 0.6.4; el eframe del fork main exige
  ^0.6.5). Las apps fijan el fork en `6d518fc`, donde `^0.6.4` es válido;
  por eso la entrega app-level es la que funciona hoy.

## Apps de diseño: camino real

| App | Cómo recibe el lápiz | Estado |
|---|---|---|
| PhotoCraft | crate compartida `ui-shell` de `craft-libs` | `wtm-pen-shared.sh photocraft` |
| VectorCraft | ídem (mismo rev de `craft-libs`) | `wtm-pen-shared.sh vectorcraft` |
| FilmCraft / EffectCraft | ídem (mismo driver) | `wtm-pen-shared.sh <app>` |
| LightCraft | `eframe::App` propio en el repo de la app | instalado; `wtm-pen-app.sh lightcraft` |
| Blender 5.0.1 | XInput2 nativo (`GHOST`) | sin parche; el UHID debe existir antes de abrirlo |
| Krita | XInput2 nativo | ya funciona |
| Figma | web (Pointer Events del navegador) | depende del navegador; sin Chromium en la imagen |

Blender y Krita no pasan por winit/egui: leen el tableta XInput2 directo. El
guion de palma (`wtmpen guard`) sigue siendo el mismo para todas.

### Blender y Krita (XInput2 nativo)

- **Krita**: ya recibe presión y tilt; no necesita nada.
- **Blender 5.0.1**: lee el tableta XInput2 al arrancar, así que `Lorie pen`
  debe existir **antes** de abrir Blender:
  ```sh
  DISPLAY=:0 xinput list --long "Lorie pen"   # debe listar Pressure/Tilt/Rotary
  ```
  Si no existe, haz un trazo con el S Pen en la tablet (crea el UHID) y vuelve
  a abrir Blender. Tras reiniciar el relay, `_precreate_pen` lo crea al
  arrancar y Blender lo ve desde el primer inicio.
  Verificación: Sculpt (Draw) o Grease Pencil; el radio debe variar con la
  presión. La palma la filtra `wtmpen guard`.
- **Figma**: es web; la presión llega por Pointer Events del navegador. La
  imagen no trae Chromium/Chrome instalado, así que Figma corre en el
  navegador que uses (o se instala Chromium en el proot). No pasa por el
  puente de las apps Craft.

## Parche único para la suite Craft (`craft-libs`)

PhotoCraft/VectorCraft no tienen su `eframe::App` en su repo: usan la crate
`craft-ui-shell` de `storytold/craft-libs` (rev `5c30761…`), cuyo
`impl<A: ShellApp> eframe::App for Driver<A>` tiene el `raw_input_hook` común.
Ahí vive el puente, una sola vez para PhotoCraft, VectorCraft, FilmCraft y
EffectCraft:

```sh
~/.config/wtm/bin/wtm-pen-shared.sh photocraft
# 1) baja craft-libs en el rev que fija la app
# 2) aplica crates/ui-shell/src/pen_bridge.rs + raw_input_hook + dep Linux
# 3) añade [patch."https://github.com/storytold/craft-libs"] al Cargo.toml
#    de la app apuntando a las crates locales de craft-libs
# 4) cargo build -p <app> e instala en /usr/bin/<app> con backup .pre-pen
```

Piezas: `wtm-pen-shared.sh`, `wtm-pen-shared-patch.py`,
`pen_bridge_ui_shell.rs`. El parche es idempotente y se reaplica tras cada
`git reset` de craft-libs. Para volver a la app sin lápiz:
`cp -p /usr/bin/<app>.pre-pen /usr/bin/<app>`.

`rust-motion-tablet` 1.1 fija `objc2 =0.6.4` (solo la rama macOS) y las apps
fijan `=0.6.5`, así que Cargo no puede resolver aunque compile para Linux.
El script vendoriza el crate en `~/artcraft-src/rust-motion-tablet-1.1.0` y
relaja el pin a `0.6` (misma API; esa rama no se compila en Linux), y añade
`[patch.crates-io]`. Es el mismo conflicto que cerró `storytold/egui#61`,
resuelto aquí sin tocar las apps.

PR upstream del parche compartido: `storytold/craft-libs#3647`
(rama `JostinVaron:feat/pen-bridge`). Mientras se revisa, el script usa el
`[patch]` local de arriba; cuando se mergee, basta subir la rev de
`craft-libs` en la app y quitar la tabla local.

El aplicador app-level (`wtm-pen-app.sh`) sigue sirviendo para apps con
`eframe::App` propio (LightCraft y futuras apps fuera del driver).

## Aplicar a cualquier app

```sh
~/.config/wtm/bin/wtm-pen-app.sh <app>
# clona storytold/<app> (shallow) en ~/artcraft-src/<app>,
# copia pen_bridge.rs, parchea Cargo.toml + main.rs,
# compila `cargo build -p <app>` y copia a /usr/bin/<app>.
# Log: $PREFIX/tmp/wtm-pen-<app>.log
```

- El parche añade `rust-motion-tablet = "1.1"` al target Linux, el módulo
  `apps/<app>/src/pen_bridge.rs`, `mod pen_bridge;`, `pen_bridge::start();`
  y `pen_bridge::inject(raw);` en `raw_input_hook`.
- Si el patcher avisa `sin raw_input_hook`, añade a mano
  `pen_bridge::inject(raw);` en el hook de la app.
- Restaurar el binario original:
  `cp -p /usr/bin/<app>.pre-pen /usr/bin/<app>`.
- Requisitos: Rust 1.100+ vía rustup (`/root/.cargo`) y las deps apt
  X11/Vulkan/GTK. El nightly oficial no publica linux-aarch64; se compila en
  el S25.

## Tilt / rotación

- La muestra del puente trae `pressure, tilt_x, tilt_y, rotation, eraser`.
- LightCraft usa **solo presión** (`LiveParams` no tiene ángulo; dabs
  circulares). Inclinación/rotación en el trazo requieren modificar el brush
  engine de la app (radio/flow por tilt y rotación de textura). Ubuntu y el
  puente ya entregan los ejes; falta el consumo en el pincel de cada app.

## Operativa sin depender de nadie

- **Widgets** en `~/.shortcuts`: `DeX` (relay/wizard), `Ubuntu` (display X +
  GNOME + guard + watchdog), `Exit-Dex`, `Progreso`, `PenTest`.
- **DeX**: verifica ADB 5555 (diálogo guiado si cayó tras reboot), hotspot,
  `server.env`, tablet; actualiza `base_url` de la tablet y lanza el viewer.
- **Ubuntu**: fija `displayScale=100` / `touchMode=3`; arranca `termux-x11 :0`,
  la activity en el display virtual, GNOME (bus nuevo si la sesión murió),
  `wtmpen guard` y `ubuntu-watchdog`; verifica 2000x1200 y estabilidad.
- **Tablet**: WTM Viewer `0.1.7-pen8`; auto-conecta a la `base_url` guardada.
- **Arranque tras reboot**: Termux:Boot `01-dex.sh` levanta watchdog/relay;
  luego toca `DeX` y `Ubuntu` (los widgets hacen el resto).
- **Recuperación**:
  - Hotspot cambia → el wizard actualiza `server.env` y la URL de la tablet.
  - ADB inalámbrico caído → diálogo guiado del wizard (IP:puerto).
  - X/GNOME caídos → widget `Ubuntu` (bus nuevo, display actual).
  - Palma → `wtmpen guard` (desactiva `Lorie touch` mientras hay lápiz).
  - Lápiz no aparece al arrancar → pre-creación UHID corregida (`9be209d`);
    el primer toque también lo crea.
  - LightCraft avisa del lock del catálogo en `/sdcard` → es solo el lock de
    red; OK.

## Verificación rápida

```sh
# dispositivo y ejes
DISPLAY=:0 xinput list --long "Lorie pen"
# presión viva
DISPLAY=:0 wtmpen test
# estado de palma
DISPLAY=:0 wtmpen status
# app de prueba
lightcraft        # o photocraft, vectorcraft
```

En la app: abrir una foto/imagen, entrar al panel de pincel (Detail/Masks en
LightCraft) y dibujar con el S Pen; el grosor debe variar con la presión.
