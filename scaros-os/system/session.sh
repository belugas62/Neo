#!/bin/sh
# Session graphique de Scaros OS. Lancée par startx au démarrage (voir ~/.bash_profile).
SCAROS=/opt/scaros
LOG="$HOME/.local/state/scaros"
MENU_PROFILE="$HOME/.config/scaros-menu"
mkdir -p "$LOG"
for f in scaros chromium spotifyd; do
  [ -f "$LOG/$f.log" ] && mv -f "$LOG/$f.log" "$LOG/$f.old.log"
done

# l'écran ne se met jamais en veille tout seul
xset s off -dpms s noblank
xsetroot -solid "#06080c"

openbox --config-file "$SCAROS/system/openbox/rc.xml" &
unclutter --timeout 2 --jitter 4 --ignore-scrolling &

# Spotify Connect : le PC apparaît comme une enceinte « Scaros » dans l'appli Spotify
if command -v spotifyd >/dev/null 2>&1; then
  ( while true; do spotifyd --no-daemon >>"$LOG/spotifyd.log" 2>&1; sleep 5; done ) &
fi

# serveur du menu, relancé s'il s'arrête
( while true; do python3 "$SCAROS/launcher/scaros.py" >>"$LOG/scaros.log" 2>&1; sleep 2; done ) &

i=0
while [ $i -lt 50 ] && ! curl -fs -o /dev/null http://127.0.0.1:8421/api/state; do
  sleep 0.2; i=$((i + 1))
done

# le menu en plein écran, relancé s'il plante
while true; do
  # évite la bulle « restaurer les pages » après une coupure de courant
  if [ -f "$MENU_PROFILE/Default/Preferences" ]; then
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
      "$MENU_PROFILE/Default/Preferences"
  fi
  chromium --kiosk --app=http://127.0.0.1:8421/ --class=ScarosLauncher \
    --user-data-dir="$MENU_PROFILE" --no-first-run --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --disable-features=Translate,MediaRouter \
    --autoplay-policy=no-user-gesture-required --overscroll-history-navigation=0 --disable-pinch \
    --password-store=basic --check-for-update-interval=31536000 \
    >>"$LOG/chromium.log" 2>&1
  sleep 1
done
