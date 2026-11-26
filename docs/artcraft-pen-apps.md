# S Pen en las apps de diseño — estado real y operación

Estado 2026-11-26. El lápiz ya funciona en Linux/X11. Lo que faltaba no era
soporte en las apps, sino que el dispositivo `Lorie pen` existiera mientras se
dibuja.

## Cómo llega la presión

1. El S Pen se relaya por UHID a Termux:X11 (`pen_handler.py`) y el servidor X
   crea `Lorie pen` con `Abs Pressure` (0..65535), `Abs Tilt X/Y` y
   `Abs Rotary Z`.
2. `Lorie pen` **solo aparece cuando hay un evento de stylus**: el primer trazo,
   o la pre-creación al arrancar el relay (`_precreate_pen`, commit `9be209d`).
3. Las apps lo leen por **XInput2 crudo en una segunda conexión X** (x11rb),
   porque winit descarta esos valuadores. Ese es el punto que LightCraft no
   tenía y que sí traen PhotoCraft y VectorCraft.

## Matriz

| App | Soporte del lápiz | Acción |
|---|---|---|
| PhotoCraft 0.23.1 | nativo: crate `photocraft-tablet` (XInput2 raw, hotplug) | ninguna; dibujar |
| VectorCraft 0.24.1 | nativo: `apps/vectorcraft/src/tablet.rs` (XInput2 raw, hotplug) | ninguna; dibujar |
| FigCraft 0.18.1 | sin backend; su pluma lee `Touch{force}` | puente instalado en `/usr/bin/figcraft` |
| AniCraft 0.2.1 | sin backend; su tinta lee `Touch` | puente instalado en `/usr/bin/anicraft` |
| DesignCraft 0.21.1 | sin herramienta de presión | puntero normal; sin puente |
| LightCraft | sin backend de tableta | puente app-level ya instalado en `/usr/bin/lightcraft` |
| Blender 5.0.1 | nativo: XInput2 (GHOST) | abrirlo con `Lorie pen` ya presente |
| Krita | nativo: XInput2 | ninguna |
| Figma | web (Pointer Events del navegador) | depende del navegador |

- La presión viene **activada por defecto** (`use_tablet_pressure: true`).
- PhotoCraft y VectorCraft escuchan `HIERARCHY`/`DEVICE_CHANGED`, así que el
  lápiz puede aparecer con la app ya abierta.

## Si el lápiz no responde

```sh
DISPLAY=:0 xinput list --long "Lorie pen"   # debe listar Pressure/Tilt/Rotary
```

Validado: tras reiniciar relay + widget `Ubuntu`, `Lorie pen` existe desde el
arranque (la pre-creación UHID ocurre antes de lanzar la activity). Sin
reinicio completo, el primer trazo con el S Pen lo crea. En Blender hay que reabrir la ventana después del trazo:
GHOST enumera los tabletas al arrancar. Tras reiniciar el relay, la
pre-creación lo deja listo desde el inicio.

Verificación por app:

- FigCraft: herramienta Pluma/Pencil; FigCraft lee `Touch{force}` y su perfil
  de presión cambia el ancho del trazo.
- PhotoCraft: elige el pincel, baja `Size jitter`/usa presión y dibuja; el
  tamaño cambia con la fuerza.
- VectorCraft: igual, en el pincel de trazo.
- LightCraft: foto → panel **Detail/Masks** → dibujar.
- Blender: Sculpt (Draw) o Grease Pencil; el radio cambia con la presión.
- Krita: cualquier preset con presión.

## Palma

`wtmpen guard` corre en la sesión y desactiva `Lorie touch` mientras el lápiz
está en rango (medido: 0 `TouchBegin` durante el hover). Android y el viewer
también filtran la palma. `wtmpen status` muestra el estado.

## Apps con puente app-level

LightCraft, FigCraft y AniCraft no traen backend de tableta, así que se les
instaló el puente `pen_bridge` (XInput2 con `rust-motion-tablet` →
`egui::Event::Touch{force}`) en su `raw_input_hook`. Cada binario conserva su
original como `.pre-pen`:

```sh
ls /usr/bin/*.pre-pen
cp -p /usr/bin/lightcraft.pre-pen /usr/bin/lightcraft   # restaurar una
```

El aplicador `wtm-pen-app.sh` sirve para cualquier app sin backend. **No** se
usa en PhotoCraft/VectorCraft: duplicaría los eventos que ya entregan.

## Figma

Es web: la presión llega por Pointer Events del navegador. La imagen no trae
Chromium/Chrome instalado; Figma corre en el navegador que uses (o se instala
uno en el proot). No pasa por el puente de las apps Craft.

## Historial

- `storytold/lightcraft#4751`: puente app-level del POC.
- `storytold/egui#61`: cerrado por el pin `objc2` de `rust-motion-tablet`.
- `storytold/craft-libs#3647`: cerrado; Photo/Vector ya traen su backend y no
  usan el driver compartido, así que ese parche no llegaba a ellas.
