#!/usr/bin/env bash
# Met à jour Scaros sans tout réinstaller, puis relance l'écran.
#   depuis le menu : Réglages > À propos > Mettre à jour Scaros
#   ou à la main :   su -   puis   scaros-update
set -euo pipefail

ARCHIVE="https://github.com/belugas62/Neo/archive/scaros-os.tar.gz"
STATUS_DIR=/var/lib/scaros
[ "$(id -u)" -eq 0 ] || { echo "Lance la mise à jour en root : tape « su - » puis « scaros-update »."; exit 1; }

# étape en cours, lue par le menu (download, install, done, failed)
status() {
  mkdir -p "$STATUS_DIR"
  printf '{"step": "%s", "at": %s}\n' "$1" "$(date +%s)" > "$STATUS_DIR/update-status.json.tmp"
  chmod 644 "$STATUS_DIR/update-status.json.tmp"
  mv -f "$STATUS_DIR/update-status.json.tmp" "$STATUS_DIR/update-status.json"
}

# 1) télécharge la dernière version, puis laisse SA copie de ce script faire la suite :
#    ainsi une mise à jour applique toujours les dernières étapes (nouveaux paquets…)
if [ -z "${SCAROS_SRC:-}" ]; then
  status "download"
  tmp="$(mktemp -d)"
  echo "Téléchargement de la dernière version…"
  if ! wget -q -O "$tmp/scaros.tar.gz" "$ARCHIVE" || ! tar -xzf "$tmp/scaros.tar.gz" -C "$tmp"; then
    rm -rf "$tmp"; status "failed"; echo "Téléchargement raté : vérifie la connexion. Rien n'a été modifié."; exit 1
  fi
  src="$tmp/Neo-scaros-os/scaros-os"
  if [ ! -f "$src/update.sh" ] || [ ! -f "$src/launcher/scaros.py" ]; then
    rm -rf "$tmp"; status "failed"; echo "Archive inattendue : rien n'a été modifié."; exit 1
  fi
  SCAROS_SRC="$src" SCAROS_TMP="$tmp" exec bash "$src/update.sh"
fi
src="$SCAROS_SRC"
on_exit() {
  local rc=$?
  [ "$rc" -eq 0 ] || status "failed"
  rm -rf "$SCAROS_TMP"
}
trap on_exit EXIT
status "install"

# 2) paquets ajoutés depuis l'installation
NEW_PACKAGES=(
  libspa-0.2-bluetooth
  # AirPlay : recopie d'écran depuis un iPhone (UxPlay et le décodage vidéo GStreamer)
  uxplay avahi-daemon avahi-utils gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav
  gstreamer1.0-x gstreamer1.0-gl gstreamer1.0-pulseaudio
  # volume et notifications affichés par-dessus les applis
  python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-rsvg-2.0 fontconfig
  # diagnostic réseau (Réglages › Wi-Fi › Diagnostic réseau)
  tcpdump iputils-arping iw
)
missing=()
for pkg in "${NEW_PACKAGES[@]}"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "Installation de : ${missing[*]}…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends "${missing[@]}"
  [[ " ${missing[*]} " == *" libspa-0.2-bluetooth "* ]] && need_reboot=1
fi

# AirPlay : l'iPhone trouve Scaros grâce à Avahi (Bonjour) ; il doit tourner
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true

# sans économie d'énergie Wi-Fi, le portable reste joignable (télécommande, AirPlay, SSH) :
# sinon la carte s'assoupit et le téléphone ne le trouve plus au bout d'un moment
mkdir -p /etc/NetworkManager/conf.d
printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/scaros-wifi.conf
for dev in /sys/class/net/wl*; do
  [ -e "$dev" ] && iw dev "$(basename "$dev")" set power_save off 2>/dev/null || true
done
wifi_modprobe="# Scaros : la carte Wi-Fi ne s'assoupit jamais (cartes Realtek et Intel)
options rtw88_core disable_lps_deep=y
options rtw89_core disable_ps_mode=y
options iwlmvm power_scheme=1"
# réglage des pilotes : pris en compte au prochain démarrage (redémarrage si la carte est concernée)
if [ "$(cat /etc/modprobe.d/scaros-wifi.conf 2>/dev/null)" != "$wifi_modprobe" ]; then
  printf '%s\n' "$wifi_modprobe" > /etc/modprobe.d/scaros-wifi.conf
  if grep -qE '^(rtw88_core|rtw89_core|iwlmvm) ' /proc/modules; then need_reboot=1; fi
fi

# Bluetooth : allumé au démarrage, reconnexion rapide, réappairage sans question des appareils connus
main_conf=/etc/bluetooth/main.conf
if [ -f "$main_conf" ]; then
  sed -E 's/^#?[[:space:]]*FastConnectable[[:space:]]*=.*/FastConnectable = true/;
          s/^#?[[:space:]]*JustWorksRepairing[[:space:]]*=.*/JustWorksRepairing = always/;
          s/^#?[[:space:]]*AutoEnable[[:space:]]*=.*/AutoEnable=true/' "$main_conf" > "$main_conf.scaros"
  if cmp -s "$main_conf" "$main_conf.scaros"; then
    rm -f "$main_conf.scaros"
  else
    mv -f "$main_conf.scaros" "$main_conf"
    systemctl restart bluetooth >/dev/null 2>&1 || true
  fi
fi
[ -f /etc/modprobe.d/scaros-xbox.conf ] || echo "options bluetooth disable_ertm=Y" > /etc/modprobe.d/scaros-xbox.conf
systemctl enable bluetooth >/dev/null 2>&1 || true

# applis ajoutées depuis l'installation
NEW_FLATPAKS=(com.valvesoftware.SteamLink)
for app in "${NEW_FLATPAKS[@]}"; do
  if ! flatpak info "$app" >/dev/null 2>&1; then
    echo "Installation de $app (quelques minutes)…"
    flatpak install -y --noninteractive flathub "$app" || echo "Installation de $app ratée : relance scaros-update plus tard."
  fi
done
# décodage vidéo par la puce Intel pour les nouveaux runtimes
flatpak list --runtime --columns=application,branch 2>/dev/null | awk '$1=="org.freedesktop.Platform" {print $2}' | sort -u |
  while read -r branch; do
    flatpak install -y --noninteractive flathub "org.freedesktop.Platform.VAAPI.Intel//$branch" >/dev/null 2>&1 || true
  done

write_polkit_rule() {
  # le compte de la box gère Wi-Fi, Bluetooth, alimentation, applis et mises à jour sans mot de passe
  mkdir -p /etc/polkit-1/rules.d
  cat > /etc/polkit-1/rules.d/50-scaros.rules <<EOF
// Scaros OS : le compte « $1 » pilote le Wi-Fi, l'alimentation, les applis et les mises à jour depuis le menu.
polkit.addRule(function (action, subject) {
  if (subject.user != "$1") return;
  if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 ||
      action.id.indexOf("org.freedesktop.login1.") == 0 ||
      action.id.indexOf("org.freedesktop.Flatpak.") == 0) {
    return polkit.Result.YES;
  }
  if (action.id == "org.freedesktop.systemd1.manage-units" &&
      (action.lookup("unit") == "scaros-update.service" || action.lookup("unit") == "scaros-diag.service") &&
      action.lookup("verb") == "start") {
    return polkit.Result.YES;
  }
});
EOF
}
USER_NAME="$(getent passwd 1000 | cut -d: -f1 || true)"
[ -n "$USER_NAME" ] && write_polkit_rule "$USER_NAME"

# service lancé par le menu (Réglages > À propos > Mettre à jour Scaros)
cat > /etc/systemd/system/scaros-update.service <<'EOF'
[Unit]
Description=Mise à jour de Scaros OS
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/scaros-update
TimeoutStartSec=1800
EOF
# diagnostic réseau lancé depuis le menu (Réglages › Wi-Fi › Diagnostic réseau), en root
cat > /etc/systemd/system/scaros-diag.service <<'EOF'
[Unit]
Description=Diagnostic réseau de Scaros OS

[Service]
Type=oneshot
ExecStart=/usr/local/bin/scaros-diag --service
TimeoutStartSec=180
EOF
systemctl daemon-reload

# Spotify n'est plus dans Scaros
rm -f /usr/local/bin/spotifyd

# 3) fichiers de Scaros
mkdir -p /opt/scaros
rm -rf /opt/scaros/launcher /opt/scaros/system
cp -r "$src/launcher" "$src/system" /opt/scaros/
chmod 755 /opt/scaros/system/session.sh /opt/scaros/launcher/scaros.py /opt/scaros/launcher/osd.py
install -m 755 "$src/update.sh" /usr/local/bin/scaros-update
install -m 755 "$src/system/scaros-diag.sh" /usr/local/bin/scaros-diag
# version installée (numéro de la version GitHub, lu dans l'archive), comparée par le menu
python3 -I -c 'import sys, tarfile; print(tarfile.open(sys.argv[1]).pax_headers.get("comment", ""))' \
  "$SCAROS_TMP/scaros.tar.gz" > /opt/scaros/commit 2>/dev/null || rm -f /opt/scaros/commit
# polices du menu, aussi pour l'affichage par-dessus les applis
mkdir -p /usr/local/share/fonts/scaros
cp "$src"/launcher/ui/fonts/*.woff2 /usr/local/share/fonts/scaros/ 2>/dev/null && fc-cache -f >/dev/null 2>&1 || true

# écran de démarrage (logo, animation) : remplacé s'il a changé
theme=/usr/share/plymouth/themes/scaros
if [ -d "$theme" ] && ! diff -rq "$src/system/plymouth/scaros" "$theme" >/dev/null 2>&1; then
  echo "Nouvel écran de démarrage…"
  rm -rf "$theme"
  cp -r "$src/system/plymouth/scaros" "$theme"
  update-initramfs -u >/dev/null 2>&1 || true
fi

status "done"
if [ -n "${need_reboot:-}" ]; then
  echo "Scaros est à jour. Le PC redémarre pour activer les nouveautés…"
  systemctl reboot
else
  echo "Scaros est à jour. L'écran redémarre…"
  systemctl restart getty@tty1
fi
