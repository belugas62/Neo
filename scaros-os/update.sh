#!/usr/bin/env bash
# Met à jour le menu et le serveur de Scaros sans tout réinstaller, puis relance l'écran.
#   su -
#   scaros-update
set -euo pipefail

ARCHIVE="https://github.com/belugas62/Neo/archive/scaros-os.tar.gz"
[ "$(id -u)" -eq 0 ] || { echo "Lance la mise à jour en root : tape « su - » puis « scaros-update »."; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "Téléchargement de la dernière version…"
wget -q -O "$tmp/scaros.tar.gz" "$ARCHIVE"
tar -xzf "$tmp/scaros.tar.gz" -C "$tmp"
src="$tmp/Neo-scaros-os/scaros-os"
[ -f "$src/launcher/scaros.py" ] || { echo "Archive inattendue : rien n'a été modifié."; exit 1; }

rm -rf /opt/scaros/launcher /opt/scaros/system
cp -r "$src/launcher" "$src/system" /opt/scaros/
chmod 755 /opt/scaros/system/session.sh /opt/scaros/launcher/scaros.py
install -m 755 "$src/update.sh" /usr/local/bin/scaros-update

echo "Scaros est à jour. L'écran redémarre…"
systemctl restart getty@tty1
