# Limpieza de bloatware y rendimiento (Ubuntu/GNOME en Termux:X11)

Fecha: 2026-11-26. Ámbito: apps y servicios hoja, autostart, animaciones.
No se tocó el core de GNOME, Settings, red, geoclue ni las apps de trabajo
(ArtCraft, Blender, Krita, Claude Desktop/Code, VS Code) ni los temas.

## Resultado medido (S25, sesión GNOME 49 X11, 2026-11-26)

- **74 paquetes purgados**: 42 apps + 32 servicios (listas exactas en
  `/root/wtm-backups/purged-2026-11-26.txt` y
  `/root/wtm-backups/purged-services-2026-11-26.txt`).
- **Disco**: ~1.2–1.5 GB liberados según `dpkg` (no se guardó `df` previo;
  `df` posterior: 52 GB libres de 219 GB, 77% usado).
- **RAM del escritorio**: ~370 MB RSS (`gnome-shell` 210, `nautilus` 82,
  `mutter` 64, `gsd-*` 31, `pulseaudio` 10). El `free` del teléfono marca
  7464/11113 MB usados; Android + `opencode` (318+256 MB) + Termux dominan,
  no el escritorio.
- **GPU**: `OpenGL renderer: zink Vulkan 1.4(Adreno (TM) 830v1
  (MESA_TURNIP))`; `glxgears` **526 FPS** con `vblank_mode=0`; el stream
  scrcpy subió a **54.8 fps** bajo movimiento (glxgears), con 4 IDR.
  El warning `MESA-EGL: DRI3 error` es benigno: no hay fallback a
  llvmpipe; el shell mapea `libvulkan_freedreno` y `/kgsl-3d0`.
- **Estabilidad**: `wtm-stability-check.sh` **PASS=12 FAIL=0 WARN=0**
  (12:06), 6 clicks/6 releases touch, 15 eventos XI2 de lápiz, 0
  `Gjs-CRITICAL` nuevos, captura 1.3 MB.
- **Mouse**: el Pebble M350s aparece en `dumpsys input` con `DisplayId: 11`;
  `Lorie mouse` enabled; inyección `input mouse -d 11` produce Motion en
  XI2. La prueba **física** del usuario queda pendiente.

## Apps purgadas (42)

`baobab`, `deja-dup`, `evolution-data-server`, `fonts-noto-cjk`,
`gnome-calculator`, `gnome-calendar`, LibreOffice completo, `locales-all`,
`python3-uno`, `remmina*`, `rhythmbox*`, `seahorse`, `shotwell`,
`simple-scan`, `snapd`, `thunderbird`, `transmission-gtk`.

## Servicios purgados (32)

`apport*`, `avahi-daemon`, `bluez-cups`, `bolt`, `brltty`, `cups`,
`cups-pk-helper`, `fwupd`, `gnome-clocks`, `gnome-software*`,
`gstreamer1.0-packagekit`, `hplip`, `ipp-usb`, `libnss-mdns`,
`modemmanager`, `orca`, `packagekit`, `software-properties-common`,
`speech-dispatcher*`, `spice-vdagent`, `switcheroo-control`,
`ubuntu-release-upgrader-gtk`, `ubuntu-report`, `update-manager`,
`update-notifier`, `whoopsie`, `xfce4-power-manager*`.

## Conservado y por qué

- **GNOME Shell 49, mutter, gnome-session, gnome-control-center, nautilus,
  gnome-terminal**: core y UI; paquetes **held** (`hi`), no se tocaron.
- **network-manager** y **gnome-settings-daemon**: red y sesión.
- **geoclue-2.0 / libgeoclue-2-0**: dependencia dura de
  `gnome-settings-daemon`; se desactiva su demo-agent, no se purga.
- **localsearch**: dependencia de `nautilus`; se desactiva su autostart,
  no se purga.
- **Temas** (`papirus-icon-theme`, `bibata-cursor-theme`, `yaru-theme-icon`)
  y wallpapers: look Windows 11/macOS.
- **ArtCraft** (solidcraft, vectorcraft, photocraft, figcraft, anicraft,
  lightcraft, designcraft, pdfcraft, filmcraft, effectcraft, craft-anything,
  gridcraft), **Blender**, **Krita**, **Claude Desktop**, **VS Code**
  (`code`, para Claude Code), **Firefox**, **ibus**, stack GPU
  (mesa/vulkan) y **claude-desktop.desktop** en autostart.

## Autostart desactivado

`localsearch-3`, `geoclue-demo-agent`, `gnome-flashback-geoclue-demo-agent`,
`gnome-flashback-nm-applet`, `nm-applet`, `orca-autostart`,
`org.gnome.SettingsDaemon.DiskUtilityNotify`, `spice-vdagent`,
`ubuntu-advantage-notification`, `ubuntu-report-on-upgrade`,
`update-notifier`, `xfce4-power-manager`; `remmina-applet` movido a
`~/.config/autostart.disabled/`. Se conserva `claude-desktop.desktop`.

## Animaciones

`gsettings set org.gnome.desktop.interface enable-animations false` y
`enable-hot-corners false` (sesión `dexuser`). Revertir:
`gsettings set ... true`.

## Mouse: watchdog read-only

El watchdog ya **no re-aplica** `route-input.sh` cada 60 s. Cada minuto lee
`dumpsys input` (`mouse_display`) y solo re-asocia si el display virtual
cambió o si el `DisplayId` real del mouse difiere del actual. Re-aplicar
una asociación sana podía interferir con el ratón en uso. Marcador
`rd-route.display`; `dex.sh` sigue enrutando al arranque.

## Restaurar

- `/root/wtm-backups/restore-bloat-2026-11-26.sh` (42 apps).
- `/root/wtm-backups/restore-services-2026-11-26.sh` (32 servicios).
- `bash ~/.config/wtm/bin/wtm-cleanup-bloat.sh --restore` ejecuta los
  backups disponibles.

## Reproducir

```
bash ~/.config/wtm/bin/wtm-cleanup-bloat.sh            # dry-run
bash ~/.config/wtm/bin/wtm-cleanup-bloat.sh --apply    # purga + autostart + animaciones
bash ~/.config/wtm/bin/wtm-cleanup-bloat.sh --restore  # reinstala
bash ~/.config/wtm/bin/wtm-stability-check.sh          # PASS/FAIL
```

El script simula, aborta si aparece un paquete core y guarda
`/root/wtm-backups/purged-<fecha>.txt` + `restore-wtm-cleanup-<fecha>.sh`.
Es idempotente: con 0 paquetes no llama a `apt-get purge` vacío.

## Caveats

- `blur-my-shell@aunetx` sigue **desactivada** por estabilidad (el crash
  `MetaInputDeviceX11 ... disposed` + `keyboard.js:1105` se amplificaba con
  la extensión). Reactivar asume riesgo.
- `gnome-software`, `packagekit`, `cups`, `update-manager` fuera: los
  paneles "Software"/"Printers"/"Software Updates" de Settings pueden no
  aparecer; usar `apt` para actualizar/instalar.
- `locales-all` fuera, pero **`es_ES.utf8` sigue disponible** (locale base).
- `gnome-shell`/mutter/gnome-session están **held** (GNOME 49); no hacer
  `apt upgrade` a ciegas.
- La prueba **física** del mouse y la fluidez en el tablet quedan del lado
  del usuario; si el mouse sigue sin responder, capturar
  `getevent -lt /dev/input/event12` mientras se mueve y revisar el cursor
  del viewer.
- El stream a 54.8 fps bajo movimiento es el encoder scrcpy; si el tablet
  se siente lento, el siguiente eje es decode/red del viewer, no la GPU.
