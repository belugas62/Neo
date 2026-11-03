#!/usr/bin/env bash
# Diagnostic réseau de Scaros : trouve pourquoi un téléphone n'arrive pas à joindre la box
# (télécommande, AirPlay). Il vérifie la connexion du portable, puis écoute 30 secondes
# pendant que tu essaies sur le téléphone, pour voir si ses demandes arrivent.
#
#   depuis le menu : Réglages › Wi-Fi › Diagnostic réseau
#   à la main :      su -   puis   scaros-diag          (scaros-diag --vite : sans l'écoute)
#
# Le dernier rapport est gardé dans /var/lib/scaros/diag.txt ; depuis un PC du même réseau,
# il s'affiche à l'adresse http://<adresse de Scaros>:8421/diag (pratique pour le copier).
set -u
export LC_ALL=C  # sorties des outils en anglais : le script les lit plus facilement

PORT=8421
LISTEN=${DIAG_SECONDS:-30}
STATE_DIR=/var/lib/scaros
SERVICE=0
case "${1:-}" in
  --service) SERVICE=1 ;;
  --vite) LISTEN=0 ;;
  "") ;;
  *) echo "Usage : scaros-diag [--vite]"; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "Lance le diagnostic en root : tape « su - » puis « scaros-diag »."; exit 1; }

PROBLEMS=()
MAIN_IP=""
MAIN_BAND=""
MAIN_SSID=""
REACHED=0
title() { echo; echo "== $* =="; }
ok()    { echo "  [ok] $*"; }
bad()   { echo "  [!!] $*"; PROBLEMS+=("$*"); }
info()  { echo "       $*"; }
have()  { command -v "$1" >/dev/null 2>&1; }

# progression lue par le menu (checks, listen, done, failed)
status() {
  [ "$SERVICE" = 1 ] || return 0
  mkdir -p "$STATE_DIR"
  printf '{"step": "%s", "at": %s, "until": %s, "ip": "%s"}\n' "$1" "$(date +%s)" "${2:-0}" "$MAIN_IP" \
    > "$STATE_DIR/diag-status.json.tmp"
  chmod 644 "$STATE_DIR/diag-status.json.tmp"
  mv -f "$STATE_DIR/diag-status.json.tmp" "$STATE_DIR/diag-status.json"
}

# noms Bonjour des appareils (« iPhone-de-Oscar.local »), quand ils répondent
names_of() {
  have avahi-resolve-address || return 0
  timeout 4 stdbuf -oL avahi-resolve-address "$@" 2>/dev/null
}
name_in() { awk -v ip="$1" '$1 == ip {print $2; exit}' <<<"$2"; }

check_scaros() {
  title "Scaros"
  local ver commit where ports
  ver=$(sed -nE 's/^VERSION = "(.*)"/\1/p' /opt/scaros/launcher/scaros.py 2>/dev/null)
  commit=$(head -c 7 /opt/scaros/commit 2>/dev/null)
  info "Scaros OS ${ver:-?}${commit:+ (version $commit)}, nom sur le réseau : $(hostname).local"
  if curl -s -m 3 -o /dev/null "http://127.0.0.1:$PORT/api/ping"; then
    ok "le serveur de Scaros répond (port $PORT)"
  else
    bad "le serveur de Scaros ne répond pas (port $PORT) : redémarre le PC"
  fi
  where=$(ss -Hltn "sport = :$PORT" 2>/dev/null | awk '{print $4}' | sort -u | paste -sd' ')
  case "$where" in
    "") ;;
    *"*:"*|*"0.0.0.0:"*|*"[::]:"*) ok "il accepte les téléphones sur toutes ses adresses" ;;
    *) bad "il n'accepte les connexions que sur $where : redémarre le PC" ;;
  esac
  if pgrep -x uxplay >/dev/null; then
    ports=$(ss -Hltnp 2>/dev/null | awk '/"uxplay"/ {n = split($4, a, ":"); print a[n]}' | sort -un | paste -sd' ')
    ok "AirPlay tourne${ports:+ (ports $ports)}"
  else
    info "AirPlay ne tourne pas en ce moment"
  fi
  if systemctl is-active -q avahi-daemon 2>/dev/null; then
    ok "les annonces Bonjour (AirPlay, $(hostname).local) fonctionnent"
  else
    bad "Avahi est arrêté : l'iPhone ne voit plus Scaros (redémarre le PC)"
  fi
}

check_connection() {
  title "Connexion"
  local entry dev ip4 drv mac link ssid bssid freq sig band ps v6
  DEFDEV=${DIAG_IFACE:-$(ip -4 route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i+1); exit}}')}
  GW=$(ip -4 route show default dev "$DEFDEV" 2>/dev/null | awk '{print $3; exit}')
  mapfile -t ADDRS < <(ip -4 -o addr show scope global 2>/dev/null | awk '{split($4, a, "/"); print $2 " " a[1]}')
  MYADDRS=" $(ip -o addr show 2>/dev/null | awk '{split($4, a, "/"); print a[1]}' | paste -sd' ') "
  if [ "${#ADDRS[@]}" -eq 0 ]; then
    bad "Scaros n'a pas d'adresse sur le réseau : il n'est pas connecté"
    return
  fi
  for entry in "${ADDRS[@]}"; do
    dev=${entry%% *}; ip4=${entry#* }
    if [ "$dev" = "$DEFDEV" ] && [ -z "$MAIN_IP" ]; then MAIN_IP=$ip4; fi
  done
  [ -n "$MAIN_IP" ] || { DEFDEV=${ADDRS[0]%% *}; MAIN_IP=${ADDRS[0]#* }; }
  for entry in "${ADDRS[@]}"; do
    dev=${entry%% *}; ip4=${entry#* }
    drv=$(basename "$(readlink -f "/sys/class/net/$dev/device/driver" 2>/dev/null)" 2>/dev/null)
    mac=$(cat "/sys/class/net/$dev/address" 2>/dev/null)
    if [ -d "/sys/class/net/$dev/wireless" ] || [ -e "/sys/class/net/$dev/phy80211" ]; then
      link=$(iw dev "$dev" link 2>/dev/null)
      ssid=$(sed -nE 's/^[[:space:]]*SSID: (.*)/\1/p' <<<"$link")
      bssid=$(sed -nE 's/^Connected to ([0-9a-fA-F:]{17}).*/\1/p' <<<"$link")
      freq=$(sed -nE 's/^[[:space:]]*freq: ([0-9]+).*/\1/p' <<<"$link")
      sig=$(sed -nE 's/^[[:space:]]*signal: (-?[0-9]+).*/\1/p' <<<"$link")
      band=""
      if [ -z "$freq" ]; then :; elif [ "$freq" -ge 5900 ]; then band="6 GHz"; elif [ "$freq" -ge 4900 ]; then band="5 GHz"; else band="2,4 GHz"; fi
      info "Wi-Fi ($dev) : adresse $ip4 sur « ${ssid:-?} »${band:+, $band}${sig:+, signal $sig dBm}"
      if [ "$dev" = "$DEFDEV" ]; then MAIN_BAND=$band; MAIN_SSID=$ssid; fi
      info "borne ${bssid:-?} · carte ${drv:-?} · MAC $mac"
      [ -n "$sig" ] && [ "$sig" -lt -75 ] && bad "le signal Wi-Fi est faible ($sig dBm) : les connexions peuvent couper"
      ps=$(iw dev "$dev" get power_save 2>/dev/null | awk '{print $NF}')
      if [ "$ps" = on ]; then
        iw dev "$dev" set power_save off 2>/dev/null
        bad "l'économie d'énergie Wi-Fi était activée : le portable s'assoupit et ne répond plus au téléphone. Coupée pour cette fois ; une mise à jour de Scaros la coupe pour de bon"
      elif [ "$ps" = off ]; then
        ok "économie d'énergie Wi-Fi coupée"
      fi
    else
      info "Câble ($dev) : adresse $ip4 · carte ${drv:-?} · MAC $mac"
    fi
  done
  if [ "${#ADDRS[@]}" -gt 1 ]; then
    bad "Scaros est connecté de ${#ADDRS[@]} façons à la fois (câble et Wi-Fi ?) : les téléphones peuvent viser la mauvaise. Garde-en une seule"
  fi
  v6=$(ip -6 -o addr show dev "$DEFDEV" scope global 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}')
  if [ -n "$v6" ]; then info "IPv6 : $v6"; else info "IPv6 : pas d'adresse IPv6 publique (normal sur certaines box)"; fi
}

check_box() {
  title "Box"
  local out got dups
  if [ -z "${GW:-}" ]; then
    bad "pas de box trouvée (pas de route par défaut)"
    return
  fi
  out=$(ping -n -c 5 -i 0.3 -W 1 "$GW" 2>&1)
  got=$(sed -nE 's/.* ([0-9]+) received.*/\1/p' <<<"$out")
  dups=$(sed -nE 's/.*\+([0-9]+) duplicates.*/\1/p' <<<"$out")
  if [ "${got:-0}" -gt 0 ]; then ok "la box ($GW) répond (${got}/5)"; else bad "la box ($GW) ne répond pas"; fi
  if [ -n "$dups" ]; then
    bad "la box répond en double ($dups réponses en trop) : un CPL ou un répéteur duplique les paquets, ce qui perd les téléphones"
  fi
}

check_conflict() {
  title "Adresse en double"
  local entry dev ip4 out others
  if ! have arping; then
    info "test impossible : outil arping absent (fais une mise à jour de Scaros)"
    return
  fi
  for entry in "${ADDRS[@]}"; do
    dev=${entry%% *}; ip4=${entry#* }
    out=$(arping -D -c 3 -w 4 -I "$dev" "$ip4" 2>&1)
    others=$(grep -oiE '\[([0-9a-f]{2}:){5}[0-9a-f]{2}\]' <<<"$out" | tr -d '[]' | tr 'A-F' 'a-f' | sort -u | paste -sd' ')
    if [ -n "$others" ]; then
      bad "un AUTRE appareil (MAC $others) utilise aussi l'adresse $ip4 : les téléphones tombent sur lui au lieu de Scaros. Redémarre la box, ou réserve une adresse fixe pour Scaros dans la box"
    elif grep -q '^Sent' <<<"$out"; then
      ok "aucun autre appareil n'utilise $ip4"
    else
      info "test impossible sur $dev : $(head -n1 <<<"$out")"
    fi
  done
}

check_firewall() {
  title "Pare-feu"
  local fw="" line
  have nft && fw=$(nft list ruleset 2>/dev/null | grep -E '\b(drop|reject)\b' | head -5)
  have iptables && fw+=$(iptables -S INPUT 2>/dev/null | grep -E 'DROP|REJECT' | head -5)
  if [ -n "$fw" ]; then
    bad "un pare-feu filtre les connexions entrantes :"
    while read -r line; do info "$line"; done <<<"$fw"
  else
    ok "aucun pare-feu ne bloque les téléphones"
  fi
}

show_neighbours() {
  title "Appareils vus par Scaros"
  local neigh ips names line ip mac st name label shared
  neigh=$(ip -4 neigh show dev "$DEFDEV" 2>/dev/null | awk '$1 ~ /^[0-9.]+$/' | head -15)
  if [ -z "$neigh" ]; then info "aucun pour l'instant"; return; fi
  ips=$(awk '$2 == "lladdr" {print $1}' <<<"$neigh")
  # shellcheck disable=SC2086
  names=$(names_of $ips)
  while read -r line; do
    ip=$(awk '{print $1}' <<<"$line")
    mac=$(awk '{for (i = 1; i < NF; i++) if ($i == "lladdr") print $(i+1)}' <<<"$line")
    st=$(awk '{print $NF}' <<<"$line")
    name=$(name_in "$ip" "$names")
    label="$ip"
    [ "$ip" = "${GW:-}" ] && label="$ip (box)"
    [ -n "$name" ] && label="$ip ($name)"
    case "$st" in
      FAILED|INCOMPLETE) info "$label : injoignable depuis Scaros" ;;
      *) info "$label · ${mac:-?}" ;;
    esac
  done <<<"$neigh"
  # plusieurs adresses derrière la même MAC : un répéteur ou un CPL « traduit » les adresses
  shared=$(awk '$2 == "lladdr" {print $3}' <<<"$neigh" | sort | uniq -d | paste -sd' ')
  [ -n "$shared" ] && info "plusieurs appareils partagent la MAC $shared : ils passent par un répéteur ou un CPL qui traduit les adresses"
  return 0
}

# AirPlay : l'iPhone trouve Scaros par des annonces Bonjour (mDNS, multicast). On regarde si
# sa recherche arrive jusqu'à Scaros pendant le test, et si Scaros lui répond.
airplay_discovery() {
  local mdns="$1" summary others answers askers peer n name names who
  title "AirPlay (découverte par l'iPhone)"
  summary=$(awk -v me="$MYADDRS" '
    function host(a) { sub(/:$/, "", a); sub(/[.][0-9]+$/, "", a); return a }
    {
      s = ""
      for (i = 1; i <= NF; i++) if ($i == "IP" || $i == "IP6") { s = $(i + 1); break }
      if (s == "") next
      h = host(s)
      air = ($0 ~ /_airplay[.]_tcp|_raop[.]_tcp/)
      question = ($0 ~ /[?][[:space:]]+[^[:space:]]*_(airplay|raop)[.]_tcp/)
      if (index(me, " " h " ")) { if (air && !question) ans++; next }
      talk[h] = 1
      if (question) ask[h]++
    }
    END {
      n = 0; for (h in talk) n++
      printf "N %d\nA %d\n", n, ans
      for (h in ask) printf "Q %s %d\n", h, ask[h]
    }' <<<"$mdns")
  others=$(awk '$1 == "N" {print $2}' <<<"$summary")
  answers=$(awk '$1 == "A" {print $2}' <<<"$summary")
  askers=$(awk '$1 == "Q" {print $2, $3}' <<<"$summary")
  if [ -n "$askers" ]; then
    # shellcheck disable=SC2046
    names=$(names_of $(awk '{print $1}' <<<"$askers"))
    while read -r peer n; do
      name=$(name_in "$peer" "$names")
      who="$peer${name:+ ($name)}"
      ok "$who cherche les appareils AirPlay ($n fois) : sa recherche arrive jusqu'à Scaros"
    done <<<"$askers"
    if [ "${answers:-0}" -gt 0 ]; then
      ok "Scaros répond et s'annonce sous le nom « Scaros » ($answers réponses)"
      info "si Scaros n'apparaît quand même pas sur l'iPhone, ses réponses se perdent en route vers lui :"
      info "mets l'iPhone sur le même Wi-Fi ET la même bande (2,4 GHz) que Scaros, puis coupe et rallume son Wi-Fi"
    elif pgrep -x uxplay >/dev/null; then
      bad "l'iPhone cherche AirPlay mais Scaros ne lui répond pas : redémarre AirPlay (Réglages › AirPlay), puis le PC si ça continue"
    else
      bad "l'iPhone cherche AirPlay mais AirPlay ne tourne pas sur Scaros : regarde Réglages › AirPlay"
    fi
  elif [ "${others:-0}" -gt 0 ]; then
    info "Scaros reçoit bien les annonces Bonjour de $others appareil(s) du réseau"
    bad "aucune recherche AirPlay n'est arrivée (si tu as ouvert « Recopie de l'écran » sur l'iPhone pendant le test) : l'iPhone n'est sans doute pas sur le même Wi-Fi ou la même bande que Scaros. Mets-les tous les deux sur le Wi-Fi 2,4 GHz du même boîtier"
  else
    bad "aucune annonce Bonjour n'arrive jusqu'à Scaros : le Wi-Fi (ou le CPL) bloque le multicast, l'iPhone ne peut pas trouver Scaros pour AirPlay. Mets l'iPhone et Scaros sur le même Wi-Fi 2,4 GHz ; dans les réglages du CPL ou de la box, désactive l'isolation et le filtrage multicast (IGMP)"
  fi
  if [ "$MAIN_BAND" = "5 GHz" ]; then
    info "Scaros est en 5 GHz : AirPlay est souvent invisible entre les bandes 2,4 et 5 GHz d'un CPL ou d'un répéteur"
  fi
}

listen_test() {
  [ "$LISTEN" -gt 0 ] && [ -n "$MAIN_IP" ] || return 0
  title "Test en direct"
  if ! have tcpdump; then
    info "test impossible : outil tcpdump absent (fais une mise à jour de Scaros)"
    return
  fi
  local cap deadline tpid analysis mdns peers names peer aq syn sa inn out rst svc name who pingout
  cap=$(mktemp)
  deadline=$(( $(date +%s) + LISTEN ))
  status "listen" "$deadline"
  info "écoute de $LISTEN s pendant l'essai sur le téléphone ($MAIN_IP:$PORT/remote, puis Recopie de l'écran)"
  if [ "$SERVICE" = 0 ]; then
    printf "\n  >> MAINTENANT, sur le téléphone : ouvre Safari à l'adresse %s:%s/remote,\n     puis ouvre « Recopie de l'écran » (AirPlay). Ne touche pas au PC.\n\n" "$MAIN_IP" "$PORT" >&2
  fi
  timeout "$LISTEN" tcpdump -i any -nn -l \
    "not host 127.0.0.1 and not host ::1 and (tcp port $PORT or tcp port 7000 or tcp port 7001 or tcp port 7100 or udp port 5353 or (arp and host $MAIN_IP))" \
    >"$cap" 2>/dev/null &
  tpid=$!
  while kill -0 "$tpid" 2>/dev/null; do
    [ "$SERVICE" = 1 ] || printf '\r     encore %2d s… ' "$(( deadline - $(date +%s) ))" >&2
    sleep 1
  done
  wait "$tpid" 2>/dev/null
  [ "$SERVICE" = 1 ] || printf '\r                      \r' >&2

  # annonces Bonjour (AirPlay) à part ; le reste : connexions de la télécommande et d'AirPlay
  mdns=$(grep -E '[.]5353[: ]' "$cap")
  grep -vE '[.]5353[: ]' "$cap" > "$cap.tcp"
  mv -f "$cap.tcp" "$cap"
  # pour chaque appareil : demandes ARP (« où est Scaros ? »), connexions reçues, réponses de Scaros
  analysis=$(awk -v me="$MYADDRS" -v port="$PORT" '
    function host(a) { sub(/:$/, "", a); sub(/[.][0-9]+$/, "", a); return a }
    function prt(a)  { sub(/:$/, "", a); n = split(a, x, "."); return x[n] }
    {
      s = ""; d = ""
      for (i = 1; i <= NF; i++) if ($i == "IP" || $i == "IP6") { s = $(i + 1); d = $(i + 3); break }
      if (s != "") {
        f = ""
        if (match($0, /Flags \[[^]]*\]/)) f = substr($0, RSTART + 7, RLENGTH - 8)
        if (index(me, " " host(s) " ")) {
          p = host(d); svc = prt(s)
          if (f == "S.") sa[p]++; else if (f ~ /R/) rst[p]++; else out[p]++
        } else {
          p = host(s); svc = prt(d)
          if (f == "S") syn[p]++; else inn[p]++
        }
        seen[p] = 1
        if (svc == port) rem[p] = 1; else air[p] = 1
        next
      }
      if ($0 ~ /who-has/) {
        t = ""
        for (i = 1; i < NF; i++) if ($i == "tell") { t = $(i + 1); sub(/,$/, "", t) }
        if (t != "" && !index(me, " " t " ")) { aq[t]++; seen[t] = 1 }
      }
    }
    END {
      for (p in seen) {
        svc = (p in air) ? ((p in rem) ? "télécommande et AirPlay" : "AirPlay") : "télécommande"
        printf "%s %d %d %d %d %d %d %s\n", p, aq[p], syn[p], sa[p], inn[p], out[p], rst[p], svc
      }
    }' "$cap")
  rm -f "$cap"
  if [ -z "$analysis" ]; then
    bad "rien n'est arrivé jusqu'à Scaros pendant le test : la demande du téléphone est bloquée avant le portable (Wi-Fi du CPL ou de la box qui isole les appareils, autre appareil à la même adresse, VPN ou réseau invité sur le téléphone)"
    if [ "$MAIN_BAND" = "5 GHz" ]; then
      # vu chez nous : un CPL TP-Link isolait les appareils sur son 5 GHz, pas sur son 2,4 GHz
      bad "Scaros est sur le Wi-Fi 5 GHz « $MAIN_SSID » : sur beaucoup de CPL et de répéteurs, le 5 GHz isole les appareils. Essaie le Wi-Fi 2,4 GHz du même boîtier (souvent le même nom, sans « _5G ») dans Réglages › Wi-Fi"
    fi
    airplay_discovery "$mdns"
    return
  fi
  peers=$(awk '{print $1}' <<<"$analysis")
  # shellcheck disable=SC2086
  names=$(names_of $peers)
  while read -r peer aq syn sa inn out rst svc; do
    name=$(name_in "$peer" "$names")
    who="$peer${name:+ ($name)}"
    if [ "$inn" -gt 0 ] && [ "$out" -gt 0 ]; then
      ok "$who : connexion établie ($svc)"
      REACHED=1
    elif [ "$syn" -gt 0 ] && [ "$rst" -gt 0 ]; then
      bad "$who : ses demandes arrivent ($svc) mais Scaros les refuse : le service ne tourne pas, redémarre le PC"
    elif [ "$syn" -gt 0 ] && [ "$sa" -gt 0 ]; then
      bad "$who : ses demandes arrivent ($syn) et Scaros répond, mais ses réponses ne lui parviennent jamais : le Wi-Fi ou le CPL bloque le chemin du retour"
    elif [ "$syn" -gt 0 ]; then
      bad "$who : ses demandes arrivent ($syn) mais Scaros ne répond pas : pare-feu ou service bloqué"
    elif [ "$aq" -gt 0 ]; then
      bad "$who : cherche Scaros sur le réseau ($aq fois) et Scaros lui répond, mais sa connexion n'arrive jamais : la réponse se perd en route (Wi-Fi ou CPL)"
    else
      info "$who : échanges partiels ($svc)"
    fi
    # dans l'autre sens : Scaros joint-il ce téléphone ?
    pingout=$(ping -n -c 3 -i 0.3 -W 1 "$peer" 2>&1)
    if grep -q ' 0 received' <<<"$pingout"; then
      info "$who ne répond pas non plus quand Scaros l'appelle"
    elif grep -q 'duplicates' <<<"$pingout"; then
      bad "$who répond en double à Scaros : un CPL ou un répéteur duplique les paquets"
    else
      info "Scaros arrive à joindre $who"
    fi
  done <<<"$analysis"
  airplay_discovery "$mdns"
}

verdict() {
  title "Verdict"
  local p
  if [ "${#PROBLEMS[@]}" -eq 0 ]; then
    if [ "$REACHED" = 1 ]; then ok "tout fonctionne : le téléphone joint bien Scaros"
    elif [ "$LISTEN" -eq 0 ]; then ok "rien d'anormal côté Scaros (test en direct non fait)"
    else ok "rien d'anormal côté Scaros"; fi
  else
    for p in "${PROBLEMS[@]}"; do echo "  [!!] $p"; done
  fi
}

main() {
  echo "Diagnostic réseau de Scaros, $(date '+%d/%m/%Y à %H:%M')"
  status "checks"
  check_scaros
  check_connection
  check_box
  check_conflict
  check_firewall
  show_neighbours
  listen_test
  verdict
}

mkdir -p "$STATE_DIR"
REPORT=$(mktemp)
trap 'status "failed"; rm -f "$REPORT"' INT TERM
if [ "$SERVICE" = 1 ]; then
  main >"$REPORT" 2>/dev/null
else
  main | tee "$REPORT"
  echo
  echo "Rapport gardé : depuis un PC, ouvre http://$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i+1)}'):$PORT/diag"
fi
install -m 644 "$REPORT" "$STATE_DIR/diag.txt"
rm -f "$REPORT"
MAIN_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i+1)}')
status "done"
