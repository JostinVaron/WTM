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
| PhotoCraft | `eframe::App` propio (`crates/ui-egui`) | `wtm-pen-app.sh photocraft` |
| VectorCraft | ídem | `wtm-pen-app.sh vectorcraft` |
| FilmCraft / EffectCraft | ídem | `wtm-pen-app.sh <app>` |
| LightCraft | `eframe::App` propio (`apps/lightcraft`) | instalado; `wtm-pen-app.sh lightcraft` |
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

## PhotoCraft / VectorCraft: App propio en `crates/ui-egui`

Cada app de la suite trae su propio `impl eframe::App` (como LightCraft), con
el `raw_input_hook` en `crates/ui-egui/src/app.rs`; no usan el `Driver`
compartido de `craft-ui-shell` (ese driver existe para el gallery y otros
hosts). Por eso el parche es por app, pero automático:

```sh
~/.config/wtm/bin/wtm-pen-app.sh photocraft
# clona (o reusa) el repo, localiza el raw_input_hook real,
# copia pen_bridge.rs al crate que lo contiene, lo declara en lib.rs
# (tras los //! del crate), añade la dep Linux y llama start()/inject()
# en el hook; luego cargo build -p <app> e instala con backup .pre-pen
```

El patcher excluye `tests/`, `target/` y el shell `*-web`, y prefiere
`crates/ui-egui/src/app.rs`. Si el hook ya tiene `pen_bridge::inject`, no
hace nada (idempotente).

### Conflicto objc2 (por qué no va en egui-winit)

`rust-motion-tablet` 1.1 fija `objc2 =0.6.4` (solo la rama macOS) y las apps
fijan `=0.6.5`: Cargo resuelve la rama macOS aunque compile para Linux y
falla. El script vendoriza el crate en
`~/artcraft-src/rust-motion-tablet-1.1.0`, relaja el pin a `0.6` (misma API;
esa rama no se compila en Linux) y añade `[patch.crates-io]`. Es el mismo
conflicto que cerró `storytold/egui#61`, resuelto fuera de las apps.

### Parche compartido opcional (`craft-libs`)

Para hosts que sí usan el driver `craft-ui-shell`, el mismo puente está
propuesto upstream: `storytold/craft-libs#3647` (piezas
`wtm-pen-shared.sh` y `pen_bridge_ui_shell.rs`). No es el camino de
Photo/Vector hoy; es el que dejará el fix en la librería cuando se mergee.
