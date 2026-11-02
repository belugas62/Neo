#!/usr/bin/env bash
# Installe Scaros OS sur un Debian 13 (trixie) minimal, fraîchement installé.
#
#   su -                 # passe en root (mot de passe root choisi pendant l'installation)
#   bash install.sh
#
# Le script peut être relancé sans risque : il refait seulement ce qui manque.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/scaros
FLATPAKS=(rocks.shy.VacuumTube org.jellyfin.JellyfinDesktop tv.plex.PlexHTPC com.valvesoftware.SteamLink)

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; WARNINGS+=("$*"); }
die()  { printf '\033[1;31m[x] %s\033[0m\n' "$*"; exit 1; }
WARNINGS=()

[ "$(id -u)" -eq 0 ] || die "Lance ce script en root : tape « su - », puis « bash $HERE/install.sh »."
[ "$(uname -m)" = x86_64 ] || die "Ce script vise un PC 64 bits (x86_64)."
# shellcheck source=/dev/null
. /etc/os-release
[ "${ID:-}" = debian ] || warn "Script prévu pour Debian 13. Système détecté : ${PRETTY_NAME:-inconnu}."

USER_NAME="${SCAROS_USER:-$(getent passwd 1000 | cut -d: -f1 || true)}"
[ -n "$USER_NAME" ] || die "Aucun utilisateur trouvé. Crée-en un, ou lance : SCAROS_USER=nom bash install.sh"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
echo "Scaros va démarrer automatiquement sur le compte « $USER_NAME »."

export DEBIAN_FRONTEND=noninteractive
apt_install() { apt-get install -y --no-install-recommends "$@"; }

write_polkit_rule() {
  # le compte de la box gère Wi-Fi, Bluetooth, alimentation et mises à jour d'applis sans mot de passe
  mkdir -p /etc/polkit-1/rules.d
  cat > /etc/polkit-1/rules.d/50-scaros.rules <<EOF
// Scaros OS : le compte « $1 » pilote le Wi-Fi, l'alimentation et les applis depuis le menu.
polkit.addRule(function (action, subject) {
  if (subject.user == "$1" &&
      (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 ||
       action.id.indexOf("org.freedesktop.login1.") == 0 ||
       action.id.indexOf("org.freedesktop.Flatpak.") == 0)) {
    return polkit.Result.YES;
  }
});
EOF
}

# ---------------------------------------------------------------------------
step "1/9 Paquets du système (affichage, son, manette, vidéo)"
apt-get update
apt_install \
  xserver-xorg-core xserver-xorg-input-libinput xinit x11-xserver-utils xdotool openbox unclutter-xfixes \
  chromium fonts-noto-core \
  pipewire pipewire-pulse wireplumber pulseaudio-utils alsa-ucm-conf libspa-0.2-bluetooth \
  intel-media-va-driver i965-va-driver vainfo \
  bluez dbus-user-session polkitd \
  python3 python3-evdev python3-qrcode \
  curl ca-certificates wget flatpak plymouth avahi-daemon zram-tools wireless-tools iw openssh-server

# micrologiciels (son Intel SOF, Wi-Fi) : présents si la section non-free-firmware est activée
for fw in firmware-sof-signed firmware-misc-nonfree firmware-iwlwifi firmware-realtek; do
  apt_install "$fw" >/dev/null 2>&1 || warn "Micrologiciel $fw non installé (pas grave s'il n'est pas utile sur ce PC)."
done

# ---------------------------------------------------------------------------
step "2/9 Wi-Fi géré par NetworkManager"
apt_install network-manager
IFACES=/etc/network/interfaces
if grep -qE '^[[:space:]]*wpa-ssid' "$IFACES" 2>/dev/null; then
  ssid="$(sed -nE 's/^[[:space:]]*wpa-ssid[[:space:]]+//p' "$IFACES" | head -n1)"
  psk="$(sed -nE 's/^[[:space:]]*wpa-psk[[:space:]]+//p' "$IFACES" | head -n1)"
  ssid="${ssid%\"}"; ssid="${ssid#\"}"; psk="${psk%\"}"; psk="${psk#\"}"
  conf="/etc/NetworkManager/system-connections/scaros-wifi.nmconnection"
  if [ -n "$ssid" ] && [ ! -f "$conf" ]; then
    umask 077
    {
      printf '[connection]\nid=%s\ntype=wifi\nautoconnect=true\n\n' "$ssid"
      printf '[wifi]\nmode=infrastructure\nssid=%s\n\n' "$ssid"
      if [ -n "$psk" ]; then printf '[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n\n' "$psk"; fi
      printf '[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n'
    } > "$conf"
    umask 022
    cp -n "$IFACES" "$IFACES.avant-scaros"
    # le Wi-Fi passe à NetworkManager au prochain démarrage
    awk '/^(allow-hotplug|auto|iface)[[:space:]]+wl/ {wifi=1} /^(allow-hotplug|auto|iface)[[:space:]]+(lo|en|eth)/ {wifi=0} {print (wifi ? "# " : "") $0}' \
      "$IFACES.avant-scaros" > "$IFACES"
    echo "Wi-Fi « $ssid » repris par NetworkManager (copie de l'ancien réglage : $IFACES.avant-scaros)."
  fi
fi
systemctl enable NetworkManager >/dev/null 2>&1 || true
# sans économie d'énergie Wi-Fi, le portable reste joignable (téléphone, SSH)
mkdir -p /etc/NetworkManager/conf.d
printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/scaros-wifi.conf
for dev in /sys/class/net/wl*; do
  [ -e "$dev" ] && iw dev "$(basename "$dev")" set power_save off 2>/dev/null || true
done

# ---------------------------------------------------------------------------
step "3/9 Applis YouTube, Jellyfin, Plex et Steam Link (Flathub)"
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
for app in "${FLATPAKS[@]}"; do
  if flatpak info "$app" >/dev/null 2>&1; then
    echo "$app déjà installé."
  else
    flatpak install -y --noninteractive flathub "$app" || warn "Installation de $app ratée. Relance install.sh plus tard."
  fi
done
# décodage vidéo par la puce Intel aussi dans les applis Flatpak
flatpak list --runtime --columns=application,branch 2>/dev/null | awk '$1=="org.freedesktop.Platform" {print $2}' | sort -u |
  while read -r branch; do
    flatpak install -y --noninteractive flathub "org.freedesktop.Platform.VAAPI.Intel//$branch" >/dev/null 2>&1 \
      || echo "(pilote vidéo Intel pour le runtime $branch : déjà là ou indisponible)"
  done

# ---------------------------------------------------------------------------
step "4/9 Fichiers de Scaros dans $DEST"
mkdir -p "$DEST"
rm -rf "$DEST/launcher" "$DEST/system"
cp -r "$HERE/launcher" "$HERE/system" "$DEST/"
chmod 755 "$DEST/system/session.sh" "$DEST/launcher/scaros.py"
install -m 755 "$HERE/update.sh" /usr/local/bin/scaros-update
mkdir -p /etc/chromium/policies/managed
cat > /etc/chromium/policies/managed/scaros.json <<'EOF'
{
  "TranslateEnabled": false,
  "PasswordManagerEnabled": false,
  "BrowserSignin": 0,
  "MetricsReportingEnabled": false,
  "PromotionalTabsEnabled": false,
  "DefaultBrowserSettingEnabled": false
}
EOF

# ---------------------------------------------------------------------------
step "5/9 Compte « $USER_NAME » : démarrage automatique de Scaros"
write_polkit_rule "$USER_NAME"
for g in input video render audio bluetooth; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$USER_NAME"
done
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/scaros-autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $USER_NAME --noclear --skip-login --nonewline --noissue %I \$TERM
Type=idle
EOF
profile="$USER_HOME/.bash_profile"
if [ ! -f "$profile" ]; then
  # bash ne lit plus ~/.profile quand ~/.bash_profile existe : on le garde
  printf '[ -f "$HOME/.profile" ] && . "$HOME/.profile"\n' > "$profile"
fi
if ! grep -q '>>> scaros >>>' "$profile"; then
  cat >> "$profile" <<'EOF'

# >>> scaros >>>
# Lance Scaros sur l'écran principal. Pour l'empêcher une fois : crée le fichier ~/.scaros-off
if [ -z "${DISPLAY:-}" ] && [ "$(tty)" = /dev/tty1 ] && [ ! -e "$HOME/.scaros-off" ]; then
  mkdir -p "$HOME/.local/state/scaros"
  exec startx /opt/scaros/system/session.sh -- -keeptty -nolisten tcp >"$HOME/.local/state/scaros/xorg.log" 2>&1
fi
# <<< scaros <<<
EOF
fi
mkdir -p "$USER_HOME/.local/state/scaros"
chown -R "$USER_NAME:$USER_NAME" "$profile" "$USER_HOME/.local"

# ---------------------------------------------------------------------------
step "6/9 Manette Bluetooth et nom réseau « scaros »"
main_conf=/etc/bluetooth/main.conf
if [ -f "$main_conf" ]; then
  sed -i -E 's/^#?[[:space:]]*FastConnectable[[:space:]]*=.*/FastConnectable = true/;
             s/^#?[[:space:]]*JustWorksRepairing[[:space:]]*=.*/JustWorksRepairing = always/;
             s/^#?[[:space:]]*AutoEnable[[:space:]]*=.*/AutoEnable=true/' "$main_conf"
fi
echo "options bluetooth disable_ertm=Y" > /etc/modprobe.d/scaros-xbox.conf
systemctl enable bluetooth >/dev/null 2>&1 || true
if [ "$(hostname)" != scaros ]; then
  old="$(hostname)"
  hostnamectl set-hostname scaros
  sed -i "s/\b$old\b/scaros/g" /etc/hosts
fi
systemctl enable avahi-daemon >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
step "7/9 Portable fermé + télé : pas de mise en veille"
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/scaros.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
cat > /etc/default/zramswap <<'EOF'
ALGO=zstd
PERCENT=50
EOF
systemctl enable zramswap >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
step "8/9 Écran de démarrage Scaros"
rm -rf /usr/share/plymouth/themes/scaros
cp -r "$DEST/system/plymouth/scaros" /usr/share/plymouth/themes/scaros
grep -qx i915 /etc/initramfs-tools/modules || echo i915 >> /etc/initramfs-tools/modules
plymouth-set-default-theme scaros
update-initramfs -u
grub=/etc/default/grub
if [ -f "$grub" ]; then
  set_grub() { if grep -q "^$1=" "$grub"; then sed -i "s|^$1=.*|$1=$2|" "$grub"; else echo "$1=$2" >> "$grub"; fi; }
  set_grub GRUB_TIMEOUT 0
  set_grub GRUB_TIMEOUT_STYLE hidden
  set_grub GRUB_CMDLINE_LINUX_DEFAULT '"quiet splash loglevel=3 rd.udev.log_level=3 systemd.show_status=false vt.global_cursor_default=0"'
  update-grub
else
  warn "GRUB introuvable : l'écran de démarrage n'est pas activé."
fi

# ---------------------------------------------------------------------------
step "9/9 Vérifications"
vainfo --display drm --device /dev/dri/renderD128 2>/dev/null | grep -qi "VAProfileVP9" && echo "Décodage vidéo par la puce Intel : OK (VP9 disponible)." \
  || warn "Le décodage vidéo matériel n'a pas pu être vérifié (normal tant que la session graphique n'a pas démarré)."
for app in "${FLATPAKS[@]}"; do flatpak info "$app" >/dev/null 2>&1 || warn "$app manquant."; done

echo
if [ "${#WARNINGS[@]}" -gt 0 ]; then
  printf '\033[1;33mÀ vérifier :\033[0m\n'
  printf '  - %s\n' "${WARNINGS[@]}"
  echo
fi
printf '\033[1;32mScaros OS est installé.\033[0m\n'
echo "Étape suivante : appairer la manette Xbox."
if [ -t 0 ]; then
  read -r -p "L'appairer maintenant ? Mets-la en mode appairage (bouton au-dessus, 3 s) puis tape Entrée. [O/n] " answer
  if [ "${answer:-o}" != n ] && [ "${answer:-o}" != N ]; then
    systemctl start bluetooth || true
    python3 "$DEST/launcher/scaros.py" --pair && echo "Manette appairée." || echo "Pas trouvée : tu pourras réessayer depuis Paramètres > Manette."
  fi
fi
echo "Redémarre avec la commande : reboot"
