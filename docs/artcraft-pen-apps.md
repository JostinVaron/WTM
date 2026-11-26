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

## Apps instaladas con lápiz

| App | Estado | Backup |
|---|---|---|
| LightCraft | `/usr/bin/lightcraft` compilado aarch64 (Adreno Vulkan) | `/usr/bin/lightcraft.pre-pen` |
| PhotoCraft | `wtm-pen-app.sh photocraft` (compila/instala) | `/usr/bin/photocraft.pre-pen` |
| VectorCraft | `wtm-pen-app.sh vectorcraft` | `/usr/bin/vectorcraft.pre-pen` |
| FilmCraft / EffectCraft | opcional (vídeo; sin brush de lápiz) | `.pre-pen` |

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
