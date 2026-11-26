# Krita 6.0.1 en Ubuntu (Termux:X11): canvas software sin artefactos negros

Verificado en el par S25 (SM-S931B) + Galaxy Tab S6 Lite (SM-P613),
2026-11-26. Krita 6.0.1 (arm64) en el proot `ubuntu-gnome`, X11 `:0`
(Termux:X11), display virtual WTM 2000x1200.

## Síntoma

Con el S Pen, Krita 6.0.1 dejaba **manchas/artefactos negros** en el
trazo. El lápiz y la presión llegaban bien al servidor X (ver
`ubuntu-s-pen.md`); el problema era el canvas OpenGL de Krita sobre
**zink/Adreno**.

## Causa

Krita usa su canvas OpenGL por defecto. En este par el GL llega por la
ruta zink/Adreno (Turnip + zink), y esa combinación con el canvas de
Krita 6.0.1 produce lecturas incompletas de textura que se ven como
negro. No es el lápiz ni el relay: es la aceleración del canvas.

## Fix aplicado (2026-11-26)

Solo afecta a Krita; GNOME y las demás apps siguen usando Adreno.

1. `~/.config/kritadisplayrc` (usuario `dexuser`):
   ```ini
   [General]
   canvasState=OPENGL_SUCCESS
   OpenGLRenderer=none
   ```
2. `~/.config/kritarc`:
   ```ini
   [General]
   canvasAcceleration=false
   ```
3. Wrapper `/usr/local/bin/krita` (queda antes que `/usr/bin/krita` en
   `PATH`):
   ```sh
   #!/bin/bash
   export QT_OPENGL=software
   export LIBGL_ALWAYS_SOFTWARE=1
   export MESA_LOADER_DRIVER_OVERRIDE=llvmpipe
   export GALLIUM_DRIVER=llvmpipe
   exec /usr/bin/krita "$@"
   ```
4. Comprobación:
   ```sh
   which -a krita   # /usr/local/bin/krita primero
   ```

## Reproducir

El script está en `WTM/runtime/termux/ubuntu/wtm-krita-software.sh`.
Desde Termux:

```sh
cp WTM/runtime/termux/ubuntu/wtm-krita-software.sh "$PREFIX/tmp/"
proot-distro login ubuntu-gnome --shared-tmp -- \
  bash /tmp/wtm-krita-software.sh
```

El script hace copia de seguridad de `kritadisplayrc`/`kritarc`
(`*.wtm-bak-*`), escribe la configuración, crea el wrapper y muestra el
resultado. Es idempotente.

## Verificar

1. **Reinicia Krita** (cerrar el documento no basta; el proceso tiene que
   releer `krita`/`kritadisplayrc`).
2. Dibuja con el S Pen: no debe haber manchas negras; la presión debe
   seguir modulando el grosor.
3. `which -a krita` debe mostrar `/usr/local/bin/krita` primero.

Estado: fix aplicado y documentado. La confirmación final es un trazo
real después de reiniciar Krita (prueba del usuario).

## Rollback

Si una versión futura de Krita corrige zink/Adreno:

```sh
rm -f /usr/local/bin/krita
# restaurar las copias:
ls -1 ~/.config/kritadisplayrc.wtm-bak-* ~/.config/kritarc.wtm-bak-*
# o volver a OpenGLRenderer=auto / canvasAcceleration=true
```

## Notas

- El canvas software es más lento que el GL, pero estable. La UI y el
  resto del escritorio no se ven afectados.
- No uses `QT_OPENGL=software` global: solo el wrapper de Krita.
- Si Krita volviera a fallar, revisa primero que el wrapper sea el que
  corre (`which -a krita`) y que el proceso se haya reiniciado.
