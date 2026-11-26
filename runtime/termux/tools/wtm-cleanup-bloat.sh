#!/data/data/com.termux/files/usr/bin/bash
# wtm-cleanup-bloat.sh — limpieza reproducible de bloatware del escritorio
# Ubuntu/GNOME en Termux:X11: apps y servicios hoja, autostart y animaciones.
#
# Uso:
#   bash ~/.config/wtm/bin/wtm-cleanup-bloat.sh            # dry-run (seguro)
#   bash ~/.config/wtm/bin/wtm-cleanup-bloat.sh --apply    # aplica
#   bash ~/.config/wtm/bin/wtm-cleanup-bloat.sh --restore  # reinstala backups
#
# Seguridad:
# - Simula la purga y ABORTA si aparece un paquete core (GNOME Shell, mutter,
#   sesión, Settings, nautilus, terminal, red, geoclue, localsearch, temas,
#   ArtCraft/Blender/Krita/Claude/VS Code) en la lista.
# - No purga geoclue (dependencia dura de gnome-settings-daemon) ni localsearch
#   (dependencia de nautilus); desactiva su autostart.
# - Guarda la lista y un script de restauración en /root/wtm-backups.
# - No reinicia ni mata la sesión; los overrides de autostart aplican al
#   siguiente login y las animaciones se aplican en caliente.
set -uo pipefail
export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOME=${HOME:-/data/data/com.termux/files/home}
export PREFIX=${PREFIX:-/data/data/com.termux/files/usr}

APPS="baobab deja-dup evolution-data-server fonts-noto-cjk gnome-calculator gnome-calendar libreoffice-base-core libreoffice-calc libreoffice-common libreoffice-core libreoffice-draw libreoffice-gnome libreoffice-gtk3 libreoffice-impress libreoffice-math libreoffice-style-colibre libreoffice-style-elementary libreoffice-style-yaru libreoffice-uiconfig-calc libreoffice-uiconfig-common libreoffice-uiconfig-draw libreoffice-uiconfig-impress libreoffice-uiconfig-math libreoffice-uiconfig-writer libreoffice-writer locales-all python3-uno remmina remmina-common remmina-plugin-rdp remmina-plugin-secret remmina-plugin-vnc rhythmbox rhythmbox-data rhythmbox-plugin-alternative-toolbar rhythmbox-plugins seahorse shotwell simple-scan snapd thunderbird transmission-gtk"
SERVICES="apport apport-core-dump-handler apport-gtk avahi-daemon bluez-cups bolt brltty cups cups-pk-helper fwupd gnome-clocks gnome-software gnome-software-plugin-deb gstreamer1.0-packagekit hplip ipp-usb libnss-mdns modemmanager orca packagekit software-properties-common speech-dispatcher speech-dispatcher-espeak-ng spice-vdagent switcheroo-control ubuntu-release-upgrader-gtk ubuntu-report update-manager update-notifier whoopsie xfce4-power-manager xfce4-power-manager-plugins"
CORE_RE='^(gnome-shell|gnome-shell-.*|mutter|mutter-.*|gir1\.2-mutter-.*|gnome-session|gnome-session-.*|gnome-control-center|gnome-control-center-.*|nautilus|nautilus-.*|gnome-terminal|gnome-terminal-.*|network-manager|network-manager-.*|gnome-settings-daemon|gnome-settings-daemon-.*|geoclue-2\.0|libgeoclue-.*|gir1\.2-geoclue-.*|localsearch|localsearch-.*|papirus-icon-theme|bibata-cursor-theme|yaru-theme-icon|claude-desktop|code|solidcraft|vectorcraft|photocraft|figcraft|anicraft|lightcraft|krita|krita-data|blender|blender-data|gnome-keyring.*|at-spi.*|im-launch|xdg-user-dirs.*|pulseaudio.*|pipewire.*|dbus.*|xserver-xorg.*|mesa.*|libgl.*|libvulkan.*|libgtk.*|fonts-noto-core|fonts-dejavu.*|locales)$'

MODE="dry-run"
case "${1:-}" in
  --apply) MODE="apply" ;;
  --restore) MODE="restore" ;;
  --dry-run|"") ;;
  *) echo "uso: $0 [--dry-run|--apply|--restore]"; exit 2 ;;
esac

echo "== WTM limpieza bloatware ($MODE) $(date '+%F %T') =="

proot-distro login ubuntu-gnome --shared-tmp -- bash -s -- "$MODE" "$APPS" "$SERVICES" "$CORE_RE" <<'EOS'
set -uo pipefail
MODE=$1; APPS=$2; SERVICES=$3; CORE_RE=$4

if [ "$MODE" = "restore" ]; then
  R=$(ls -1t /root/wtm-backups/restore-wtm-cleanup-*.sh 2>/dev/null | head -1)
  if [ -n "$R" ]; then
    echo "Restaurando con $R"
    bash "$R"
    exit $?
  fi
  FOUND=0
  for s in /root/wtm-backups/restore-bloat-*.sh /root/wtm-backups/restore-services-*.sh; do
    [ -x "$s" ] || continue
    echo "Restaurando con $s"
    bash "$s"
    FOUND=1
  done
  [ "$FOUND" = 1 ] || { echo "No hay backups de restauración en /root/wtm-backups"; exit 1; }
  exit 0
fi

SIM=$(apt-get -s purge $APPS $SERVICES 2>&1 || true)
PURG=$(printf '%s\n' "$SIM" | awk '/^Purg /{print $2}' | sort -u)
N=$(printf '%s\n' "$PURG" | grep -c . || true)
echo "paquetes a purgar: ${N:-0}"

if printf '%s\n' "$PURG" | grep -Eq "$CORE_RE"; then
  echo "ABORTADO: la simulación incluye paquetes core:"
  printf '%s\n' "$PURG" | grep -E "$CORE_RE"
  exit 1
fi
printf '  %s\n' $PURG

if [ "$MODE" != "apply" ]; then
  echo "(dry-run; usa --apply para ejecutar)"
  exit 0
fi

mkdir -p /root/wtm-backups
if [ "${N:-0}" -gt 0 ]; then
  LIST=/root/wtm-backups/purged-$(date +%F).txt
  printf '%s\n' $PURG > "$LIST"
  cat > /root/wtm-backups/restore-wtm-cleanup-$(date +%F).sh <<EOF
#!/bin/bash
# Restaura los paquetes purgados por wtm-cleanup-bloat.sh el $(date +%F).
apt-get update
xargs -a $LIST apt-get install -y
EOF
  chmod 755 /root/wtm-backups/restore-wtm-cleanup-$(date +%F).sh
  DEBIAN_FRONTEND=noninteractive apt-get purge -y $PURG
  echo "purge rc=$?"
else
  echo "nada que purgar (ya limpio)"
fi

A=/home/dexuser/.config/autostart
mkdir -p "$A" /home/dexuser/.config/autostart.disabled
for n in localsearch-3 geoclue-demo-agent gnome-flashback-geoclue-demo-agent gnome-flashback-nm-applet nm-applet orca-autostart org.gnome.SettingsDaemon.DiskUtilityNotify spice-vdagent ubuntu-advantage-notification ubuntu-report-on-upgrade update-notifier xfce4-power-manager; do
  printf '[Desktop Entry]\nType=Application\nName=%s (disabled)\nHidden=true\n' "$n" > "$A/$n.desktop"
done
[ -f "$A/remmina-applet.desktop" ] && mv "$A/remmina-applet.desktop" /home/dexuser/.config/autostart.disabled/
echo "autostart: overrides en $A (claude-desktop conservado)"

SPID=$(ps -eo pid,args 2>/dev/null | awk '$2=="gnome-shell"{print $1; exit}')
if [ -n "${SPID:-}" ] && [ -r "/proc/$SPID/environ" ]; then
  DBUS=$(tr '\0' '\n' < "/proc/$SPID/environ" | sed -n 's/^DBUS_SESSION_BUS_ADDRESS=//p' | head -1)
  if [ -n "${DBUS:-}" ]; then
    HOME=/home/dexuser XDG_CONFIG_HOME=/home/dexuser/.config DBUS_SESSION_BUS_ADDRESS="$DBUS" \
      gsettings set org.gnome.desktop.interface enable-animations false 2>/dev/null || true
    HOME=/home/dexuser XDG_CONFIG_HOME=/home/dexuser/.config DBUS_SESSION_BUS_ADDRESS="$DBUS" \
      gsettings set org.gnome.desktop.interface enable-hot-corners false 2>/dev/null || true
    echo "animaciones/hot-corners: off"
  fi
fi

echo "restore: /root/wtm-backups/restore-wtm-cleanup-$(date +%F).sh"
echo "verifica: bash ~/.config/wtm/bin/wtm-stability-check.sh"
EOS
