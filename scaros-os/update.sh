#!/usr/bin/env bash
# Met à jour le menu et le serveur de Scaros sans tout réinstaller, puis relance l'écran.
#   su -
#   scaros-update
set -euo pipefail

ARCHIVE="https://github.com/belugas62/Neo/archive/scaros-os.tar.gz"
[ "$(id -u)" -eq 0 ] || { echo "Lance la mise à jour en root : tape « su - » puis « scaros-update »."; exit 1; }

# 1) télécharge la dernière version, puis laisse SA copie de ce script faire la suite :
#    ainsi une mise à jour applique toujours les dernières étapes (nouveaux paquets…)
if [ -z "${SCAROS_SRC:-}" ]; then
  tmp="$(mktemp -d)"
  echo "Téléchargement de la dernière version…"
  if ! wget -q -O "$tmp/scaros.tar.gz" "$ARCHIVE" || ! tar -xzf "$tmp/scaros.tar.gz" -C "$tmp"; then
    rm -rf "$tmp"; echo "Téléchargement raté : vérifie la connexion. Rien n'a été modifié."; exit 1
  fi
  src="$tmp/Neo-scaros-os/scaros-os"
  if [ ! -f "$src/update.sh" ] || [ ! -f "$src/launcher/scaros.py" ]; then
    rm -rf "$tmp"; echo "Archive inattendue : rien n'a été modifié."; exit 1
  fi
  SCAROS_SRC="$src" SCAROS_TMP="$tmp" exec bash "$src/update.sh"
fi
src="$SCAROS_SRC"
trap 'rm -rf "$SCAROS_TMP"' EXIT

# 2) paquets ajoutés depuis l'installation
NEW_PACKAGES=(libspa-0.2-bluetooth)
missing=()
for pkg in "${NEW_PACKAGES[@]}"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "Installation de : ${missing[*]}…"
  apt-get update -qq
  apt-get install -y --no-install-recommends "${missing[@]}"
  need_reboot=1
fi

# 3) fichiers de Scaros
rm -rf /opt/scaros/launcher /opt/scaros/system
cp -r "$src/launcher" "$src/system" /opt/scaros/
chmod 755 /opt/scaros/system/session.sh /opt/scaros/launcher/scaros.py
install -m 755 "$src/update.sh" /usr/local/bin/scaros-update

if [ -n "${need_reboot:-}" ]; then
  echo "Scaros est à jour. Le PC redémarre pour activer les nouveautés…"
  systemctl reboot
else
  echo "Scaros est à jour. L'écran redémarre…"
  systemctl restart getty@tty1
fi
