#!/usr/bin/env python3
"""Scaros OS — le serveur local du menu TV.

Il sert le menu (ui/index.html) et la télécommande téléphone (ui/remote.html),
lance les applis, choisit l'écran (PC ou télé en HDMI), règle le son et le
Bluetooth, et lit la manette.

    python3 scaros.py              # sur le PC, dans la session graphique de Scaros
    python3 scaros.py --dry-run    # pour tester ailleurs : aucune commande système n'est lancée
    python3 scaros.py --pair       # appaire une manette Bluetooth puis quitte
"""

import argparse
import base64
import glob
import hashlib
import io
import json
import logging
import math
import os
import queue
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

try:
    import evdev
    from evdev import ecodes as E
except ImportError:  # sans python3-evdev, le menu lit la manette lui-même
    evdev = None

try:
    import qrcode
    import qrcode.image.svg
except ImportError:
    qrcode = None

VERSION = "0.9"
ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "scaros"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "scaros"
LAUNCHER_CLASS = "ScarosLauncher"
WEB_CLASS = "ScarosWeb"        # fenêtres des applis web (Twitch), à part du menu
INTERNAL = re.compile(r"^(eDP|LVDS|DSI)", re.I)
KEYS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "ok": "Return", "back": "Escape", "playpause": "space"}
ACTIONS = set(KEYS) | {"home", "menu", "volup", "voldown", "mute"} | {f"ch{n}" for n in range(1, 10)}
MOVES = {"up", "down", "left", "right"}
TV_RES = {"1080": (1920, 1080), "720": (1280, 720), "max": None}   # définition de l'image sur la télé
AIRPLAY_NAME = "Scaros"         # nom affiché dans la liste AirPlay de l'iPhone (et titre de la fenêtre vidéo)
SOURCE_CONF = Path("/etc/scaros/source.conf")            # dépôt GitHub des mises à jour (voir source.conf)
INSTALLED_COMMIT = Path("/opt/scaros/commit")              # écrit par scaros-update
UPDATE_STATUS = Path("/var/lib/scaros/update-status.json")  # progression écrite par scaros-update
DIAG_STATUS = Path("/var/lib/scaros/diag-status.json")      # progression écrite par scaros-diag
DIAG_REPORT = Path("/var/lib/scaros/diag.txt")              # dernier rapport de scaros-diag
DIAG_SECONDS = 30                                           # écoute pendant l'essai sur le téléphone
PHONE_TTL = 40                # un téléphone qui ne donne plus signe de vie depuis 40 s est parti
AUDIO_MODES = ("auto", "tv", "pc", "bluetooth")
AUDIO_NAMES = {"tv": "la télé (HDMI)", "pc": "les haut-parleurs du PC"}
MAC = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", re.I)

log = logging.getLogger("scaros")
DRY = False

from media import LEAGUES, TABS, Media, image_type  # noqa: E402  (à côté de ce fichier)
import store  # noqa: E402


def run(cmd, timeout=10, quiet=False):
    """Lance une commande système et renvoie (code, sortie). En --dry-run, ne lance rien."""
    if DRY:
        if not quiet:
            log.info("[dry-run] %s", " ".join(cmd))
        return 0, ""
    if not shutil.which(cmd[0]):
        if not quiet:
            log.warning("commande introuvable : %s", cmd[0])
        return 127, ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("trop long : %s", " ".join(cmd))
        return 124, ""
    if p.returncode and not quiet:
        log.warning("%s -> %s %s", " ".join(cmd), p.returncode, p.stderr.strip()[:300])
    return p.returncode, p.stdout


def update_source():
    """(dépôt, branche) d'où viennent les mises à jour ; branche vide = branche principale du dépôt."""
    repo, branch = "belugas62/Neo", "scaros-os"  # installations d'avant source.conf
    try:
        conf = dict(re.findall(r'^[ \t]*(REPO|BRANCH)[ \t]*=[ \t]*"?([^"#\s]*)', SOURCE_CONF.read_text(), re.M))
    except OSError:
        conf = {}
    if re.fullmatch(r"[\w.-]+/[\w.-]+", conf.get("REPO", "")):
        repo, branch = conf["REPO"], conf.get("BRANCH", "")
    return repo, branch


def source_label():
    repo, branch = update_source()
    return repo + (f" ({branch})" if branch else "")


def bt_audio_available():
    """Le module qui permet à PipeWire d'envoyer le son en Bluetooth est-il installé ?"""
    return DRY or bool(glob.glob("/usr/lib/*/spa-0.2/bluez5/libspa-bluez5.so"))


def system_info():
    """Processeur, mémoire, stockage et durée d'allumage, pour Réglages > À propos."""
    info = {"hostname": socket.gethostname()}
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["cpu"] = re.sub(r"\(R\)|\(TM\)|CPU", "", line.split(":", 1)[1]).replace("  ", " ").strip()
                break
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                info["ram"] = f"{int(line.split()[1]) / 1024 / 1024:.1f} Go".replace(".", ",")
        info["uptime"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    try:
        du = shutil.disk_usage("/")
        info["disk"] = f"{du.free / 1e9:.0f} Go sur {du.total / 1e9:.0f} Go"
    except OSError:
        pass
    return info


def app_icon(flatpak_id):
    """Icône d'une appli Flatpak installée (pour sa tuile dans la bande des applis)."""
    if not flatpak_id or not re.fullmatch(r"[A-Za-z0-9._-]{3,120}", flatpak_id):
        return None
    for root in ("/var/lib/flatpak", str(Path.home() / ".local/share/flatpak")):
        base = Path(root) / "exports/share/icons/hicolor"
        for size in ("256x256", "512x512", "128x128", "scalable"):
            for ext in ("png", "svg"):
                f = base / size / "apps" / f"{flatpak_id}.{ext}"
                if f.is_file():
                    return f
    return None


def lan_ip():
    """Adresse du PC sur le Wi-Fi (aucun paquet n'est envoyé)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


WEATHER_CODES = {
    0: ("clear", "Ciel dégagé"), 1: ("clear", "Plutôt dégagé"), 2: ("partly", "Partiellement nuageux"),
    3: ("cloudy", "Couvert"), 45: ("fog", "Brouillard"), 48: ("fog", "Brouillard givrant"),
    51: ("rain", "Bruine légère"), 53: ("rain", "Bruine"), 55: ("rain", "Bruine forte"),
    56: ("rain", "Bruine verglaçante"), 57: ("rain", "Bruine verglaçante"),
    61: ("rain", "Pluie faible"), 63: ("rain", "Pluie"), 65: ("rain", "Forte pluie"),
    66: ("rain", "Pluie verglaçante"), 67: ("rain", "Pluie verglaçante"),
    71: ("snow", "Neige faible"), 73: ("snow", "Neige"), 75: ("snow", "Forte neige"), 77: ("snow", "Grains de neige"),
    80: ("rain", "Averses"), 81: ("rain", "Averses"), 82: ("rain", "Fortes averses"),
    85: ("snow", "Averses de neige"), 86: ("snow", "Averses de neige"),
    95: ("storm", "Orage"), 96: ("storm", "Orage et grêle"), 99: ("storm", "Orage et grêle"),
}
DRY_DIAG = """Diagnostic réseau de Scaros (démo)

== Scaros ==
       Scaros OS {version}, nom sur le réseau : scaros.local
  [ok] le serveur de Scaros répond (port 8421)
  [ok] il accepte les téléphones sur toutes ses adresses
  [ok] AirPlay tourne (ports 7000 7001 7100)
  [ok] les annonces Bonjour (AirPlay, scaros.local) fonctionnent

== Connexion ==
       Wi-Fi (wlp3s0) : adresse 192.168.1.11 sur « TP-Link_2G », 2,4 GHz, signal -52 dBm
       borne 50:91:e3:12:34:56 · carte ath10k_pci · MAC 3c:91:80:aa:bb:cc
  [ok] économie d'énergie Wi-Fi coupée
       IPv6 : 2a02:8428:1234:5600::1b

== Box ==
  [ok] la box (192.168.1.1) répond (5/5)

== Adresse en double ==
  [ok] aucun autre appareil n'utilise 192.168.1.11

== Pare-feu ==
  [ok] aucun pare-feu ne bloque les téléphones

== Appareils vus par Scaros ==
       192.168.1.1 (box) · 00:1a:2b:3c:4d:5e
       192.168.1.24 (iPhone-de-Oscar.local) · 6e:21:aa:bb:cc:dd

== Test en direct ==
       écoute de 30 s pendant l'essai sur le téléphone (192.168.1.11:8421/remote, puis Recopie de l'écran)
  [ok] 192.168.1.24 (iPhone-de-Oscar.local) : connexion établie (télécommande)
       Scaros arrive à joindre 192.168.1.24 (iPhone-de-Oscar.local)

== AirPlay (découverte par l'iPhone) ==
       Scaros reçoit bien les annonces Bonjour de 4 appareil(s) du réseau
  [!!] aucune recherche AirPlay n'est arrivée (si tu as ouvert « Recopie de l'écran » sur l'iPhone pendant le test) : l'iPhone n'est sans doute pas sur le même Wi-Fi ou la même bande que Scaros. Mets-les tous les deux sur le Wi-Fi 2,4 GHz du même boîtier

== Verdict ==
  [!!] aucune recherche AirPlay n'est arrivée (si tu as ouvert « Recopie de l'écran » sur l'iPhone pendant le test) : l'iPhone n'est sans doute pas sur le même Wi-Fi ou la même bande que Scaros. Mets-les tous les deux sur le Wi-Fi 2,4 GHz du même boîtier
"""
DRY_WEATHER = [  # --dry-run : réponses d'Open-Meteo simulées
    {"current": {"temperature_2m": 14.2, "weather_code": 63, "is_day": 1},
     "daily": {"temperature_2m_max": [16.1], "temperature_2m_min": [9.4], "sunrise": ["2026-11-11T08:02"],
               "sunset": ["2026-11-11T17:09"], "uv_index_max": [2.1]}},
    {"current": {"temperature_2m": 21.0, "weather_code": 0, "is_day": 1},
     "daily": {"temperature_2m_max": [24.0], "temperature_2m_min": [12.0]}},
    {"current": {"temperature_2m": -1.5, "weather_code": 73, "is_day": 1},
     "daily": {"temperature_2m_max": [1.0], "temperature_2m_min": [-4.0]}},
    {"current": {"temperature_2m": 9.0, "weather_code": 2, "is_day": 0},
     "daily": {"temperature_2m_max": [13.0], "temperature_2m_min": [6.0]}},
    {"current": {"temperature_2m": 18.0, "weather_code": 95, "is_day": 1},
     "daily": {"temperature_2m_max": [22.0], "temperature_2m_min": [15.0]}},
    {"current": {"temperature_2m": 7.0, "weather_code": 45, "is_day": 1},
     "daily": {"temperature_2m_max": [9.0], "temperature_2m_min": [4.0]}},
    {"current": {"temperature_2m": 11.0, "weather_code": 3, "is_day": 1},
     "daily": {"temperature_2m_max": [12.0], "temperature_2m_min": [7.0]}},
]


AQI_LABELS = ((20, "Bon"), (40, "Correct"), (60, "Moyen"), (80, "Médiocre"), (100, "Mauvais"), (10**6, "Très mauvais"))


def parse_weather(data, city, air=None):
    """Réponses d'Open-Meteo (météo, et qualité de l'air si dispo) -> ce qu'affiche le menu."""
    cur = data["current"]
    code = int(cur["weather_code"])
    kind, label = WEATHER_CODES.get(code, ("cloudy", "Nuageux"))
    daily = data.get("daily") or {}
    first = lambda k: (daily.get(k) or [None])[0]
    tmax, tmin = first("temperature_2m_max"), first("temperature_2m_min")
    hm = lambda s: s[11:16] if isinstance(s, str) and len(s) >= 16 else None
    uv = None
    hourly = data.get("hourly") or {}
    times, values = hourly.get("time") or [], hourly.get("uv_index") or []
    now = (cur.get("time") or "")[:13]
    for t, v in zip(times, values):
        if t[:13] == now and v is not None:
            uv = round(float(v))
    if uv is None and first("uv_index_max") is not None:
        uv = round(float(first("uv_index_max")))
    aqi = None
    try:
        aqi = round(float(((air or {}).get("current") or {})["european_aqi"]))
    except (KeyError, TypeError, ValueError):
        pass
    return {"city": city, "temp": round(float(cur["temperature_2m"])), "code": code, "kind": kind, "label": label,
            "day": bool(cur.get("is_day", 1)), "max": None if tmax is None else round(tmax),
            "min": None if tmin is None else round(tmin), "sunrise": hm(first("sunrise")), "sunset": hm(first("sunset")),
            "uv": uv, "uvMax": None if first("uv_index_max") is None else round(float(first("uv_index_max"))),
            "aqi": aqi, "aqiLabel": next(lab for lim, lab in AQI_LABELS if aqi < lim) if aqi is not None else None,
            "at": int(time.time())}


class Bus:
    """Diffuse les événements vers les pages ouvertes (Server-Sent Events)."""

    CLOSE = object()
    PER_CLIENT = 2

    def __init__(self):
        self.clients = {}            # file -> adresse du client
        self.lock = threading.Lock()

    def subscribe(self, addr):
        """Ouvre un flux ; au-delà de PER_CLIENT flux pour une même adresse, ferme les plus anciens.

        Un téléphone qui se met en veille ou rouvre la page laisse souvent des flux fantômes ;
        le navigateur limite le nombre de connexions par site, et les boutons finiraient bloqués.
        """
        q = queue.Queue(maxsize=200)
        with self.lock:
            same = [c for c, a in self.clients.items() if a == addr]
            for old in same[:max(0, len(same) - self.PER_CLIENT + 1)]:
                del self.clients[old]
                try:
                    old.put_nowait(self.CLOSE)
                except queue.Full:
                    pass
            self.clients[q] = addr
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.pop(q, None)

    def emit(self, kind, **data):
        msg = json.dumps({"type": kind, **data}, ensure_ascii=False)
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


class Osd:
    """Volume et notifications par-dessus les applis (osd.py, une petite fenêtre toujours devant).

    Le menu est caché quand une appli est ouverte : sans ça, le volume changé à la manette
    ne s'afficherait nulle part.
    """

    def __init__(self):
        self.proc = None
        self.failed_at = 0.0
        self.lock = threading.Lock()

    def available(self):
        return not DRY and bool(os.environ.get("DISPLAY")) and (ROOT / "osd.py").is_file()

    def _spawn(self):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            err = open(STATE_DIR / "osd.log", "ab")
            self.proc = subprocess.Popen(["/usr/bin/python3", str(ROOT / "osd.py")], stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
            err.close()
        except OSError as e:
            log.warning("affichage par-dessus les applis impossible : %s", e)
            self.proc, self.failed_at = None, time.monotonic()

    def start(self):
        """Démarré à l'avance : le premier affichage est immédiat."""
        if self.available():
            with self.lock:
                self._spawn()

    def send(self, **msg):
        if DRY:
            log.info("[dry-run] par-dessus l'appli : %s", msg)
            return
        if not self.available():
            return
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                if time.monotonic() - self.failed_at < 30:
                    return  # il vient de planter (paquet manquant ?) : on ne relance pas en boucle
                self.failed_at = time.monotonic()
                self._spawn()
            if self.proc is None:
                return
            try:
                self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self.proc = None


class Scaros:
    def __init__(self, apps, port):
        self.apps = apps
        self.port = port
        self.bus = Bus()
        self.lock = threading.RLock()
        self.settings = self._load_settings()
        # applis ajoutées depuis l'App Store (Flathub ou site web), après celles de apps.json
        self.user_apps = self._load_user_apps()
        for k, v in self.user_apps.items():
            self.apps.setdefault(k, v)
        self.store_jobs = {}         # appli -> installation en cours {state, progress}
        self.store_icons = {}        # appli du catalogue -> image (icône de Flathub)
        self.store_icons_loaded = False
        self.dry_flatpaks = set()
        self.plex_job = None
        self.mode = self.settings.get("display_mode", "auto")
        self.hdmi = False
        self.output = "pc"
        self.pad = None
        self.volume = 35 if DRY else None
        self.muted = False
        self.current = None          # (id, Popen) de l'appli ouverte
        self.shown = False           # la fenêtre de l'appli ouverte est-elle à l'écran ?
        self.overlay = False         # centre de contrôle ouvert par-dessus l'appli (qui reste ouverte)
        self.installed = {}
        self.phones = {}             # ip -> dernier signe de vie
        self.streams = {}            # ip -> flux d'événements ouverts (téléphones)
        self.phones_seen = 0
        self.pairing = False
        self.windows = []            # fenêtres de l'appli ouverte
        self.launcher_ids = set()
        self.focus_checked = 0.0
        self.inputs = queue.Queue()  # touches, textes et lancements, exécutés dans l'ordre d'arrivée
        self.output_lock = threading.Lock()
        self.audio_lock = threading.Lock()
        self.tv_res = self.settings.get("tv_res", "1080")
        self.resolution = None       # définition actuelle de l'écran utilisé, ex. « 1920×1080 »
        self.cache = {}              # nom -> (moment, valeur) : listes Wi-Fi et Bluetooth
        self.retrying = None         # appli relancée après une fermeture immédiate
        self.osd = Osd()
        self.airplay = "idle"        # idle | connected | mirroring
        self.airplay_device = None
        self.airplay_proc = None
        self.airplay_win = None
        self.airplay_connected_at = 0.0
        self.airplay_running = False      # UxPlay tourne depuis quelques secondes
        self.airplay_error = None         # pourquoi AirPlay ne marche pas (affiché dans Réglages)
        self.airplay_advertised = None    # « Scaros » vu sur le réseau (avahi-browse) : True / False / inconnu
        self.airplay_checking = False
        self.airplay_restart = False
        self.mirroring = False
        self.mouse = Mouse()
        self.mouse_mode = False      # mode souris : pavé du téléphone en trackpad, manette en curseur
        self.update = {"checking": False, "running": False, "available": None, "step": None,
                       "source": source_label()}
        self.diag = {"running": False, "step": None, "until": None, "at": None if DRY else self._diag_report_time()}
        self.weather = None
        self.weather_lock = threading.Lock()
        self.dry_weather = 0
        self.bt_busy = set()
        self.wifi_busy = False
        self.audio_mode = self.settings.get("audio_mode", "auto")
        self.audio_target = None
        self.audio_device = None
        self.bt_scanning = False
        self.dry_bt = []             # appareils Bluetooth simulés en --dry-run
        self.wifi_scanning = False
        self.apps_updating = False
        self.extra = {}              # pages ouvertes en plein écran sans être des applis du menu (fiche Plex, Spotify)
        self.spotifyd_proc = None
        self.spotifyd_error = None
        self.spotifyd_started = 0.0
        self.airplay_cover = STATE_DIR / "airplay-cover.jpg"
        self.airplay_meta = STATE_DIR / "airplay-meta.txt"
        self.airplay_opts = None     # options de fichiers que l'UxPlay installé connaît (-ca, -md)
        self.media = Media(self.bus, STATE_DIR, CACHE_DIR, VERSION, port, DRY, prefs=lambda: self.settings.get("ui", {}),
                           notify=self.say, launch_url=self.launch_url)
        self.media.connect_state = self.connect_state
        self.media.airplay_np = self.airplay_np
        self.media.open_plex = self.open_plex
        self.dry_wifi = [            # réseaux simulés en --dry-run
            {"ssid": "Freebox-3A2C1F", "signal": 82, "secure": True, "known": True, "active": True},
            {"ssid": "Livebox-9F10", "signal": 61, "secure": True, "known": False, "active": False},
            {"ssid": "Bbox-55AA12", "signal": 38, "secure": True, "known": False, "active": False},
            {"ssid": "Wifi invités", "signal": 47, "secure": False, "known": False, "active": False},
        ]

    # ---------- réglages ----------
    def _load_settings(self):
        try:
            return json.loads((STATE_DIR / "settings.json").read_text())
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            (STATE_DIR / "settings.json").write_text(json.dumps(self.settings, indent=2))
        except OSError as e:
            log.warning("réglages non enregistrés : %s", e)

    # ---------- état ----------
    def channels(self):
        return list(self.apps)

    def cached(self, name, ttl, compute):
        """Garde un résultat coûteux (nmcli, bluetoothctl) quelques secondes."""
        hit = self.cache.get(name)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = compute()
        self.cache[name] = (time.monotonic(), value)
        return value

    def wifi(self, networks=None):
        """Nom du Wi-Fi utilisé (lu dans la liste déjà connue, sans relancer de recherche)."""
        nets = self.wifi_networks() if networks is None else networks
        active = next((n["ssid"] for n in nets if n["active"]), None)
        if active or DRY or shutil.which("nmcli"):
            return active
        code, out = run(["iwgetid", "-r"], timeout=3, quiet=True)
        return out.strip() or None

    # ---------- téléphones (télécommande) ----------
    def phone_count(self):
        limit = time.time() - PHONE_TTL
        with self.lock:
            ips = {ip for ip, t in self.phones.items() if t > limit} | {ip for ip, n in self.streams.items() if n > 0}
        return len(ips)

    def _phones_changed(self):
        n = self.phone_count()
        if n != self.phones_seen:
            self.phones_seen = n
            self.bus.emit("remote", phones=n)

    def stream_opened(self, ip):
        with self.lock:
            self.streams[ip] = self.streams.get(ip, 0) + 1
            self.phones[ip] = time.time()
        self._phones_changed()

    def stream_closed(self, ip):
        with self.lock:
            self.streams[ip] = max(0, self.streams.get(ip, 0) - 1)
            self.phones[ip] = time.time()

    def _watch_phones(self):
        """Fait disparaître l'icône du téléphone quand il est parti (veille, autre appli)."""
        while True:
            time.sleep(5)
            self._phones_changed()

    def local_name(self, ip):
        """Le nom que les téléphones peuvent taper (scaros.local) tel qu'Avahi le publie vraiment."""
        if DRY:
            return "scaros.local"
        if not shutil.which("avahi-resolve-address"):
            return None
        out = run(["avahi-resolve-address", ip], timeout=4, quiet=True)[1].split()
        return out[1] if len(out) > 1 and out[1].endswith(".local") else None

    def state(self, lite=False):
        ip = lan_ip()
        with self.lock:
            s = {
                "version": VERSION, "dry": DRY,
                "mode": self.mode, "hdmi": self.hdmi, "output": self.output,
                "res": self.resolution, "tvRes": self.tv_res,
                "pad": self.pad, "padSupport": evdev is not None,
                "volume": self.volume, "muted": self.muted,
                "app": self.current[0] if self.current else None, "shown": self.shown,
                "apps": self.apps_state(),
                "prefs": self.settings.get("ui", {}),
                "audio": {"mode": self.audio_mode, "target": self.audio_target, "device": self.audio_device},
                "airplay": self.airplay_state(),
                "mouse": self.mouse_mode,
                "accounts": self.media.public(),
            }
        if lite:  # la télécommande n'a besoin que de ça : réponse immédiate
            s["remoteBuild"] = remote_page()[1]
            return s
        # les appels lents (nmcli, bluetoothctl) se font hors du verrou, pour ne jamais retarder une touche
        nets = self.wifi_networks()
        s.update({
            "remote": {"url": f"http://{ip}:{self.port}/remote", "phones": self.phone_count(),
                       "qr": qrcode is not None, "name": self.cached("lname", 60, lambda: self.local_name(ip))},
            "wifi": self.wifi(nets), "wifiList": nets, "ip": ip, "info": system_info(),
            "bt": {"devices": self.bt_devices(), "scanning": self.bt_scanning, "audio": bt_audio_available()},
            "weather": self.weather, "place": self.settings.get("weather_place"),
            "update": self.update, "justUpdated": self.just_updated(), "diag": self.diag,
            "media": self.media.state(), "leagues": {k: v[0] for k, v in LEAGUES.items()},
        })
        return s

    def refresh_installed(self):
        for k, v in self.apps.items():
            ok = True
            if DRY:
                ok = True
            elif v.get("flatpak"):
                ok = run(["flatpak", "info", v["flatpak"]], timeout=15, quiet=True)[0] == 0
            elif v.get("web"):
                ok = shutil.which("chromium") is not None
            elif v.get("cmd"):
                ok = shutil.which(v["cmd"][0]) is not None
            self.installed[k] = ok
        log.info("applis installées : %s", self.installed)

    # ---------- applis ----------
    @staticmethod
    def _web_command(conf):
        """Appli web (ex. Twitch) : un Chromium plein écran, avec son propre profil
        (la connexion au compte est gardée), séparé de celui du menu."""
        profile = Path.home() / ".config" / conf.get("profile", "scaros-web")
        prefs = profile / "Default" / "Preferences"
        try:  # après une coupure de courant : pas de bulle « restaurer les pages »
            text = prefs.read_text()
            text = text.replace('"exited_cleanly":false', '"exited_cleanly":true')
            prefs.write_text(re.sub(r'"exit_type":"[^"]*"', '"exit_type":"Normal"', text))
        except OSError:
            pass
        return ["chromium", "--kiosk", f"--app={conf['web']}", f"--class={WEB_CLASS}",
                f"--user-data-dir={profile}", "--no-first-run", "--noerrdialogs",
                "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
                "--password-store=basic", "--autoplay-policy=no-user-gesture-required",
                "--check-for-update-interval=31536000", "--disable-features=Translate,MediaRouter",
                # décodage vidéo par la puce Intel (le nom du réglage a changé selon les versions)
                "--enable-features=VaapiVideoDecoder,VaapiVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxGL"]

    @staticmethod
    def web_reachable(url, timeout=4):
        """L'adresse d'une appli web répond-elle ? Sinon Chromium n'afficherait que sa page d'erreur :
        mieux vaut un message clair dans le menu (serveur éteint, pas d'internet)."""
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": f"Scaros/{VERSION}"})
        try:
            urllib.request.urlopen(req, timeout=timeout).close()
            return True
        except urllib.error.HTTPError:
            return True  # le serveur répond, même avec une erreur : la page saura l'afficher
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def conf(self, app_id):
        return self.apps.get(app_id) or self.extra.get(app_id) or {}

    def launch_url(self, app_id, name, url, profile):
        """Ouvre une adresse en plein écran comme une appli (fiche Plex Web, connexion à Spotify)."""
        self.extra[app_id] = {"name": name, "web": url, "profile": profile}
        self.installed[app_id] = True
        self.submit("launch", app_id)

    def launch(self, app_id, retry=True):
        conf = self.conf(app_id)
        if not conf:
            return False
        if not self.installed.get(app_id, True):
            self.refresh_installed()
        if not self.installed.get(app_id, True):
            self.bus.emit("app", id=app_id, state="missing")
            return False
        if conf.get("web") and not self.web_reachable(conf["web"]):
            log.warning("%s : %s ne répond pas", app_id, conf["web"])
            self.bus.emit("app", id=app_id, state="offline")
            self.notify("alert", f"{conf['name']} ne répond pas. Vérifie la connexion internet.", 5000)
            return False
        if self.mirroring:
            self.stop_airplay()
        self.close_app(focus=False)
        if DRY:
            cmd = ["sleep", "8"]
            if conf.get("web"):
                log.info("[dry-run] %s", " ".join(self._web_command(conf)))
        else:
            cmd = self._web_command(conf) if conf.get("web") else conf["cmd"]
        before = set(self._visible_windows())
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            out = open(STATE_DIR / f"{app_id}.log", "ab")
            p = subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT)
            out.close()
        except OSError as e:
            log.error("impossible de lancer %s : %s", app_id, e)
            self.bus.emit("app", id=app_id, state="failed")
            return False
        with self.lock:
            self.current = (app_id, p)
            self.shown = False
            self.overlay = False
            self.windows = []
        log.info("lancé : %s (pid %s)", app_id, p.pid)
        self.bus.emit("app", id=app_id, state="running")
        threading.Thread(target=self._watch, args=(app_id, p, time.time(), retry), daemon=True).start()
        threading.Thread(target=self._track_windows, args=(app_id, p, before), daemon=True).start()
        return True

    def _watch(self, app_id, p, started, retry):
        code = p.wait()
        with self.lock:
            mine = self.current is not None and self.current[1] is p
            if mine:
                self.current = None
                self.shown = False
                self.overlay = False
        if not mine:
            return
        quick = time.time() - started < 4
        log.info("%s s'est fermé (code %s)%s", app_id, code, " aussitôt" if quick else "")
        if quick and retry:
            # souvent l'ancienne fenêtre de l'appli finissait de se fermer : on réessaie une fois
            self.retrying = app_id
            time.sleep(1.5)
            if self.retrying == app_id and self.current is None:
                self.submit("launch", app_id, False)
            return
        self.bus.emit("app", id=app_id, state="failed" if quick else "closed")
        self.focus_launcher()

    def close_app(self, focus=True):
        with self.lock:
            cur, self.current = self.current, None
            self.shown = False
            self.overlay = False
        if not cur:
            return
        self.osd.send(t="hide")
        app_id, p = cur
        self.windows = []
        flatpak_id = self.conf(app_id).get("flatpak")
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        if flatpak_id:
            run(["flatpak", "kill", flatpak_id], timeout=5, quiet=True)
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        log.info("fermé : %s", app_id)
        self.bus.emit("app", id=app_id, state="closed")
        if focus:
            self.focus_launcher()

    def _visible_windows(self):
        return run(["xdotool", "search", "--onlyvisible", "--name", "."], timeout=3, quiet=True)[1].split()

    def _is_current(self, p):
        with self.lock:
            return self.current is not None and self.current[1] is p

    def _track_windows(self, app_id, p, before):
        """Repère les fenêtres de l'appli et lui donne le focus dès qu'elles s'affichent.

        Sans ça, Openbox peut laisser le focus au menu : les touches de la manette
        partiraient alors vers le menu caché derrière l'appli.
        """
        if DRY:  # pas de vraie fenêtre : on fait comme si elle s'affichait vite
            time.sleep(1.5)
            if self._is_current(p):
                self.shown = True
                self.bus.emit("app", id=app_id, state="shown")
            return
        deadline = time.time() + 90
        while time.time() < deadline and p.poll() is None and self._is_current(p):
            new = [w for w in self._visible_windows() if w not in before]
            if new and new != self.windows:
                # le menu peut réapparaître pendant ce temps (Chromium relancé) : ce n'est pas l'appli
                self.launcher_window()
                new = [w for w in new if w not in self.launcher_ids]
            if new and new != self.windows:
                self.windows = new
                if not self.overlay:
                    run(["xdotool", "windowactivate", new[-1]], timeout=3, quiet=True)
                log.info("%s : fenêtre %s activée", app_id, new[-1])
                if not self.shown:
                    self.shown = True
                    self.bus.emit("app", id=app_id, state="shown")
                    if self.conf(app_id).get("controller") == "native":
                        self.notify("gamepad", "La manette sert au jeu. Maintiens le bouton Xbox pour revenir au menu.", 6000)
            time.sleep(0.7)

    def _app_window(self):
        """La fenêtre de l'appli ouverte (la dernière repérée, sinon la plus récente qui n'est pas le menu)."""
        if self.windows:
            return self.windows[-1]
        self.launcher_window()
        launcher = self.launcher_ids
        others = [w for w in self._visible_windows() if w not in launcher]
        return others[-1] if others else None

    def ensure_app_focus(self):
        """Avant d'envoyer une touche : si le menu a repris le focus, le rend à l'appli."""
        if DRY or self.current is None or self.overlay or self.mirroring or not self.windows:
            return
        now = time.monotonic()
        if now - self.focus_checked < 1.5:
            return
        self.focus_checked = now
        if not self.launcher_ids:
            self.launcher_window()
        active = self._active_window()
        if not active or active in self.launcher_ids:
            log.info("focus rendu à l'appli (était sur %s)", active or "aucune fenêtre")
            run(["xdotool", "windowactivate", self.windows[-1]], timeout=3, quiet=True)

    def launcher_window(self):
        """La fenêtre visible du menu. Chromium crée aussi une fenêtre cachée de la même classe,
        souvent listée en premier : l'activer ne faisait rien (le centre de contrôle restait
        caché derrière l'appli). On ne garde que les fenêtres affichées, avec le titre en secours."""
        for args in (["--onlyvisible", "--class", LAUNCHER_CLASS], ["--onlyvisible", "--name", "^Scaros OS$"]):
            ids = run(["xdotool", "search", *args], timeout=3, quiet=True)[1].split()
            if ids:
                self.launcher_ids = set(ids)
                return ids[-1]
        return None

    @staticmethod
    def _active_window():
        return run(["xdotool", "getactivewindow"], timeout=3, quiet=True)[1].strip()

    def _activate(self, win):
        """Met une fenêtre devant et lui donne le clavier, puis vérifie que c'est bien le cas."""
        run(["xdotool", "windowactivate", "--sync", win], timeout=2, quiet=True)
        if self._active_window() == win:
            return True
        run(["xdotool", "windowraise", win], timeout=2, quiet=True)
        run(["xdotool", "windowfocus", "--sync", win], timeout=2, quiet=True)
        return self._active_window() == win

    def focus_launcher(self):
        if DRY:
            return True
        win = self.launcher_window()
        if not win:
            log.warning("fenêtre du menu introuvable")
            return False
        if self._activate(win):
            return True
        # dernier recours : on réduit les fenêtres de l'appli, le menu se retrouve devant
        for w in self.windows:
            run(["xdotool", "windowminimize", w], timeout=2, quiet=True)
        ok = self._activate(win)
        log.info("menu ramené devant en réduisant l'appli : %s", "réussi" if ok else "échec")
        return ok

    def show_overlay(self):
        """Centre de contrôle pendant une appli : le menu passe devant, l'appli reste ouverte derrière."""
        with self.lock:
            if (self.current is None and not self.mirroring) or self.overlay:
                return
            self.overlay = True
        self.osd.send(t="hide")
        ok = self.focus_launcher()
        log.info("centre de contrôle par-dessus %s%s", "la recopie AirPlay" if self.mirroring else self.current[0],
                 "" if ok else " : le menu n'a pas pu passer devant")

    def resume_app(self):
        """Ferme le centre de contrôle et rend l'écran à l'appli restée ouverte."""
        with self.lock:
            if not self.overlay:
                return
            self.overlay = False
            cur = self.current
        if self.mirroring and self.airplay_win:
            self._activate(self.airplay_win)
            log.info("retour dans la recopie AirPlay")
            return
        if cur is None:
            return
        win = self._app_window()
        if win:
            self._activate(win)
        log.info("retour dans %s", cur[0])

    # ---------- entrées (manette, téléphone) : une seule file, dans l'ordre ----------
    def submit(self, kind, *args):
        """Met une action dans la file. Les appuis restent dans l'ordre, même s'ils arrivent très vite."""
        self.inputs.put((time.monotonic(), kind, args))

    def _input_worker(self):
        while True:
            t, kind, args = self.inputs.get()
            try:
                if kind == "input":
                    # une flèche restée en attente plus d'une seconde (appli qui se ferme…) est oubliée
                    if args[0] in MOVES and time.monotonic() - t > 1.0:
                        continue
                    self.handle(args[0])
                elif kind == "text":
                    self.type_text(args[0])
                elif kind == "launch":
                    self.launch(*args)
                elif kind == "resume":
                    self.resume_app()
            except Exception:  # une erreur ne doit jamais bloquer la manette
                log.exception("action %s %s", kind, args)

    def handle(self, action):
        if self.mirroring and not self.overlay and action in ("back", "home"):
            self.stop_airplay()
            if action == "home":
                self.close_app()
                self.bus.emit("home")
            return
        if action == "menu":
            if self.current or self.mirroring:
                self.show_overlay()
            self.bus.emit("menu")
        elif action == "home":
            self.retrying = None
            self.close_app()
            self.bus.emit("home")
        elif action in ("volup", "voldown"):
            self.change_volume(5 if action == "volup" else -5)
        elif action == "mute":
            self.toggle_mute()
        elif action.startswith("ch"):
            ids = self.channels()
            n = int(action[2:]) - 1
            if 0 <= n < len(ids):
                self.launch(ids[n])
        elif action in KEYS:
            key = KEYS[action]
            cur = self.current
            if cur and not self.overlay:
                # une appli peut demander une autre touche, ex. "keys": {"back": "BackSpace"} dans apps.json
                key = self.conf(cur[0]).get("keys", {}).get(action, key)
                self.ensure_app_focus()
            run(["xdotool", "key", "--clearmodifiers", key], timeout=3)

    def type_text(self, text):
        text = text.replace("\n", " ").strip()[:200]
        if text:
            run(["xdotool", "type", "--clearmodifiers", "--delay", "40", "--", text], timeout=20)

    def native_controller(self):
        """L'appli affichée lit-elle la manette elle-même (jeux) au lieu de recevoir des touches ?

        Pas pendant que le centre de contrôle est ouvert par-dessus : la manette sert alors au menu.
        """
        cur = self.current
        return bool(cur and not self.overlay and not self.mirroring and self.conf(cur[0]).get("controller") == "native")

    # ---------- mode souris ----------
    def set_mouse_mode(self, on):
        """Pavé du téléphone en trackpad et manette en curseur (dans les applis), pour celles
        qui se pilotent mal aux flèches. Activé depuis la télécommande ou le centre de contrôle."""
        on = bool(on)
        with self.lock:
            if on == self.mouse_mode:
                return
            self.mouse_mode = on
        log.info("mode souris %s", "activé" if on else "désactivé")
        self.bus.emit("mouse", on=on)
        if on:
            threading.Thread(target=self.mouse.wake, daemon=True).start()
        self.notify("mouse", "Mode souris : stick gauche pour le curseur, A pour cliquer, X pour le clic droit."
                    if on else "Mode souris désactivé : retour aux flèches.", 4500 if on else 2500)

    def pad_mouse(self):
        """La manette pilote-t-elle le curseur ? Seulement par-dessus une appli : dans le menu et le
        centre de contrôle, elle garde les flèches (pour pouvoir désactiver le mode souris)."""
        return self.mouse_mode and self.current is not None and not self.overlay and not self.mirroring

    def mouse_event(self, msg):
        """Un geste du pavé du téléphone : « m dx dy » (déplacer), « s dx dy » (défiler, en 1/120
        de cran), « c left|right » (clic), « d left » / « u left » (appuyer / relâcher)."""
        parts = str(msg).split()
        if not parts:
            return
        kind = parts[0]
        try:
            if kind in ("m", "s") and len(parts) == 3:
                dx, dy = (max(-4000, min(4000, int(float(v)))) for v in parts[1:])
                (self.mouse.move if kind == "m" else self.mouse.scroll)(dx, dy)
            elif kind in ("c", "d", "u") and len(parts) == 2 and parts[1] in Mouse.BUTTONS:
                self.mouse.button(parts[1], {"c": "click", "d": "down", "u": "up"}[kind])
        except ValueError:
            pass

    def set_pad(self, name):
        with self.lock:
            if name == self.pad:
                return
            self.pad = name
        log.info("manette : %s", name or "aucune")
        self.bus.emit("pad", name=name)
        self.notify("gamepad", "Manette connectée." if name else "Manette déconnectée.")

    def hello(self, ip):
        with self.lock:
            self.phones[ip] = time.time()
        self._phones_changed()

    # ---------- son ----------
    def read_volume(self):
        """Volume (0-100) et sourdine de la sortie utilisée."""
        if DRY:
            return self.volume, self.muted
        code, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=3, quiet=True)
        m = re.search(r"([\d.]+)", out)
        if code != 0 or not m:
            return None, False
        return round(float(m.group(1)) * 100), "MUTED" in out

    def _emit_volume(self, quiet=False):
        """quiet : simple mise à jour (sortie changée), sans afficher le volume à l'écran."""
        self.bus.emit("volume", level=self.volume, muted=self.muted, quiet=quiet)
        if not quiet and self.covered():  # une appli cache le menu : le volume s'affiche par-dessus
            self.osd.send(t="volume", level=self.volume or 0, muted=self.muted)

    def change_volume(self, delta):
        if DRY:
            self.volume = max(0, min(100, (self.volume or 0) + delta))
            self.muted = False
        elif shutil.which("wpctl"):
            # monter ou baisser le son coupe la sourdine, comme sur une télé
            run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], timeout=3, quiet=True)
            run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@",
                 f"{abs(delta)}%{'+' if delta > 0 else '-'}"], timeout=3)
            self.volume, self.muted = self.read_volume()
        else:
            run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], timeout=3, quiet=True)
            run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{delta:+d}%"], timeout=3)
        self._emit_volume()

    def toggle_mute(self):
        if DRY:
            self.muted = not self.muted
        elif shutil.which("wpctl"):
            run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"], timeout=3)
            self.volume, self.muted = self.read_volume()
        else:
            run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"], timeout=3)
            self.muted = not self.muted
        log.info("son %s", "coupé" if self.muted else "rétabli")
        self._emit_volume()

    def _sinks(self):
        if DRY:
            sinks = [{"name": "alsa_output.pc", "desc": "Haut-parleurs", "hdmi": False, "bt": False, "mac": None, "plugged": True}]
            if self.hdmi:
                sinks.append({"name": "alsa_output.hdmi", "desc": "HDMI", "hdmi": True, "bt": False, "mac": None, "plugged": True})
            for d in self.dry_bt:
                if d["connected"] and d["kind"] == "audio":
                    sinks.append({"name": "bluez_output." + d["mac"].replace(":", "_") + ".1", "desc": d["name"],
                                  "hdmi": False, "bt": True, "mac": d["mac"], "plugged": True})
            return sinks
        code, out = run(["pactl", "-f", "json", "list", "sinks"], timeout=5, quiet=True)
        try:
            data = json.loads(out or "[]")
        except ValueError:
            return []
        sinks = []
        for s in data:
            ports = s.get("ports") or []
            text = " ".join([s.get("name", ""), s.get("description", ""), s.get("active_port") or ""]).lower()
            name = s.get("name") or ""
            mac = re.search(r"(?:[0-9A-F]{2}_){5}[0-9A-F]{2}", name, re.I)
            sinks.append({
                "name": name,
                "desc": s.get("description") or name,
                "hdmi": "hdmi" in text or "displayport" in text,
                "bt": name.startswith("bluez_"),
                "mac": mac.group(0).replace("_", ":").upper() if mac else None,
                "plugged": any(p.get("availability") == "available" for p in ports),
            })
        return sinks

    def _switch_profile(self, hdmi):
        code, out = run(["pactl", "-f", "json", "list", "cards"], timeout=5, quiet=True)
        try:
            cards = json.loads(out or "[]")
        except ValueError:
            return
        for card in cards:
            for name, info in (card.get("profiles") or {}).items():
                if name == "off" or info.get("available") is False or not info.get("sinks"):
                    continue
                if ("hdmi" in name.lower()) == hdmi:
                    run(["pactl", "set-card-profile", card["name"], name], timeout=5)
                    return

    def apply_audio(self):
        with self.audio_lock:  # écran, Bluetooth et réglages peuvent la demander en même temps
            self._apply_audio()

    def _apply_audio(self):
        """Choisit la sortie du son selon le réglage : auto, télé, PC ou appareil Bluetooth.

        En auto, le son va sur le casque ou l'enceinte Bluetooth connecté, sinon sur l'écran utilisé.
        """
        bt_sinks = [s for s in self._sinks() if s["bt"]]
        preferred = self.settings.get("audio_bt")
        bt = next((s for s in bt_sinks if s["mac"] == preferred), bt_sinks[0] if bt_sinks else None)
        screen = "tv" if self.output == "tv" else "pc"
        mode = self.audio_mode
        if mode == "tv":
            target = "tv" if self.hdmi else "pc"
        elif mode == "pc":
            target = "pc"
        else:
            target = "bt" if bt else screen
        device = bt["desc"] if target == "bt" else AUDIO_NAMES[target]
        if not DRY and shutil.which("pactl"):
            self._set_sink(target, bt)
        changed = (target, device) != (self.audio_target, self.audio_device)
        first = self.audio_target is None
        self.audio_target, self.audio_device = target, device
        if changed:
            log.info("son -> %s (réglage %s)", device, mode)
            if not DRY:  # chaque sortie a son propre volume : on affiche le bon
                self.volume, self.muted = self.read_volume()
            self._emit_volume(quiet=True)
            if not first:
                self.notify("headphones" if target == "bt" else "speaker", f"Le son passe sur {device}.")
        self.bus.emit("audio", mode=mode, target=target, device=device, changed=changed)

    def _set_sink(self, target, bt):
        if target == "bt":
            pick = [bt]
        else:
            want = target == "tv"
            pick = [s for s in self._sinks() if not s["bt"] and s["hdmi"] == want]
            if not pick:
                self._switch_profile(want)
                time.sleep(0.5)
                pick = [s for s in self._sinks() if not s["bt"] and s["hdmi"] == want]
        if not pick:
            log.warning("aucune sortie son %s trouvée", {"tv": "HDMI", "pc": "PC"}.get(target, "Bluetooth"))
            return
        pick.sort(key=lambda s: not s["plugged"])
        name = pick[0]["name"]
        run(["pactl", "set-default-sink", name], timeout=5)
        code, out = run(["pactl", "list", "short", "sink-inputs"], timeout=5, quiet=True)
        for line in out.splitlines():
            run(["pactl", "move-sink-input", line.split()[0], name], timeout=5, quiet=True)

    def set_audio_mode(self, mode, mac=None):
        if mode not in AUDIO_MODES or (mac and not MAC.fullmatch(mac)):
            return False
        self.audio_mode = mode
        self.settings["audio_mode"] = mode
        if mac:
            self.settings["audio_bt"] = mac.upper()
        self._save_settings()
        self.apply_audio()
        return True

    def _watch_audio(self):
        """Suit les casques et enceintes Bluetooth qui se connectent ou se déconnectent."""
        last = None
        while True:
            current = sorted(s["name"] for s in self._sinks() if s["bt"])
            if last is not None and current != last:
                self.apply_audio()
                self._bt_changed()
            last = current
            time.sleep(2)

    # ---------- Bluetooth : casques, enceintes, manettes ----------
    def _bt_info(self, mac):
        if DRY:
            return next((dict(d) for d in self.dry_bt if d["mac"] == mac), None)
        code, out = run(["bluetoothctl", "info", mac], timeout=5, quiet=True)
        if code != 0 or "Device" not in out:
            return None
        info = {"mac": mac.upper(), "name": mac, "paired": False, "connected": False, "kind": "other",
                "trusted": False, "bonded": None, "public": "(public)" in out.splitlines()[0]}
        for line in out.splitlines():
            key, _, value = line.strip().partition(": ")
            if key in ("Name", "Alias") and value:
                info["name"] = value
            elif key == "Paired":
                info["paired"] = value == "yes"
            elif key == "Connected":
                info["connected"] = value == "yes"
            elif key == "Trusted":
                info["trusted"] = value == "yes"
            elif key == "RSSI":  # présent seulement si l'appareil est capté pendant la recherche en cours
                info["seen"] = True
            elif key == "Bonded":  # « mémorisé » : la clé est gardée après un redémarrage
                info["bonded"] = value == "yes"
            elif key == "Icon":
                info["kind"] = "audio" if value.startswith("audio") else "input" if value.startswith("input") else "other"
        return info

    def _bt_power(self):
        """Bluetooth allumé et appairable. Éteint (au démarrage, ou coupé par la touche « mode avion »),
        les appareils déjà connus ne peuvent pas se reconnecter : on croit alors qu'il faut les réappairer."""
        if DRY or not shutil.which("bluetoothctl"):
            return True
        out = run(["bluetoothctl", "show"], timeout=5, quiet=True)[1]
        if "Controller" not in out:
            return False  # pas d'adaptateur (ou bluetoothd arrêté)
        if "Powered: yes" not in out:
            if "blocked" in out and shutil.which("rfkill"):
                run(["rfkill", "unblock", "bluetooth"], timeout=5, quiet=True)
            log.info("Bluetooth éteint : rallumage (%s)", self._bt_cmd("power", "on", timeout=10))
        if "Pairable: yes" not in out:
            # sans « appairable », un appairage n'est pas mémorisé : l'appareil est oublié au redémarrage
            self._bt_cmd("pairable", "on", timeout=10)
        return True

    def _bt_trust_all(self):
        """Les appareils appairés passent « de confiance » : ils se reconnectent sans rien demander."""
        if DRY or not shutil.which("bluetoothctl"):
            return
        out = run(["bluetoothctl", "devices", "Paired"], timeout=5, quiet=True)[1]
        if "Device" not in out:  # anciennes versions : pas de filtre
            out = run(["bluetoothctl", "devices"], timeout=5, quiet=True)[1]
        for mac in re.findall(r"^Device (\S+)", out, re.M):
            info = self._bt_info(mac)
            if not info or not info["paired"]:
                continue
            if not info["trusted"]:
                log.info("%s (%s) devient de confiance : %s", info["name"], mac, self._bt_cmd("trust", mac, timeout=10))
            if info["bonded"] is False:
                log.warning("%s (%s) est appairé sans être mémorisé : il faudra le réappairer une fois", info["name"], mac)

    def _watch_bt(self):
        time.sleep(4)
        self._bt_power()
        self._bt_trust_all()
        self._bt_changed()
        while True:
            time.sleep(60)
            if not self.bt_scanning and not self.pairing:
                self._bt_power()

    def bt_devices(self, fresh=False):
        """Appareils appairés, plus les casques, enceintes et manettes repérés pendant une recherche."""
        if fresh:
            self.cache.pop("bt", None)
        return self.cached("bt", 3, self._bt_list)

    def _bt_list(self):
        if DRY:
            devices = [dict(d) for d in self.dry_bt]
        else:
            devices = []
            code, out = run(["bluetoothctl", "devices"], timeout=5, quiet=True)
            paired = set(re.findall(r"^Device (\S+)", run(["bluetoothctl", "devices", "Paired"], timeout=5, quiet=True)[1], re.M))
            unnamed = re.compile(r"(?:[0-9A-F]{2}[-:]){5}[0-9A-F]{2}", re.I)
            for mac, name in re.findall(r"^Device (\S+) (.+)$", out, re.M):
                # pendant une recherche, des dizaines d'appareils sans nom passent (téléphones, montres
                # des voisins) : on ne les interroge pas un par un, ce petit PC ramerait
                if mac not in paired and unnamed.fullmatch(name.strip()):
                    continue
                info = self._bt_info(mac)
                if not info:
                    continue
                if info["paired"] or (info["kind"] in ("audio", "input") and not unnamed.fullmatch(info["name"])):
                    devices.append(info)
        # Les AirPods (et d'autres) se montrent sous deux adresses : la vraie, appairée, et une
        # adresse Bluetooth LE qui change. On garde une seule ligne par nom, la meilleure.
        best = {}
        for d in devices:
            key = d["name"].lower()
            rank = (d["connected"], d["paired"], d.get("public", True), d["kind"] != "other")
            if key not in best or rank > best[key][0]:
                best[key] = (rank, d)
        devices = [d for _, d in best.values()]
        devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))
        return devices

    def _bt_changed(self):
        self.bus.emit("bt", devices=self.bt_devices(fresh=True), scanning=self.bt_scanning, audio=bt_audio_available())

    def bt_scan(self, seconds=20):
        if self.bt_scanning or self.pairing:
            return
        self.bt_scanning = True
        self._bt_changed()
        scan = None
        try:
            if DRY:
                time.sleep(2)
                for mac, name in (("AA:00:00:00:00:01", "Casque Bluetooth"), ("AA:00:00:00:00:02", "Enceinte Bluetooth")):
                    if not any(d["mac"] == mac for d in self.dry_bt):
                        self.dry_bt.append({"mac": mac, "name": name, "paired": False, "connected": False, "kind": "audio"})
                seconds = 2
            else:
                self._bt_power()
                if shutil.which("bluetoothctl"):
                    scan = subprocess.Popen(["bluetoothctl", "--timeout", str(seconds), "scan", "on"],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + seconds
            last = None
            while time.time() < deadline:
                devices = self.bt_devices(fresh=True)
                if devices != last:
                    self.bus.emit("bt", devices=devices, scanning=True)
                    last = devices
                time.sleep(2)
        finally:
            if scan:
                scan.terminate()
            self.bt_scanning = False
            self._bt_changed()

    def bt_connect(self, mac):
        mac = mac.upper()
        with self.lock:
            if mac in self.bt_busy:  # déjà en cours : un deuxième appui ne relance rien
                return
            self.bt_busy.add(mac)
        try:
            self._bt_connect(mac)
        finally:
            self.bt_busy.discard(mac)

    def _bt_connect(self, mac):
        if not DRY:
            self._bt_power()
        info = self._bt_info(mac) or {"name": mac, "paired": False, "kind": "other"}
        self.bus.emit("btmsg", state="connecting", name=info["name"], mac=mac, paired=info["paired"])
        out = ""
        if DRY:
            time.sleep(1.2)
            for d in self.dry_bt:
                if d["mac"] == mac:
                    d.update(paired=True, connected=True)
            ok = True
        else:
            if not info["paired"]:
                log.info("appairage %s : %s", mac, self._bt_cmd("--agent", "NoInputNoOutput", "pair", mac))
                after = self._bt_info(mac) or {}
                if after.get("paired") and after.get("bonded") is False:
                    log.warning("%s appairé mais pas mémorisé (Bonded: no)", mac)
            # « de confiance » : l'appareil pourra se reconnecter tout seul la prochaine fois
            self._bt_cmd("trust", mac, timeout=10)
            ok = False
            for attempt in range(3):
                out = self._bt_cmd("connect", mac)
                log.info("connexion %s (essai %s) : %s", mac, attempt + 1, out)
                # la commande peut échouer alors que l'appareil finit de se connecter juste après
                for _ in range(5):
                    now = self._bt_info(mac)
                    if now and now["connected"]:
                        ok = True
                        break
                    time.sleep(1)
                if ok or re.search(r"AuthenticationFailed|Authentication Rejected|Key Missing|profile-unavailable", out):
                    break
        if ok and info["kind"] == "audio":
            self.settings["audio_bt"] = mac.upper()
            self._save_settings()
        if ok:
            state = "connected"
        elif info["kind"] == "audio" and (not bt_audio_available() or "profile-unavailable" in out):
            state = "noaudio"
        elif info["paired"] and re.search(r"AuthenticationFailed|Authentication Rejected|Key Missing", out):
            state = "repair"
        elif re.search(r"page-timeout|Host is down|not available|create-socket|busy", out, re.I):
            state = "unreachable"
        else:
            state = "failed"
        self.bus.emit("btmsg", state=state, name=info["name"], mac=mac, paired=info["paired"])
        self._bt_changed()

    def _bt_cmd(self, *args, timeout=30):
        """Lance bluetoothctl et renvoie tout ce qu'il affiche (il y écrit aussi ses erreurs)."""
        if DRY or not shutil.which("bluetoothctl"):
            return ""
        try:
            p = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "délai dépassé"
        return " ".join(((p.stdout or "") + (p.stderr or "")).split())[-300:]

    def bt_disconnect(self, mac):
        info = self._bt_info(mac) or {"name": mac}
        if DRY:
            for d in self.dry_bt:
                if d["mac"] == mac:
                    d["connected"] = False
        else:
            run(["bluetoothctl", "disconnect", mac], timeout=15)
        self.bus.emit("btmsg", state="disconnected", name=info["name"])
        self._bt_changed()

    def bt_remove(self, mac):
        info = self._bt_info(mac) or {"name": mac}
        if DRY:
            self.dry_bt = [d for d in self.dry_bt if d["mac"] != mac]
        else:
            run(["bluetoothctl", "remove", mac], timeout=15)
        if self.settings.get("audio_bt") == mac.upper():
            self.settings.pop("audio_bt")
            self._save_settings()
        self.bus.emit("btmsg", state="removed", name=info["name"])
        self._bt_changed()

    # ---------- Wi-Fi ----------
    def _nm(self, *args, timeout=20):
        """nmcli sans jamais écrire la commande dans le journal (elle peut contenir un mot de passe)."""
        if not shutil.which("nmcli"):
            return 127, "nmcli absent"
        try:
            p = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "délai dépassé"
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def _wifi_known(self):
        code, out = self._nm("-t", "-f", "NAME,TYPE", "connection", "show", timeout=10)
        names = set()
        for line in out.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if len(parts) >= 2 and parts[-1].endswith("wireless"):
                names.add(":".join(parts[:-1]).replace("\\:", ":"))
        return names

    def wifi_networks(self, fresh=False):
        """Réseaux Wi-Fi visibles, du connecté au plus faible."""
        if fresh:
            self.cache.pop("wifi", None)
        return self.cached("wifi", 5, self._wifi_list)

    def _wifi_list(self):
        if DRY:
            return sorted((dict(n) for n in self.dry_wifi), key=lambda n: (not n["active"], not n["known"], -n["signal"]))
        # --rescan no : lit la dernière recherche. Une nouvelle recherche coupe le Wi-Fi
        # quelques instants et peut faire saccader une vidéo ; elle n'a lieu que sur demande.
        code, out = self._nm("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no", timeout=15)
        if code != 0:
            return []
        known = self._wifi_known()
        nets = {}
        for line in out.splitlines():
            parts = [p.replace("\\:", ":") for p in re.split(r"(?<!\\):", line)]
            if len(parts) < 4 or not parts[1]:
                continue
            inuse, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
            net = {"ssid": ssid, "signal": int(signal) if signal.isdigit() else 0,
                   "secure": security not in ("", "--"), "active": inuse == "*", "known": ssid in known}
            old = nets.get(ssid)
            if old is None or net["active"] or (not old["active"] and net["signal"] > old["signal"]):
                nets[ssid] = net
        return sorted(nets.values(), key=lambda n: (not n["active"], not n["known"], -n["signal"]))

    def _wifi_changed(self):
        nets = self.wifi_networks(fresh=True)
        self.bus.emit("wifi", networks=nets, scanning=self.wifi_scanning, current=self.wifi(nets), ip=lan_ip())

    def wifi_scan(self):
        if self.wifi_scanning:
            return
        self.wifi_scanning = True
        self._wifi_changed()
        try:
            if DRY:
                time.sleep(1.5)
            else:
                self._nm("device", "wifi", "rescan", timeout=15)
                time.sleep(3)
        finally:
            self.wifi_scanning = False
            self._wifi_changed()

    def wifi_connect(self, ssid, password=None):
        with self.lock:
            if self.wifi_busy:
                return False
            self.wifi_busy = True
        try:
            return self._wifi_connect(ssid, password)
        finally:
            self.wifi_busy = False

    def _wifi_connect(self, ssid, password=None):
        self.bus.emit("wifimsg", state="connecting", ssid=ssid)
        if DRY:
            time.sleep(1.5)
            net = next((n for n in self.dry_wifi if n["ssid"] == ssid), None)
            ok = bool(net) and (not net["secure"] or net["known"] and not password or password == "scaros123")
            state = "connected" if ok else ("needpass" if net and net["secure"] and not password else "badpass")
            if ok:
                for n in self.dry_wifi:
                    n["active"] = n["ssid"] == ssid
                net["known"] = True
        else:
            known = ssid in self._wifi_known()
            if known and not password:
                code, out = self._nm("connection", "up", "id", ssid, timeout=60)
            else:
                if known:
                    self._nm("connection", "delete", "id", ssid)
                args = ["device", "wifi", "connect", ssid] + (["password", password] if password else [])
                code, out = self._nm(*args, timeout=60)
                if code != 0:
                    self._nm("connection", "delete", "id", ssid)  # pas de profil avec un mauvais mot de passe
            ok = code == 0
            secrets = re.search(r"secret|password|psk|802-1x|key", out, re.I)
            state = "connected" if ok else ("needpass" if known and not password else "badpass" if secrets else "failed")
            log.info("Wi-Fi %s : %s", ssid, "connecté" if ok else out.strip()[-200:])
        self.bus.emit("wifimsg", state=state, ssid=ssid)
        self._wifi_changed()
        return ok

    def wifi_forget(self, ssid):
        if DRY:
            for n in self.dry_wifi:
                if n["ssid"] == ssid:
                    n.update(known=False, active=False)
        else:
            self._nm("connection", "delete", "id", ssid)
        self.bus.emit("wifimsg", state="forgotten", ssid=ssid)
        self._wifi_changed()

    # ---------- applis : mises à jour ----------
    def apps_state(self):
        """Les applis du menu, pour le menu et la télécommande (dans l'ordre des chaînes 1, 2, 3…)."""
        return {k: {"name": v["name"], "installed": self.installed.get(k, True), "store": bool(v.get("store")),
                    "web": bool(v.get("web")), "color": v.get("color"), "icon": bool(v.get("flatpak") and app_icon(v["flatpak"]))}
                for k, v in self.apps.items()}

    # ---------- App Store : applis de Flathub et sites web ajoutés depuis le menu ou le téléphone ----------
    def _load_user_apps(self):
        try:
            data = json.loads((STATE_DIR / "userapps.json").read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict) and re.fullmatch(r"[a-z0-9-]{2,40}", k)
                and isinstance(v.get("name"), str) and (v.get("flatpak") or v.get("web"))}

    def _save_user_apps(self):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            (STATE_DIR / "userapps.json").write_text(json.dumps(self.user_apps, indent=2, ensure_ascii=False))
        except OSError as e:
            log.warning("applis de l'App Store non enregistrées : %s", e)

    def _apps_changed(self):
        self.bus.emit("apps", apps=self.apps_state())

    def _store_emit(self):
        self.bus.emit("store", jobs=dict(self.store_jobs))

    def _flatpaks(self):
        if DRY:
            return set(self.dry_flatpaks)
        return self.cached("flatpaks", 15, lambda: set(
            run(["flatpak", "list", "--app", "--columns=application"], timeout=30, quiet=True)[1].split()))

    def _load_store_icons(self):
        """Icônes du catalogue, lues une fois sur Flathub (puis gardées : elles ne changent presque jamais)."""
        f = STATE_DIR / "store-icons.json"
        try:
            urls = json.loads(f.read_text())
        except (OSError, ValueError):
            urls = {}
        if not DRY:
            for e in store.CATALOG:
                if e["id"] in urls:
                    continue
                try:
                    d = self._http_json(f"https://flathub.org/api/v2/appstream/{e['flatpak']}", timeout=8)
                    if isinstance(d, dict) and str(d.get("icon", "")).startswith("https://dl.flathub.org/"):
                        urls[e["id"]] = d["icon"]
                except (OSError, ValueError):
                    break  # pas d'internet : on réessaiera
            try:
                f.write_text(json.dumps(urls))
            except OSError:
                pass
        self.store_icons = {k: self.media.images.url("url", u) for k, u in urls.items() if k in store.BY_ID}
        self.store_icons_loaded = True
        if urls:
            self.bus.emit("store", jobs=dict(self.store_jobs), icons=self.store_icons)

    def store_state(self):
        if not self.store_icons_loaded:
            self.store_icons_loaded = True
            threading.Thread(target=self._load_store_icons, daemon=True).start()
        have = self._flatpaks()
        catalog = [{"id": e["id"], "name": e["name"], "cat": e["cat"], "desc": e["desc"], "color": e["color"],
                    "native": bool(e.get("native")), "mouse": bool(e.get("mouse")),
                    "installed": e["flatpak"] in have, "inMenu": e["id"] in self.apps,
                    "job": self.store_jobs.get(e["id"]), "icon": self.store_icons.get(e["id"])} for e in store.CATALOG]
        web = [{"id": k, "name": v["name"], "url": v["web"]} for k, v in self.user_apps.items() if v.get("web")]
        return {"categories": store.CATEGORIES, "catalog": catalog, "web": web}

    def _flatpak_install(self, app_id, flatpak_id):
        """flatpak install, avec la progression quand flatpak la donne. Repli : installation pour le compte seul."""
        if DRY:
            for pct in range(0, 101, 20):
                self.store_jobs[app_id] = {"state": "installing", "progress": pct}
                self._store_emit()
                time.sleep(0.4)
            self.dry_flatpaks.add(flatpak_id)
            return True
        attempts = [["flatpak", "install", "-y", "--noninteractive", "flathub", flatpak_id],
                    ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", flatpak_id]]
        for i, cmd in enumerate(attempts):
            if i == 1:
                run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                     "https://dl.flathub.org/repo/flathub.flatpakrepo"], timeout=60)
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            except OSError as e:
                log.warning("flatpak introuvable : %s", e)
                return False
            buf, last = b"", 0.0
            while True:
                chunk = p.stdout.read1(512) if hasattr(p.stdout, "read1") else p.stdout.read(512)
                if not chunk:
                    break
                buf = (buf + chunk)[-2000:]
                m = re.findall(rb"(\d{1,3})%", buf)
                if m and time.monotonic() - last > 1:
                    last = time.monotonic()
                    self.store_jobs[app_id] = {"state": "installing", "progress": min(100, int(m[-1]))}
                    self._store_emit()
            code = p.wait()
            log.info("%s -> %s", " ".join(cmd), code)
            if code == 0:
                return True
            log.warning("installation de %s ratée : %s", flatpak_id, buf.decode(errors="replace")[-300:])
        return False

    def store_install(self, app_id):
        e = store.BY_ID.get(app_id)
        if not e or app_id in self.store_jobs:
            return False
        self.store_jobs[app_id] = {"state": "installing", "progress": None}
        self._store_emit()
        self.say("download", f"Installation de {e['name']}… ça peut prendre quelques minutes.", 5000)
        try:
            ok = e["flatpak"] in self._flatpaks() or self._flatpak_install(app_id, e["flatpak"])
            self.cache.pop("flatpaks", None)
            if ok:
                conf = store.app_conf(e)
                self.user_apps[app_id] = conf
                self.apps[app_id] = conf
                self.installed[app_id] = True
                self._save_user_apps()
                self.say("download", f"{e['name']} est installé : il est au bout de la bande des applis.", 6000)
            else:
                self.say("alert", f"L'installation de {e['name']} a échoué. Vérifie la connexion internet, puis réessaie.", 7000)
        finally:
            self.store_jobs.pop(app_id, None)
            self._store_emit()
            self._apps_changed()
        return True

    def store_uninstall(self, app_id):
        conf = self.user_apps.get(app_id)
        if not conf:
            return False
        if self.current and self.current[0] == app_id:
            self.close_app()
        if conf.get("flatpak"):
            self.store_jobs[app_id] = {"state": "removing", "progress": None}
            self._store_emit()
            if DRY:
                time.sleep(1)
                self.dry_flatpaks.discard(conf["flatpak"])
            else:
                run(["flatpak", "uninstall", "-y", "--noninteractive", conf["flatpak"]], timeout=900)
            self.store_jobs.pop(app_id, None)
            self.cache.pop("flatpaks", None)
        self.user_apps.pop(app_id, None)
        self.apps.pop(app_id, None)
        self.installed.pop(app_id, None)
        self._save_user_apps()
        self._store_emit()
        self._apps_changed()
        self.say("close", f"{conf['name']} est retiré de Scaros.", 4000)
        return True

    def store_add_web(self, name, url):
        name, url = name.strip()[:40], url.strip()[:500]
        if not re.match(r"^https?://", url):
            url = "https://" + url
        host = urlparse(url).hostname
        if not host or " " in url:
            return None, "Adresse invalide (exemple : https://www.arte.tv)."
        if not name:
            name = re.sub(r"^www\.", "", host).split(".")[0].capitalize()
        base = "web-" + store.slug(name)
        app_id, n = base, 2
        while app_id in self.apps:
            app_id, n = f"{base}-{n}", n + 1
        hue = int(hashlib.md5(name.encode()).hexdigest()[:2], 16) * 360 // 256
        conf = {"name": name, "web": url, "profile": f"scaros-{app_id}", "store": True, "color": f"hsl({hue} 55% 42%)"}
        self.user_apps[app_id] = conf
        self.apps[app_id] = conf
        self.installed[app_id] = True
        self._save_user_apps()
        self._apps_changed()
        self._store_emit()
        self.say("plus", f"{name} est ajouté au bout de la bande des applis.", 5000)
        return app_id, None

    # ---------- Plex HTPC : lecture directe depuis une affiche de l'accueil ----------
    def open_plex(self, rating_key, offset, mtype, title):
        if not (self.current and self.current[0] == "plex"):
            self.submit("launch", "plex")
        job = self.plex_job = object()
        threading.Thread(target=self._plex_cast, args=(job, rating_key, offset, mtype, title), daemon=True).start()

    def _plex_cast(self, job, rating_key, offset, mtype, title):
        """Attend que Plex HTPC soit prêt (jusqu'à 90 s sur ce petit PC), puis lui demande de lire."""
        if DRY:
            time.sleep(2)
            log.info("[dry-run] Plex HTPC : lecture de %s (%s, à %s ms)", rating_key, mtype, offset)
            return
        deadline, tries, started = time.monotonic() + 90, 0, time.monotonic()
        while time.monotonic() < deadline and self.plex_job is job:
            time.sleep(2.5)
            if not (self.current and self.current[0] == "plex") and time.monotonic() - started > 8:
                return  # Plex a été fermé (ou ne s'est pas ouvert) entre-temps
            player = self.media.plex.find_player()
            if not player:
                continue
            if tries == 0:
                time.sleep(4)  # le lecteur répond avant que son interface soit prête
            tries += 1
            try:
                self.media.plex.play_on(player, rating_key, offset, mtype, command_id=tries)
                log.info("Plex HTPC (%s) : lecture de %s demandée", player.get("product") or player["id"], rating_key)
                return
            except (OSError, ValueError, KeyError) as e:
                log.info("Plex HTPC : ordre de lecture refusé (%s)", e)
                if tries >= 4:
                    break
        if self.plex_job is job:
            self.say("alert", f"Plex est ouvert, mais la lecture directe n'a pas marché : choisis « {title or 'le film'} » dans Plex.", 8000)

    def update_apps(self):
        if self.apps_updating:
            return
        self.apps_updating = True
        self.bus.emit("appsmsg", state="updating")
        try:
            if DRY:
                time.sleep(2)
                code = 0
            else:
                code = run(["flatpak", "update", "-y", "--noninteractive"], timeout=1800)[0]
            self.refresh_installed()
            self.bus.emit("appsmsg", state="done" if code == 0 else "failed")
        finally:
            self.apps_updating = False

    # ---------- réglages de l'interface (apparence, taille du texte, économiseur, veille auto) ----------
    PREFS = {"textScale": ("normal", "large", "xlarge"), "autoSleep": (0, 15, 30, 60), "saver": (0, 2, 5, 10, 15),
             "saverOn": ("on", "off"),
             "theme": ("dark", "light", "auto"),
             "bg": ("weather", "stars", "aurora", "rain", "snow", "none"),
             "homeBg": ("poster", "decor", "plain"), "accent": ("amber", "blue", "violet", "green", "pink", "white"),
             "clock24": ("24", "12"), "saverStyle": ("widgets", "landscape"), "spotifyConnect": ("on", "off"),
             "plexOpen": ("htpc", "web")}
    SAVER_WIDGETS = ("analog", "digital", "weather", "air", "music", "sport")

    def list_pref_ok(self, key, value):
        """Préférences en liste (ordre des onglets et des applis, widgets, sport)."""
        if not isinstance(value, list) or len(value) > 40 or not all(isinstance(v, str) for v in value):
            return False
        if len(set(value)) != len(value):
            return False
        allowed = {"tabs": set(TABS), "appOrder": set(self.apps) | {"settings", "store"}, "appHidden": set(self.apps),
                   "ssWidgets": set(self.SAVER_WIDGETS), "sportLeagues": set(LEAGUES)}.get(key)
        if allowed is not None:
            return set(value) <= allowed
        if key == "sportTeams":
            return all(re.fullmatch(r"[a-z0-9.]{2,20}:\d{1,9}", v) and v.split(":")[0] in LEAGUES for v in value)
        return False

    def set_pref(self, key, value):
        if key in self.PREFS:
            if value not in self.PREFS[key]:
                return False
        elif not self.list_pref_ok(key, value):
            return False
        self.settings.setdefault("ui", {})[key] = value
        self._save_settings()
        self.bus.emit("prefs", prefs=self.settings["ui"])
        if key in ("sportLeagues", "sportTeams"):
            self.media.sports.next_cache.clear()
            self.media.wake.set()
        return True

    # ---------- écrans ----------
    def hdmi_connected(self):
        if DRY:
            return self.hdmi
        for status in glob.glob("/sys/class/drm/card*-*/status"):
            connector = Path(status).parent.name.split("-", 1)[1]
            if INTERNAL.match(connector) or connector.startswith(("Writeback", "Virtual")):
                continue
            try:
                if Path(status).read_text().strip() == "connected":
                    return True
            except OSError:
                pass
        return False

    def _xrandr_outputs(self):
        """Sorties vidéo : nom, branchée ?, interne ?, définitions possibles [(l, h, fréquences, préférée)]."""
        if DRY:
            outs = [{"name": "eDP-1", "connected": True, "internal": True, "modes": [(1366, 768, [60.0], True)]}]
            outs.append({"name": "HDMI-1", "connected": self.hdmi, "internal": False,
                         "modes": [(3840, 2160, [30.0, 25.0], True), (1920, 1080, [60.0, 50.0, 59.94], False),
                                   (1280, 720, [60.0, 50.0], False)] if self.hdmi else []})
            return outs
        code, out = run(["xrandr", "--query"], timeout=8, quiet=True)
        outs = []
        for line in out.splitlines():
            m = re.match(r"^(\S+) (connected|disconnected)", line)
            if m:
                outs.append({"name": m.group(1), "connected": m.group(2) == "connected",
                             "internal": bool(INTERNAL.match(m.group(1))), "modes": []})
                continue
            m = re.match(r"^\s+(\d+)x(\d+)(i?)\S*\s+(.*)$", line)
            if m and outs and not m.group(3):  # on ignore les modes entrelacés (1080i)
                rates = [float(r) for r in re.findall(r"\d+\.\d+", m.group(4))]
                if rates:
                    outs[-1]["modes"].append((int(m.group(1)), int(m.group(2)), rates, "+" in m.group(4)))
        return outs

    @staticmethod
    def _pick_mode(modes, res):
        """Définition à demander à la télé : 1080p par défaut (une télé 4K ferait ramer ce petit PC).

        Renvoie (l, h, fréquence) ou None pour laisser la télé choisir (xrandr --auto).
        """
        limit = TV_RES.get(res)
        if not limit or not modes:
            return None
        tw, th = limit
        pref = next((m for m in modes if m[3]), modes[0])
        if pref[0] <= tw and pref[1] <= th:
            return None  # la définition native de la télé est déjà sous la limite
        fits = [m for m in modes if m[0] <= tw and m[1] <= th]
        if not fits:
            return None
        w, h, rates, _ = max(fits, key=lambda m: (m[0] * m[1], -abs(m[0] / m[1] - 16 / 9)))
        rate = min(rates, key=lambda r: (abs(r - 60), -r))
        return w, h, rate

    def apply_output(self):
        with self.output_lock:  # branchement HDMI et réglages peuvent arriver en même temps
            self._apply_output()
        self.apply_audio()

    def _apply_output(self):
        with self.lock:
            target = "tv" if self.mode in ("auto", "tv") and self.hdmi else "pc"
            if self.mode == "pc":
                target = "pc"
        outs = self._xrandr_outputs()
        ext = next((o for o in outs if not o["internal"] and o["connected"]), None)
        own = next((o for o in outs if o["internal"] and o["connected"]),
                   next((o for o in outs if o["internal"]), None))
        if target == "tv" and not ext:
            target = "pc"
        screen = ext if target == "tv" else own
        pick = self._pick_mode(screen["modes"], self.tv_res) if target == "tv" and screen else None
        cmd = ["xrandr"]
        if target == "tv":
            cmd += ["--output", ext["name"]]
            cmd += ["--mode", f"{pick[0]}x{pick[1]}", "--rate", f"{pick[2]:.2f}"] if pick else ["--auto"]
            cmd += ["--primary"] + (["--output", own["name"], "--off"] if own else [])
        else:
            cmd += (["--output", own["name"], "--auto", "--primary"] if own else [])
            for o in outs:
                if not o["internal"]:
                    cmd += ["--output", o["name"], "--off"]
        if len(cmd) > 1 and run(cmd, timeout=15)[0] != 0:
            if pick:  # la télé refuse cette définition : on la laisse choisir
                pick = None
                cmd = ["xrandr", "--output", ext["name"], "--auto", "--primary"]
                if run(cmd, timeout=15)[0] != 0:
                    run(["xrandr", "--auto"], timeout=15)
            else:
                run(["xrandr", "--auto"], timeout=15)
        size = None
        if pick:
            size = pick[:2]
        elif screen and screen["modes"]:
            m = next((m for m in screen["modes"] if m[3]), screen["modes"][0])
            size = m[:2]
        with self.lock:
            self.output = target
            self.resolution = f"{size[0]}×{size[1]}" if size else None
        log.info("écran : mode=%s hdmi=%s -> %s %s", self.mode, self.hdmi, target, self.resolution or "")
        self.bus.emit("display", mode=self.mode, hdmi=self.hdmi, output=self.output,
                      res=self.resolution, tvRes=self.tv_res)

    def set_mode(self, mode):
        if mode not in ("auto", "pc", "tv"):
            return False
        if mode == "tv" and not self.hdmi:
            return False
        with self.lock:
            self.mode = mode
        self.settings["display_mode"] = "auto" if mode == "tv" else mode
        self._save_settings()
        self.apply_output()
        return True

    def set_tv_res(self, res):
        if res not in TV_RES:
            return False
        self.tv_res = res
        self.settings["tv_res"] = res
        self._save_settings()
        self.apply_output()
        return True

    def set_hdmi(self, connected):
        with self.lock:
            self.hdmi = connected
            if not connected and self.mode == "tv":
                self.mode = "auto"
        log.info("télé %s", "branchée" if connected else "débranchée")
        self.apply_output()

    def _watch_hdmi(self):
        while True:
            connected = self.hdmi_connected()
            if connected != self.hdmi:
                time.sleep(0.8)  # laisse la télé s'annoncer
                self.set_hdmi(self.hdmi_connected())
            time.sleep(1.5)

    # ---------- alimentation ----------
    def power(self, action):
        self.close_app(focus=False)
        run(["systemctl", "poweroff" if action == "off" else "reboot"], timeout=20)

    # ---------- manette Bluetooth ----------
    def pair(self, seconds=30):
        if self.pairing:
            return False
        self.pairing = True
        try:
            self.bus.emit("pair", state="scanning")
            self._bt_power()
            scan = None
            if not DRY and shutil.which("bluetoothctl"):
                scan = subprocess.Popen(["bluetoothctl", "--timeout", str(seconds), "scan", "on"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            found = ("00:00:00:00:00:00", "Xbox Wireless Controller") if DRY else None
            deadline = time.time() + seconds
            while not found and time.time() < deadline:
                found = self._pad_in_pairing_mode(strict=time.time() < deadline - 8)
                if not found:
                    time.sleep(1.5)
            if not found:
                if scan:
                    scan.terminate()
                self.bus.emit("pair", state="notfound")
                return False
            mac, name = found
            log.info("appairage de %s (%s)", name, mac)
            if not DRY and (self._bt_info(mac) or {}).get("paired"):
                # la manette clignote : elle a oublié Scaros (utilisée sur une console, un autre PC…).
                # L'ancienne clé ne marche plus : on l'efface, puis on attend qu'elle réapparaisse.
                log.info("ancienne clé de %s effacée : %s", mac, self._bt_cmd("remove", mac, timeout=10))
                for _ in range(20):
                    time.sleep(0.5)
                    if (self._bt_info(mac) or {}).get("seen"):
                        break
            if scan:
                scan.terminate()
            log.info("appairage : %s", self._bt_cmd("--agent", "NoInputNoOutput", "pair", mac))
            log.info("confiance : %s", self._bt_cmd("trust", mac, timeout=10))
            after = self._bt_info(mac) or {}
            if after.get("bonded") is False:
                log.warning("%s appairée mais pas mémorisée (Bonded: no)", mac)
            # une manette appairée se connecte souvent toute seule : on regarde avant de forcer
            ok = bool(after.get("connected")) or run(["bluetoothctl", "connect", mac], timeout=30)[0] == 0
            for _ in range(5):
                if ok:
                    break
                time.sleep(1)
                ok = bool((self._bt_info(mac) or {}).get("connected"))
            self.bus.emit("pair", state="done" if ok else "failed", name=name)
            return ok
        finally:
            self.pairing = False

    def _pad_in_pairing_mode(self, strict=True):
        """Une manette captée pendant la recherche, pas déjà connectée. Une manette connue mais éteinte
        figure aussi dans la liste : on ne la prend pas (sauf en fin de recherche, si rien d'autre)."""
        out = run(["bluetoothctl", "devices"], timeout=5, quiet=True)[1]
        best = None
        for mac, name in re.findall(r"^Device (\S+) (.+)$", out, re.M):
            if not re.search(r"xbox|controller|gamepad|manette", name, re.I):
                continue
            info = self._bt_info(mac) or {}
            if info.get("connected") or (strict and not info.get("seen")):
                continue
            if best is None or not info.get("paired"):
                best = (mac, name)
        return best

    # ---------- par-dessus les applis ----------
    def covered(self):
        """Le menu est-il caché par une appli ou par une recopie AirPlay ?"""
        return (self.current is not None and not self.overlay) or self.mirroring

    def notify(self, icon, text, ms=3500):
        if self.covered():
            self.osd.send(t="note", icon=icon, text=text, ms=ms)

    def say(self, icon, text, ms=3500):
        """Message pour l'écran, où qu'on soit : par-dessus l'appli ouverte, ou dans le menu."""
        if self.covered():
            self.notify(icon, text, ms)
        else:
            self.bus.emit("toast", icon=icon, text=text, ms=ms)

    # ---------- AirPlay (UxPlay) : recopie d'écran et vidéos depuis un iPhone ----------
    def airplay_state(self):
        return {"enabled": self.settings.get("airplay", True), "available": DRY or shutil.which("uxplay") is not None,
                "state": self.airplay, "device": self.airplay_device, "name": AIRPLAY_NAME,
                "running": self.airplay_running, "error": self.airplay_error,
                "advertised": self.airplay_advertised, "checking": self.airplay_checking}

    def _airplay_changed(self):
        self.bus.emit("airplay", **self.airplay_state())

    def _airplay_loop(self):
        """Garde UxPlay prêt : Scaros apparaît alors dans la liste AirPlay de l'iPhone."""
        fails = 0
        if DRY:
            self.airplay_running, self.airplay_advertised = True, True
            return
        # un UxPlay resté d'une session précédente (écran redémarré) ferait doublon
        run(["pkill", "-x", "-u", str(os.getuid()), "uxplay"], timeout=5, quiet=True)
        while True:
            if not self.settings.get("airplay", True) or not shutil.which("uxplay") or not os.environ.get("DISPLAY"):
                if self.airplay_running:
                    self.airplay_running = False
                    self._airplay_changed()
                time.sleep(5)
                continue
            if not self._avahi_ok():
                self._airplay_fail("Le service de découverte du réseau (Avahi) est arrêté : lance scaros-update.")
                time.sleep(30)
                continue
            started = time.monotonic()
            # UxPlay écrit ses messages avec printf : sans « stdbuf -oL », ils restent en mémoire
            # au lieu d'arriver ligne par ligne (connexion d'un iPhone repérée trop tard)
            # -p : ports fixes (TCP 7000 7001 7100, UDP 6000 6001 7011) au lieu de ports au hasard,
            # que le diagnostic réseau sait surveiller et qu'un pare-feu peut laisser passer
            cmd = ["uxplay", "-n", AIRPLAY_NAME, "-nh", "-fs", "-nohold", "-reset", "15", "-p"]
            cmd += self._airplay_file_opts()
            if shutil.which("stdbuf"):
                cmd = ["stdbuf", "-oL", "-eL"] + cmd
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                logf = open(STATE_DIR / "airplay.log", "w")
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, errors="replace", start_new_session=True)
            except OSError as e:
                self._airplay_fail(f"UxPlay ne se lance pas : {e}")
                time.sleep(30)
                continue
            self.airplay_proc = p
            self.airplay_restart = False
            log.info("AirPlay : UxPlay lancé (« %s » sur l'iPhone)", AIRPLAY_NAME)
            # quelques secondes après le lancement : est-ce que Scaros est bien annoncé sur le réseau ?
            threading.Timer(6, self._airplay_ready, args=(p,)).start()
            written, last, errors = 0, "", []
            for line in p.stdout:
                if written < 2_000_000:  # journal limité : UxPlay peut être bavard
                    logf.write(line)
                    logf.flush()
                    written += len(line)
                if line.strip():
                    last = line.strip()
                if "*** ERROR" in line and len(errors) < 5:
                    errors.append(line.replace("*** ERROR:", "").strip())
                m = re.search(r"connection request from (.+?) \(([^)]*)\) with deviceID", line)
                if m:
                    self.airplay_device = m.group(1).strip()
                    self.airplay_connected_at = time.monotonic()
                    if self.airplay == "idle":
                        self.airplay = "connected"
                        self._airplay_changed()
                    log.info("AirPlay : connexion de %s", self.airplay_device)
            code = p.wait()
            logf.close()
            self.airplay_proc = None
            self._airplay_ended()
            quick = time.monotonic() - started < 10
            if self.airplay_restart:  # redémarrage demandé (Réglages, ou recopie arrêtée avec B)
                fails = 0
                self.airplay_running = False
                continue
            if quick:
                why = errors[0] if errors else last or f"code {code}"
                if "dnssd" in why.lower():
                    why = "annonce sur le réseau impossible (Avahi). Lance scaros-update, puis redémarre le PC."
                self._airplay_fail(f"UxPlay s'arrête au démarrage : {why}")
            fails = fails + 1 if quick else 0
            time.sleep(min(60, 2 * 2 ** min(fails, 5)))  # UxPlay qui plante en boucle : on espace

    def _airplay_file_opts(self):
        """-ca : pochette de la musique envoyée par l'iPhone (écran de veille) ; -md (UxPlay 1.72+) : titre, artiste."""
        if self.airplay_opts is None:
            try:
                p = subprocess.run(["uxplay", "-h"], capture_output=True, text=True, timeout=5)
                text = p.stdout + p.stderr
            except (OSError, subprocess.TimeoutExpired):
                text = ""
            self.airplay_opts = []
            if re.search(r"^\s*-ca\b", text, re.M):
                self.airplay_opts += ["-ca", str(self.airplay_cover)]
            if re.search(r"^\s*-md\b", text, re.M):
                self.airplay_opts += ["-md", str(self.airplay_meta)]
            for f in (self.airplay_cover, self.airplay_meta):
                f.unlink(missing_ok=True)
        return self.airplay_opts

    def airplay_np(self):
        """Musique envoyée en AirPlay : pochette (et titre si UxPlay le donne), pendant 8 minutes après le dernier morceau."""
        if self.mirroring or not self.airplay_proc:
            return None
        try:
            st = self.airplay_cover.stat()
        except OSError:
            st = None
        meta = {}
        try:
            if time.time() - self.airplay_meta.stat().st_mtime < 480:
                for line in self.airplay_meta.read_text(errors="replace").splitlines():
                    k, _, v = line.partition(":")
                    if v.strip():
                        meta[k.strip().lower()] = v.strip()
        except OSError:
            pass
        if not meta and (not st or time.time() - st.st_mtime > 480):
            return None
        return {"source": "airplay", "via": "airplay", "title": meta.get("title") or meta.get("titre") or "Musique en AirPlay",
                "artist": meta.get("artist") or meta.get("artiste") or (self.airplay_device or "iPhone"),
                "album": meta.get("album") or "", "cover": f"/api/np/cover?t={int(st.st_mtime)}" if st else None,
                "playing": True, "position": 0, "duration": 0, "device": self.airplay_device, "controls": False}

    def spotify_done(self, err, close=True):
        """Fin de la connexion à Spotify (page fermée sur la télé, message pour tout le monde)."""
        if close and self.current and self.current[0] == "spotifylogin":
            self.close_app()
        self.say("alert" if err else "check", err or "Spotify est connecté : la musique s'affiche sur l'écran de veille.", 6000)
        self.media.changed()

    # ---------- Spotify Connect (spotifyd) ----------
    def connect_state(self):
        """Scaros dans la liste des appareils de l'appli Spotify : on | off | missing | error."""
        if self.settings.get("ui", {}).get("spotifyConnect") == "off":
            return "off"
        if DRY:
            return "on"
        if not shutil.which("spotifyd"):
            return "missing"
        p = self.spotifyd_proc
        return "on" if p and p.poll() is None else "error" if self.spotifyd_error else "starting"

    def _spotifyd_loop(self):
        """Garde spotifyd lancé : « Scaros » apparaît dans l'appli Spotify (Premium) et le son sort ici."""
        if DRY:
            return
        run(["pkill", "-x", "-u", str(os.getuid()), "spotifyd"], timeout=5, quiet=True)
        fails = 0
        while True:
            want = self.settings.get("ui", {}).get("spotifyConnect", "on") != "off" and shutil.which("spotifyd")
            p = self.spotifyd_proc
            if not want:
                if p and p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                    self.spotifyd_proc = None
                time.sleep(3)
                continue
            if p and p.poll() is None:
                time.sleep(3)
                continue
            if p:
                quick = time.monotonic() - self.spotifyd_started < 15
                fails = fails + 1 if quick else 0
                self.spotifyd_error = f"spotifyd s'est arrêté (code {p.returncode})" if quick else None
                log.warning("spotifyd s'est arrêté (code %s)", p.returncode)
                time.sleep(min(120, 3 * 2 ** min(fails, 5)))
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                out = open(STATE_DIR / "spotifyd.log", "ab")
                self.spotifyd_proc = subprocess.Popen(["spotifyd", "--no-daemon"], stdout=out, stderr=subprocess.STDOUT,
                                                      stdin=subprocess.DEVNULL, start_new_session=True)
                out.close()
                self.spotifyd_started = time.monotonic()
                log.info("Spotify Connect : spotifyd lancé (« Scaros » dans l'appli Spotify)")
            except OSError as e:
                self.spotifyd_error = str(e)
                time.sleep(60)

    def _avahi_ok(self):
        code, out = run(["systemctl", "is-active", "avahi-daemon"], timeout=5, quiet=True)
        return code == 0 or out.strip() == "active" or not shutil.which("systemctl")

    def _airplay_fail(self, why):
        log.warning("AirPlay : %s", why)
        self.airplay_running, self.airplay_error, self.airplay_advertised = False, why, None
        self._airplay_changed()

    def _airplay_ready(self, p):
        if p.poll() is not None or self.airplay_proc is not p:
            return
        self.airplay_running, self.airplay_error = True, None
        self.check_airplay()

    def check_airplay(self):
        """Regarde, comme le ferait l'iPhone, si « Scaros » est annoncé sur le réseau (avahi-browse)."""
        if self.airplay_checking:
            return
        if DRY:
            self.airplay_advertised = True
            self._airplay_changed()
            return
        if not shutil.which("avahi-browse"):
            self.airplay_advertised = None
            self._airplay_changed()
            return
        self.airplay_checking = True
        self._airplay_changed()
        try:
            code, out = run(["avahi-browse", "-t", "-p", "-r", "_airplay._tcp"], timeout=10, quiet=True)
            names = set()
            for line in out.splitlines():
                parts = line.split(";")
                if len(parts) > 3 and parts[0] in ("+", "="):
                    names.add(re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1))), parts[3]))
            self.airplay_advertised = any(n == AIRPLAY_NAME or n.startswith(AIRPLAY_NAME + " ") for n in names)
            log.info("AirPlay annoncé sur le réseau : %s (vus : %s)", self.airplay_advertised, ", ".join(sorted(names)) or "aucun")
        finally:
            self.airplay_checking = False
            self._airplay_changed()

    def restart_airplay(self):
        """Relance UxPlay (Réglages > AirPlay > Redémarrer AirPlay)."""
        self.airplay_error, self.airplay_advertised = None, None
        p = self.airplay_proc
        if DRY:
            self._airplay_changed()
            threading.Timer(1.5, self.check_airplay).start()
            return
        if p and p.poll() is None:
            self.airplay_restart = True
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        self._airplay_changed()

    def _watch_airplay_window(self):
        """La recopie commence quand la fenêtre vidéo d'UxPlay (nommée « Scaros ») s'affiche,
        et s'arrête quand elle se ferme (l'iPhone a arrêté la recopie). On regarde les fenêtres
        directement, sans dépendre des messages d'UxPlay."""
        while True:
            time.sleep(1.5)
            if DRY or self.airplay_proc is None:
                continue
            wins = run(["xdotool", "search", "--onlyvisible", "--name", f"^{AIRPLAY_NAME}$"], timeout=3, quiet=True)[1].split()
            if wins and not self.mirroring:
                self.airplay_win = wins[-1]
                self._mirroring_started()
            elif not wins and self.mirroring:
                self._airplay_ended()
            elif not wins and self.airplay == "connected" and self.airplay_connected_at \
                    and time.monotonic() - self.airplay_connected_at > 90:
                self.airplay = "idle"  # musique seule ou connexion abandonnée : pas de fenêtre vidéo
                self._airplay_changed()

    def _mirroring_started(self):
        with self.lock:
            self.mirroring = True
            self.overlay = False
        self.airplay = "mirroring"
        log.info("AirPlay : recopie de %s", self.airplay_device or "un appareil")
        if self.airplay_win:
            run(["xdotool", "windowactivate", self.airplay_win], timeout=3, quiet=True)
        self._airplay_changed()
        self.notify("airplay", f"Recopie d'écran : {self.airplay_device or 'iPhone'}. B pour l'arrêter.", 5000)

    def _airplay_ended(self):
        was = self.mirroring
        with self.lock:
            self.mirroring = False
        self.airplay_win = None
        if self.airplay != "idle":
            self.airplay = "idle"
            self._airplay_changed()
        if was:
            log.info("AirPlay : recopie terminée")
            win = self._app_window() if self.current else None
            if win:
                run(["xdotool", "windowactivate", win], timeout=3, quiet=True)
            else:
                self.focus_launcher()

    def stop_airplay(self):
        """Coupe la recopie (UxPlay redémarre aussitôt, prêt pour la prochaine)."""
        p = self.airplay_proc
        if DRY:
            self._airplay_ended()
            return
        if p and p.poll() is None:
            self.airplay_restart = True
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        self._airplay_ended()

    def set_airplay(self, on):
        self.settings["airplay"] = bool(on)
        self._save_settings()
        if not on:
            self.stop_airplay()
        self._airplay_changed()
        return True

    def dry_airplay(self):
        """--dry-run : simule un iPhone qui commence puis arrête la recopie."""
        if self.mirroring:
            self._airplay_ended()
        else:
            self.airplay_device = "iPhone d'Oscar"
            self._mirroring_started()

    # ---------- mise à jour de Scaros depuis le menu ----------
    def installed_commit(self):
        try:
            return INSTALLED_COMMIT.read_text().strip() or None
        except OSError:
            return None

    def _update_status_file(self):
        try:
            return json.loads(UPDATE_STATUS.read_text())
        except (OSError, ValueError):
            return {}

    def check_update(self):
        """Compare la version installée à la dernière sur GitHub (sans rien installer)."""
        if self.update.get("checking"):
            return
        self.update["checking"] = True
        self.bus.emit("update", **self.update)
        try:
            if DRY:
                time.sleep(1)
                latest = {"sha": "dry0002", "message": "Nouvelle version de test", "date": "2026-11-02T12:00:00Z"}
            else:
                repo, branch = update_source()
                self.update["source"] = source_label()
                api = f"https://api.github.com/repos/{repo}/commits/{urllib.parse.quote(branch or 'HEAD', safe='')}"
                req = urllib.request.Request(api, headers={"User-Agent": "Scaros-OS",
                                                          "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.load(r)
                latest = {"sha": data["sha"], "message": data["commit"]["message"].splitlines()[0][:140],
                          "date": data["commit"]["committer"]["date"]}
            installed = "dry0001" if DRY else self.installed_commit()
            self.update.update(latest=latest["sha"], message=latest["message"], date=latest["date"],
                               available=None if not installed else installed != latest["sha"],
                               installed=installed, error=None, checked=time.time())
        except (OSError, ValueError, KeyError) as e:
            log.info("vérification de mise à jour impossible : %s", e)
            self.update.update(error="offline", checked=time.time())
        finally:
            self.update["checking"] = False
            self.bus.emit("update", **self.update)

    def _watch_updates(self):
        time.sleep(90)
        while True:
            self.check_update()
            time.sleep(12 * 3600)

    def start_update(self):
        if self.update.get("running"):
            return False
        # gardé pour l'annoncer après le redémarrage de l'écran
        self.settings["update_message"] = self.update.get("message") or ""
        self._save_settings()
        self.update.update(running=True, step="start", error=None)
        self.bus.emit("update", **self.update)
        threading.Thread(target=self._run_update, daemon=True).start()
        return True

    def _run_update(self):
        if DRY:
            for step in ("download", "install", "done"):
                time.sleep(1.2)
                self.update["step"] = step
                self.bus.emit("update", **self.update)
            self.update.update(running=False, available=False, installed=self.update.get("latest"))
            self.bus.emit("update", **self.update)
            return
        # scaros-update tourne en root, dans un service à part : il continue même quand l'écran redémarre
        code, out = run(["systemctl", "start", "--no-block", "scaros-update.service"], timeout=20)
        if code != 0:
            self.update.update(running=False, step="failed", error="service")
            self.bus.emit("update", **self.update)
            return
        started = time.time()
        seen = None
        while time.time() - started < 1800:
            time.sleep(1)
            st = self._update_status_file()
            if st.get("step") and st != seen:
                seen = st
                self.update["step"] = st["step"]
                self.bus.emit("update", **self.update)
                if st["step"] in ("done", "failed"):
                    break
            active = run(["systemctl", "is-active", "scaros-update.service"], timeout=5, quiet=True)[1].strip()
            if active in ("inactive", "failed") and time.time() - started > 15:
                if (self._update_status_file().get("step") or "") not in ("done",):
                    self.update.update(step="failed")
                break
        failed = self.update.get("step") == "failed"
        self.update.update(running=False, error="failed" if failed else None)
        self.bus.emit("update", **self.update)

    def just_updated(self):
        """Après une mise à jour, l'écran a redémarré : on le dit une fois."""
        st = self._update_status_file()
        at = st.get("at", 0)
        if st.get("step") == "done" and time.time() - at < 900 and self.settings.get("update_seen") != at:
            msg = self.settings.get("update_message")
            return f"Scaros est à jour. {msg}" if msg else "Scaros est à jour."
        return None

    def ack_update(self):
        self.settings["update_seen"] = self._update_status_file().get("at", 0)
        self._save_settings()

    # ---------- diagnostic réseau (scaros-diag) ----------
    @staticmethod
    def _diag_report_time():
        try:
            return int(DIAG_REPORT.stat().st_mtime)
        except OSError:
            return None

    def diag_report(self):
        if DRY:
            return DRY_DIAG.format(version=VERSION) if self.diag.get("at") else ""
        try:
            return DIAG_REPORT.read_text(errors="replace")[:20000]
        except OSError:
            return ""

    def start_diag(self):
        if self.diag.get("running"):
            return False
        self.diag.update(running=True, step="checks", until=None, error=None)
        self.bus.emit("diag", **self.diag)
        threading.Thread(target=self._run_diag, daemon=True).start()
        return True

    def _run_diag(self):
        if DRY:
            time.sleep(1.5)
            self.diag.update(step="listen", until=time.time() + 8)
            self.bus.emit("diag", **self.diag)
            time.sleep(8)
            self.diag.update(running=False, step="done", until=None, at=int(time.time()))
            self.bus.emit("diag", **self.diag)
            return
        # scaros-diag tourne en root (tcpdump, arping) dans un service à part
        started = time.time()
        code, _ = run(["systemctl", "start", "--no-block", "scaros-diag.service"], timeout=20)
        if code != 0:
            self.diag.update(running=False, step="failed", error="service")
            self.bus.emit("diag", **self.diag)
            return
        seen = None
        while time.time() - started < DIAG_SECONDS + 90:
            time.sleep(0.5)
            try:
                st = json.loads(DIAG_STATUS.read_text())
            except (OSError, ValueError):
                st = {}
            if st.get("at", 0) < started - 2:  # fichier laissé par un diagnostic précédent
                st = {}
            if st.get("step") and st != seen:
                seen = st
                self.diag.update(step=st["step"], until=st.get("until") or None)
                if st["step"] in ("done", "failed"):
                    break
                self.bus.emit("diag", **self.diag)
            if time.time() - started > 10:
                active = run(["systemctl", "is-active", "scaros-diag.service"], timeout=5, quiet=True)[1].strip()
                if active in ("inactive", "failed"):
                    break
        try:  # le service a pu finir entre deux lectures
            st = json.loads(DIAG_STATUS.read_text())
            if st.get("at", 0) >= started - 2 and st.get("step") == "done":
                self.diag["step"] = "done"
        except (OSError, ValueError):
            pass
        done = self.diag.get("step") == "done"
        self.diag.update(running=False, until=None, step="done" if done else "failed",
                         at=self._diag_report_time() if done else self.diag.get("at"))
        self.bus.emit("diag", **self.diag)

    # ---------- météo (Open-Meteo, sans compte) ----------
    @staticmethod
    def _http_json(url, timeout=10):
        req = urllib.request.Request(url, headers={"User-Agent": "Scaros-OS"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def _locate(self):
        """Ville devinée grâce à l'adresse internet de la box (modifiable dans les réglages)."""
        if DRY:
            return {"city": "Lille", "lat": 50.63, "lon": 3.06, "auto": True}
        for url, conv in (("https://ipapi.co/json/", lambda d: (d.get("city"), d.get("latitude"), d.get("longitude"))),
                          ("http://ip-api.com/json/?fields=status,city,lat,lon", lambda d: (d.get("city"), d.get("lat"), d.get("lon")))):
            try:
                city, lat, lon = conv(self._http_json(url, timeout=8))
                if city and lat is not None and lon is not None:
                    return {"city": city, "lat": float(lat), "lon": float(lon), "auto": True}
            except (OSError, ValueError, TypeError):
                continue
        return None

    def refresh_weather(self):
        with self.weather_lock:
            place = self.settings.get("weather_place")
            if not place:
                place = self._locate()
                if not place:
                    log.info("météo : ville introuvable (pas d'internet ?)")
                    return
                self.settings["weather_place"] = place
                self._save_settings()
            try:
                air = None
                if DRY:
                    data = DRY_WEATHER[self.dry_weather % len(DRY_WEATHER)]
                    air = {"current": {"european_aqi": 28}}
                else:
                    data = self._http_json(
                        "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
                            "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": 1,
                            "current": "temperature_2m,weather_code,is_day",
                            "hourly": "uv_index",
                            "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max"}))
                    try:  # qualité de l'air : un plus, pas indispensable
                        air = self._http_json("https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode({
                            "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "current": "european_aqi"}))
                    except (OSError, ValueError) as e:
                        log.info("qualité de l'air indisponible : %s", e)
                self.weather = parse_weather(data, place["city"], air)
            except (OSError, ValueError, KeyError, TypeError, IndexError) as e:
                log.info("météo indisponible : %s", e)
                return
        self.bus.emit("weather", weather=self.weather, place=self.settings.get("weather_place"))

    def _watch_weather(self):
        while True:
            self.refresh_weather()
            time.sleep(20 * 60)

    def search_city(self, query):
        if DRY:
            q = query.strip().title()
            return [{"name": q, "admin": "Hauts-de-France", "country": "France", "lat": 50.63, "lon": 3.06},
                    {"name": q, "admin": "Québec", "country": "Canada", "lat": 46.81, "lon": -71.21}]
        data = self._http_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
            {"name": query, "count": 6, "language": "fr", "format": "json"}))
        return [{"name": r["name"], "admin": r.get("admin1") or "", "country": r.get("country") or "",
                 "lat": r["latitude"], "lon": r["longitude"]} for r in data.get("results") or []]

    def set_city(self, place):
        if place is None:  # retour à la détection automatique
            self.settings.pop("weather_place", None)
        else:
            self.settings["weather_place"] = place
        self._save_settings()
        threading.Thread(target=self.refresh_weather, daemon=True).start()

    # ---------- démarrage ----------
    def start(self):
        self.hdmi = self.hdmi_connected()
        self.volume, self.muted = self.read_volume()
        threading.Thread(target=self._input_worker, daemon=True).start()
        threading.Thread(target=self._watch_phones, daemon=True).start()
        threading.Thread(target=self.refresh_installed, daemon=True).start()
        threading.Thread(target=self.apply_output, daemon=True).start()
        threading.Thread(target=self._watch_hdmi, daemon=True).start()
        threading.Thread(target=self._watch_audio, daemon=True).start()
        threading.Thread(target=self._watch_bt, daemon=True).start()
        threading.Thread(target=self._airplay_loop, daemon=True).start()
        threading.Thread(target=self._watch_airplay_window, daemon=True).start()
        threading.Thread(target=self._watch_weather, daemon=True).start()
        threading.Thread(target=self._watch_updates, daemon=True).start()
        threading.Thread(target=self.media.watch, daemon=True).start()
        threading.Thread(target=self._spotifyd_loop, daemon=True).start()
        threading.Thread(target=self.osd.start, daemon=True).start()
        if evdev is not None:
            threading.Thread(target=Pad(self).run, daemon=True).start()
        else:
            log.warning("python3-evdev absent : la manette sera lue par le menu uniquement")


class Mouse:
    """Souris virtuelle : le pavé du téléphone et la manette déplacent le vrai curseur.

    Elle passe par uinput : Xorg la voit comme une vraie souris, c'est fluide et ça marche dans
    toutes les applis. Sans accès à /dev/uinput (mise à jour pas encore faite), repli sur xdotool,
    moins fluide mais suffisant. Xorg n'accélère pas cette souris (40-scaros-souris.conf) :
    l'accélération est calculée par la télécommande et par la manette.
    """

    NAME = "Scaros souris"
    BUTTONS = {"left": 1, "middle": 2, "right": 3}

    def __init__(self):
        self.dev = None
        self.tried = False
        self.hires = False
        self.lock = threading.Lock()
        self.pending = [0, 0]        # repli xdotool : petits mouvements regroupés
        self.flusher = None
        self.rest = [0, 0]           # défilement pas encore envoyé (en 1/120 de cran)
        self.dry = {"x": 0, "y": 0, "sx": 0, "sy": 0, "buttons": []}   # --dry-run : ce qui aurait été fait

    def _device(self):
        if self.tried:
            return self.dev
        self.tried = True
        if DRY or evdev is None:
            return None
        try:
            rel = [E.REL_X, E.REL_Y, E.REL_WHEEL, E.REL_HWHEEL]
            hires = [getattr(E, n) for n in ("REL_WHEEL_HI_RES", "REL_HWHEEL_HI_RES") if hasattr(E, n)]
            self.dev = evdev.UInput({E.EV_REL: rel + hires, E.EV_KEY: [E.BTN_LEFT, E.BTN_RIGHT, E.BTN_MIDDLE]},
                                    name=self.NAME)
            self.hires = len(hires) == 2
            log.info("souris virtuelle prête (uinput)")
        except (OSError, evdev.UInputError) as e:
            log.warning("souris virtuelle impossible (%s) : repli sur xdotool, moins fluide", e)
        return self.dev

    def wake(self):
        """Mode souris activé : la souris existe tout de suite (Xorg met une demi-seconde à la voir)
        et le curseur se montre au milieu de l'écran."""
        if DRY:
            return
        self._device()
        geo = run(["xdotool", "getdisplaygeometry"], timeout=3, quiet=True)[1].split()
        if len(geo) == 2 and all(g.isdigit() for g in geo):
            run(["xdotool", "mousemove", str(int(geo[0]) // 2), str(int(geo[1]) // 2)], timeout=3, quiet=True)

    def move(self, dx, dy):
        if DRY:
            self.dry["x"] += dx
            self.dry["y"] += dy
        if not (dx or dy) or DRY:
            return
        dev = self._device()
        with self.lock:
            if dev:
                if dx:
                    dev.write(E.EV_REL, E.REL_X, dx)
                if dy:
                    dev.write(E.EV_REL, E.REL_Y, dy)
                dev.syn()
                return
            self.pending[0] += dx
            self.pending[1] += dy
            if self.flusher is None:  # un xdotool toutes les 25 ms au plus
                self.flusher = threading.Timer(0.025, self._flush)
                self.flusher.daemon = True
                self.flusher.start()

    def _flush(self):
        with self.lock:
            (dx, dy), self.pending, self.flusher = self.pending, [0, 0], None
        if dx or dy:
            run(["xdotool", "mousemove_relative", "--", str(dx), str(dy)], timeout=2, quiet=True)

    def button(self, name, action):
        """action : "click", "down" ou "up"."""
        if DRY:
            self.dry["buttons"] = (self.dry["buttons"] + [f"{action} {name}"])[-20:]
            return
        dev = self._device()
        if dev:
            code = {"left": E.BTN_LEFT, "middle": E.BTN_MIDDLE, "right": E.BTN_RIGHT}[name]
            with self.lock:
                if action in ("click", "down"):
                    dev.write(E.EV_KEY, code, 1)
                    dev.syn()
                if action in ("click", "up"):
                    dev.write(E.EV_KEY, code, 0)
                    dev.syn()
            return
        self._flush()  # le curseur arrive d'abord à sa place
        cmd = {"click": "click", "down": "mousedown", "up": "mouseup"}[action]
        run(["xdotool", cmd, str(self.BUTTONS[name])], timeout=2, quiet=True)

    def scroll(self, dx, dy):
        """Défile de dx, dy en 1/120 de cran (dy > 0 : vers le bas de la page)."""
        if DRY:
            self.dry["sx"] += dx
            self.dry["sy"] += dy
        if not (dx or dy) or DRY:
            return
        dev = self._device()
        with self.lock:
            self.rest[0] += dx
            self.rest[1] += dy
            nx, ny = int(self.rest[0] / 120), int(self.rest[1] / 120)  # crans entiers
            self.rest[0] -= nx * 120
            self.rest[1] -= ny * 120
            if dev:
                # défilement fin (pixel près) pour les applis récentes, crans entiers pour les autres
                if self.hires and dy:
                    dev.write(E.EV_REL, E.REL_WHEEL_HI_RES, -dy)
                if self.hires and dx:
                    dev.write(E.EV_REL, E.REL_HWHEEL_HI_RES, dx)
                if ny:
                    dev.write(E.EV_REL, E.REL_WHEEL, -ny)
                if nx:
                    dev.write(E.EV_REL, E.REL_HWHEEL, nx)
                dev.syn()
                return
        for n, neg, pos in ((ny, 4, 5), (nx, 6, 7)):
            if n:
                run(["xdotool", "click", "--repeat", str(min(abs(n), 20)), str(pos if n > 0 else neg)],
                    timeout=3, quiet=True)


class Pad:
    """Lit la manette (Xbox en Bluetooth ou autre) et la transforme en touches.

    La manette est réservée à Scaros (grab) : les applis reçoivent des flèches,
    Entrée et Échap, ce qui marche partout. Le bouton Xbox ramène au menu.

    Exception : une appli marquée "controller": "native" dans apps.json (Steam Link)
    lit la vraie manette pour les jeux. Scaros la lui laisse et ne surveille plus que
    le bouton Xbox maintenu, qui ramène au menu (un appui court reste pour l'appli).

    Mode souris (activé depuis le centre de contrôle) : par-dessus une appli, le stick gauche
    déplace le curseur, A clique (maintenu : glisser), X ou Y fait un clic droit, le stick droit
    fait défiler. B, la croix, LB/RB, Menu et le bouton Xbox gardent leur rôle.
    """

    HOLD_HOME = 0.8
    CURSOR_SPEED = 1500      # pixels par seconde, stick à fond
    SCROLL_SPEED = 2000      # 1/120 de cran par seconde, stick à fond

    BUTTONS = {}
    if evdev is not None:
        BUTTONS = {E.BTN_SOUTH: "ok", E.BTN_EAST: "back", E.BTN_MODE: "home", E.KEY_HOMEPAGE: "home",
                   E.KEY_BACK: "back", E.BTN_TL: "voldown", E.BTN_TR: "volup",
                   E.BTN_NORTH: "playpause", E.BTN_WEST: "playpause", E.BTN_START: "menu"}
        DPAD = {E.BTN_DPAD_UP: "up", E.BTN_DPAD_DOWN: "down", E.BTN_DPAD_LEFT: "left", E.BTN_DPAD_RIGHT: "right"}
        # X et Y : selon le pilote, le bouton X arrive en BTN_NORTH ou en BTN_WEST
        MOUSE_BUTTONS = {E.BTN_SOUTH: "left", E.BTN_NORTH: "right", E.BTN_WEST: "right"}

    def __init__(self, app):
        self.app = app
        self.sources = {}            # (source, direction) -> appuyé
        self.next_fire = {}
        self.grabbed = False
        self.guide_down = None       # moment où le bouton Xbox a été enfoncé
        self.guide_fired = False
        self.axes = {}               # position des sticks, de -1 à 1
        self.right = (None, None)    # codes du stick droit (ABS_RX/RY, ou ABS_Z/RZ en Bluetooth)
        self.acc = [0.0, 0.0, 0.0, 0.0]   # déplacements et défilements pas encore envoyés
        self.held = set()            # boutons de souris enfoncés à la manette
        self.mouse = False
        self.last_tick = time.monotonic()

    def find(self):
        main = None
        devices = []
        for path in evdev.list_devices():
            try:
                devices.append(evdev.InputDevice(path))
            except OSError:
                pass
        for d in devices:
            caps = d.capabilities()
            if E.BTN_SOUTH in caps.get(E.EV_KEY, []) and E.EV_ABS in caps:
                main = d
                break
        group = []
        if main:
            # le bouton Xbox arrive parfois sur un périphérique « Consumer Control » à part
            group = [d for d in devices if d is main or (d.uniq and d.uniq == main.uniq)
                     or d.name.startswith(main.name + " ")]
        for d in devices:
            if d not in group:
                d.close()
        return main, group

    def run(self):
        while True:
            main, group = self.find()
            if not main:
                time.sleep(2)
                continue
            self.app.set_pad(main.name)
            self.grabbed = False
            try:
                self.loop(main, group)
            except OSError:
                pass  # manette éteinte ou déconnectée
            finally:
                for d in group:
                    try:
                        d.close()
                    except OSError:
                        pass
                self.sources.clear()
                self.next_fire.clear()
                self.axes.clear()
                if self.mouse:  # manette éteinte en plein clic : rien ne reste enfoncé
                    self.set_mouse(False)
                self.app.set_pad(None)

    def loop(self, main, group):
        absinfo = {}
        axes = main.capabilities().get(E.EV_ABS, [])
        codes = {c[0] if isinstance(c, tuple) else c for c in axes}
        for code in (E.ABS_X, E.ABS_Y, E.ABS_RX, E.ABS_RY, E.ABS_Z, E.ABS_RZ):
            if code not in codes:
                continue
            try:
                info = main.absinfo(code)
            except (OSError, KeyError):
                continue
            center, half = (info.max + info.min) / 2, max(1, (info.max - info.min) / 2)
            if code in (E.ABS_Z, E.ABS_RZ) and abs(info.value - center) > half * 0.5:
                continue  # gâchette (au repos à fond d'un côté), pas un stick
            absinfo[code] = (center, half)
        # stick droit : ABS_RX/RY (pilote xpad, xpadneo), sinon ABS_Z/RZ (manette Xbox en Bluetooth simple)
        if E.ABS_RX in absinfo and E.ABS_RY in absinfo:
            self.right = (E.ABS_RX, E.ABS_RY)
        elif E.ABS_Z in absinfo and E.ABS_RZ in absinfo:
            self.right = (E.ABS_Z, E.ABS_RZ)
        else:
            self.right = (None, None)
        fds = {d.fd: d for d in group}
        while True:
            native = self.app.native_controller()
            if native == self.grabbed:
                self.set_grab(group, not native)
            mouse = not native and self.app.pad_mouse()
            if mouse != self.mouse:
                self.set_mouse(mouse)
            # en mode souris, stick penché : 100 pas par seconde pour un curseur fluide
            moving = mouse and any(abs(v) > 0.15 for v in self.axes.values())
            ready, _, _ = select.select(list(fds), [], [], 0.01 if moving else 0.04)
            for fd in ready:
                for ev in fds[fd].read():
                    self.event(ev, absinfo, native)
            self.tick(native)

    def set_mouse(self, on):
        """Entrée ou sortie du mode souris : rien ne doit rester enfoncé."""
        for name in self.held:
            self.app.mouse.button(name, "up")
        self.held.clear()
        self.acc = [0.0, 0.0, 0.0, 0.0]
        self.mouse = on
        log.info("manette : %s", "curseur de souris" if on else "flèches")

    @staticmethod
    def curve(v, top):
        """Doux près du centre (précis), rapide à fond."""
        m = abs(v)
        if m < 0.15:
            return 0.0
        m = min(1.0, (m - 0.15) / 0.85)
        return math.copysign(top * m ** 2, v)

    def set_grab(self, group, grab):
        """Réserve toute la manette (bouton Xbox compris, parfois sur un périphérique à part),
        ou la rend à l'appli. Sans ça, le bouton Xbox arrivait aussi dans l'appli ouverte."""
        for d in group:
            try:
                d.grab() if grab else d.ungrab()
            except OSError as e:
                log.warning("manette %s : %s impossible (%s)", d.name, "réservation" if grab else "libération", e)
        self.grabbed = grab
        self.sources.clear()
        self.next_fire.clear()
        self.axes.clear()
        log.info("manette %s", "réservée à Scaros" if grab else "laissée à l'appli")

    def press(self, source, direction, on):
        self.sources[(source, direction)] = on

    def event(self, ev, absinfo, native=False):
        if native:
            if ev.type == E.EV_KEY and ev.code in (E.BTN_MODE, E.KEY_HOMEPAGE):
                self.guide_down = time.monotonic() if ev.value == 1 else None
                self.guide_fired = False
            return
        if ev.type == E.EV_KEY:
            if self.mouse and ev.code in self.MOUSE_BUTTONS:
                if ev.value in (0, 1):  # 2 : répétition automatique, ignorée
                    name = self.MOUSE_BUTTONS[ev.code]
                    if ev.value:
                        self.held.add(name)
                    elif name not in self.held:
                        return  # bouton enfoncé avant le mode souris : rien à relâcher
                    else:
                        self.held.discard(name)
                    self.app.mouse.button(name, "down" if ev.value else "up")
                return
            if ev.code in self.DPAD:
                self.press("dpad", self.DPAD[ev.code], ev.value != 0)
            elif ev.code in self.BUTTONS and ev.value == 1:
                self.app.submit("input", self.BUTTONS[ev.code])
        elif ev.type == E.EV_ABS:
            if ev.code == E.ABS_HAT0X:
                self.press("hat", "left", ev.value < 0)
                self.press("hat", "right", ev.value > 0)
            elif ev.code == E.ABS_HAT0Y:
                self.press("hat", "up", ev.value < 0)
                self.press("hat", "down", ev.value > 0)
            elif ev.code in absinfo:
                center, half = absinfo[ev.code]
                v = max(-1.0, min(1.0, (ev.value - center) / half))
                self.axes[ev.code] = v
                if ev.code not in (E.ABS_X, E.ABS_Y):
                    return  # stick droit : seulement en mode souris (défilement)
                neg, pos = ("left", "right") if ev.code == E.ABS_X else ("up", "down")
                for d, val in ((neg, -v), (pos, v)):
                    held = self.sources.get(("stick", d), False)
                    self.press("stick", d, val > (0.4 if held else 0.6))

    def tick(self, native=False):
        now = time.monotonic()
        dt, self.last_tick = min(0.05, max(0.0, now - self.last_tick)), now
        if self.mouse:
            a, (rx, ry) = self.axes, self.right
            self.acc[0] += self.curve(a.get(E.ABS_X, 0.0), self.CURSOR_SPEED) * dt
            self.acc[1] += self.curve(a.get(E.ABS_Y, 0.0), self.CURSOR_SPEED) * dt
            self.acc[2] += self.curve(a.get(rx, 0.0), self.SCROLL_SPEED) * dt
            self.acc[3] += self.curve(a.get(ry, 0.0), self.SCROLL_SPEED) * dt
            mx, my, sx, sy = (int(v) for v in self.acc)
            self.acc = [v - int(v) for v in self.acc]
            if mx or my:
                self.app.mouse.move(mx, my)
            if sx or sy:
                self.app.mouse.scroll(sx, sy)
        if native:
            if self.guide_down and not self.guide_fired and now - self.guide_down >= self.HOLD_HOME:
                self.guide_fired = True
                self.app.submit("input", "home")
            return
        for d in ("up", "down", "left", "right"):
            # en mode souris, le stick gauche sert au curseur : seule la croix fait des flèches
            on = any(v for (src, dd), v in self.sources.items() if dd == d and not (self.mouse and src == "stick"))
            nxt = self.next_fire.get(d)
            if on and nxt is None:
                self.app.submit("input", d)
                self.next_fire[d] = now + 0.4
            elif on and now >= nxt:
                self.app.submit("input", d)
                self.next_fire[d] = now + 0.13
            elif not on:
                self.next_fire.pop(d, None)


class DualStackServer(ThreadingHTTPServer):
    """Écoute en IPv4 et en IPv6 : l'iPhone peut trouver « scaros.local » en IPv6 (box récentes)."""

    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


_remote_cache = {}


def remote_page():
    """La page télécommande et son numéro (empreinte du fichier). Le téléphone la garde en cache :
    elle s'affiche tout de suite, puis se recharge seule si Scaros a été mis à jour."""
    f = UI / "remote.html"
    try:
        mtime = f.stat().st_mtime
    except OSError:
        return b"", ""
    if _remote_cache.get("mtime") != mtime:
        raw = f.read_bytes()
        build = hashlib.sha1(raw).hexdigest()[:12]
        _remote_cache.update(mtime=mtime, build=build,
                             body=raw.replace(b'content="__BUILD__"', f'content="{build}"'.encode(), 1))
    return _remote_cache["body"], _remote_cache["build"]


class Handler(BaseHTTPRequestHandler):
    server_version = "Scaros/" + VERSION
    # connexions gardées ouvertes : chaque touche du téléphone part sans refaire de connexion
    protocol_version = "HTTP/1.1"
    timeout = 30
    TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
             ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2",
             ".json": "application/json", ".txt": "text/plain; charset=utf-8",
             ".webmanifest": "application/manifest+json"}

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def send(self, code, body=b"", ctype="application/json", cors=False, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        # polices et icônes : gardées une semaine (la télécommande s'ouvre plus vite)
        if isinstance(cache, str):
            self.send_header("Cache-Control", cache)
        else:
            self.send_header("Cache-Control", "max-age=604800" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, data, code=200):
        self.send(code, json.dumps(data, ensure_ascii=False).encode())

    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def address_string(self):
        return self.client_address[0].removeprefix("::ffff:")

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/api/state":
            return self.json(self.app.state(lite="lite" in url.query))
        if path == "/api/ping":
            # la télécommande vérifie si scaros.local répond (nom stable pour l'écran d'accueil)
            return self.send(200, json.dumps({"ok": True, "name": socket.gethostname()}).encode(), cors=True)
        if path == "/api/events":
            return self.events()
        if path == "/api/mouse":
            return self.mouse_socket()
        if path == "/api/dev/mouse" and DRY:  # tests : ce que la souris aurait fait
            return self.json(self.app.mouse.dry)
        if path == "/api/weather/search":
            q = urllib.parse.parse_qs(url.query).get("q", [""])[0].strip()[:60]
            if len(q) < 2:
                return self.json({"results": []})
            try:
                return self.json({"results": self.app.search_city(q)})
            except (OSError, ValueError, KeyError) as e:
                log.info("recherche de ville impossible : %s", e)
                return self.json({"error": "offline", "results": []}, 502)
        if path.startswith("/api/img/"):
            hit = self.app.media.images.get(path[len("/api/img/"):])
            if not hit:
                return self.send(404, b"", "text/plain")
            return self.send(200, hit[0], hit[1], cache="max-age=2592000")
        if path == "/api/np":
            return self.json({"np": self.app.media.now_playing()})
        if path == "/api/np/cover":
            try:
                data = self.app.airplay_cover.read_bytes()
            except OSError:
                return self.send(404, b"", "text/plain")
            return self.send(200, data, image_type(data) or "image/jpeg", cache="max-age=3600")
        if path == "/api/accounts":
            return self.json({**self.app.media.public(), "leagues": {k: v[0] for k, v in LEAGUES.items()}})
        if path == "/api/sport/teams":
            league = urllib.parse.parse_qs(url.query).get("league", [""])[0]
            try:
                return self.json({"teams": self.app.media.sports.teams(league)})
            except (OSError, ValueError) as e:
                log.info("équipes %s indisponibles : %s", league, e)
                return self.json({"error": "offline", "teams": []}, 502)
        if path == "/api/spotify/callback":
            q = urllib.parse.parse_qs(url.query)
            err = "Connexion annulée sur Spotify." if q.get("error") else \
                self.app.media.spotify.finish(q.get("code", [""])[0], q.get("state", [""])[0])
            self.app.spotify_done(err)
            msg = err or "Spotify est connecté à Scaros. Tu peux fermer cette page."
            html = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                    "<body style='font:24px system-ui;background:#000;color:#fff;display:grid;place-items:center;height:90vh;"
                    f"text-align:center;padding:1em'>{msg}</body>")
            return self.send(200, html.encode(), "text/html; charset=utf-8")
        if path.startswith("/api/appicon/"):
            f = app_icon(self.app.conf(path.rsplit("/", 1)[-1]).get("flatpak"))
            if not f:
                return self.send(404, b"", "text/plain")
            return self.send(200, f.read_bytes(), "image/svg+xml" if f.suffix == ".svg" else "image/png", cache="max-age=86400")
        if path == "/api/store":
            return self.json(self.app.store_state())
        if path == "/api/qr.svg":
            return self.qr()
        if path == "/api/diag":
            return self.json({**self.app.diag, "report": self.app.diag_report()})
        if path in ("/diag", "/diag/"):
            # pour lire ou copier le rapport depuis un PC du même réseau
            text = self.app.diag_report() or "Pas encore de diagnostic : lance-le dans Réglages › Wi-Fi › Diagnostic réseau.\n"
            return self.send(200, text.encode(), "text/plain; charset=utf-8")
        if path in ("/remote", "/remote/"):
            body, build = remote_page()
            if build and self.headers.get("If-None-Match") == f'"{build}"':
                self.send_response(304)
                self.send_header("ETag", f'"{build}"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", f'"{build}"')
            # gardée un jour : l'icône de l'écran d'accueil s'ouvre sans attendre le réseau
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/":
            return self.file(UI / "index.html")
        f = (UI / path.lstrip("/")).resolve()
        if UI in f.parents and f.is_file():
            return self.file(f)
        self.send(404, b"introuvable", "text/plain; charset=utf-8")

    def file(self, f):
        cache = f.suffix in (".woff2", ".png") and f.parent.name in ("fonts", "icons")
        self.send(200, f.read_bytes(), self.TYPES.get(f.suffix, "application/octet-stream"), cache=cache)

    def qr(self):
        if qrcode is None:
            return self.send(404, b"python3-qrcode absent", "text/plain; charset=utf-8")
        img = qrcode.make(self.app.state()["remote"]["url"], border=2,
                          image_factory=qrcode.image.svg.SvgPathFillImage)
        buf = io.BytesIO()
        img.save(buf)
        self.send(200, buf.getvalue(), "image/svg+xml")

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")  # flux sans longueur : la connexion lui est réservée
        self.close_connection = True
        self.end_headers()
        ip = self.address_string()
        q = self.app.bus.subscribe(ip)
        phone = not self.is_local()
        if phone:
            self.app.stream_opened(ip)
        # un téléphone parti sans prévenir ne doit pas bloquer ce fil indéfiniment
        self.connection.settimeout(20)
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    if msg is Bus.CLOSE:
                        break
                    self.wfile.write(f"data: {msg}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.bus.unsubscribe(q)
            if phone:
                self.app.stream_closed(ip)

    WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def mouse_socket(self):
        """Pavé souris du téléphone : une connexion WebSocket gardée ouverte, chaque geste part
        aussitôt (pas d'aller-retour par geste comme avec une requête). Messages : voir mouse_event."""
        key = self.headers.get("Sec-WebSocket-Key")
        origin = self.headers.get("Origin")
        if (self.headers.get("Upgrade") or "").lower() != "websocket" or not key:
            return self.json({"error": "WebSocket attendu"}, 400)
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            log.warning("souris refusée depuis %s (origine %s)", self.address_string(), origin)
            return self.json({"error": "requête refusée"}, 403)
        accept = base64.b64encode(hashlib.sha1((key.strip() + self.WS_GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        if not self.is_local():
            self.app.hello(self.address_string())
        self.connection.settimeout(70)  # le téléphone envoie un « k » toutes les 20 s
        try:
            while True:
                op, data = self._ws_frame()
                if op == 8:  # fermeture
                    self._ws_send(8, data[:2])
                    break
                if op == 9:  # ping
                    self._ws_send(10, data)
                elif op == 1:
                    for line in data.decode("utf-8", "replace").splitlines():
                        if line == "k":
                            if not self.is_local():
                                self.app.hello(self.address_string())
                        else:
                            self.app.mouse_event(line)
        except (OSError, ValueError, ConnectionError):
            pass

    def _ws_read(self, n):
        data = self.rfile.read(n)
        if len(data) < n:
            raise ConnectionError("WebSocket fermé")
        return data

    def _ws_frame(self):
        b0, b1 = self._ws_read(2)
        op, masked, n = b0 & 0x0F, b1 & 0x80, b1 & 0x7F
        if n == 126:
            n = int.from_bytes(self._ws_read(2), "big")
        elif n == 127:
            n = int.from_bytes(self._ws_read(8), "big")
        if n > 65536:
            raise ValueError("message trop gros")
        mask = self._ws_read(4) if masked else b"\0\0\0\0"
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(self._ws_read(n)))
        return op, data

    def _ws_send(self, op, data=b""):
        self.wfile.write(bytes([0x80 | op, len(data)]) + data)
        self.wfile.flush()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 10000:  # corps trop gros : on n'en lit qu'une partie, la connexion ne resservira pas
            self.close_connection = True
        raw = self.rfile.read(min(max(length, 0), 10000))
        # seuls le menu et la télécommande pilotent Scaros : une page web ouverte ailleurs (dans une appli,
        # sur un autre site) ne doit pas pouvoir éteindre le PC ou changer le Wi-Fi à ton insu
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        origin = self.headers.get("Origin")
        if ctype != "application/json" or (origin and urlparse(origin).netloc != self.headers.get("Host")):
            log.warning("requête refusée : %s depuis %s (origine %s)", path, self.address_string(), origin or "?")
            return self.json({"error": "requête refusée"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self.json({"error": "JSON invalide"}, 400)
        if not isinstance(body, dict):
            body = {}
        a = self.app
        if not self.is_local():
            a.hello(self.address_string())

        # touches, textes et lancements passent par une seule file : ils s'exécutent dans l'ordre
        if path == "/api/launch":
            app_id = str(body.get("id"))
            if app_id not in a.apps:
                return self.json({"error": "appli inconnue"}, 404)
            a.submit("launch", app_id)
            return self.json({"ok": True})
        if path == "/api/close":
            a.submit("input", "home")
            return self.json({"ok": True})
        if path == "/api/resume":
            a.submit("resume")
            return self.json({"ok": True})
        if path == "/api/display":
            if "res" in body:
                ok = a.set_tv_res(str(body.get("res")))
            else:
                ok = a.set_mode(str(body.get("mode")))
            return self.json({"ok": ok}, 200 if ok else 400)
        if path == "/api/input":
            action = str(body.get("action"))
            if action not in ACTIONS:
                return self.json({"error": "action inconnue"}, 400)
            a.submit("input", action)
            return self.json({"ok": True})
        if path == "/api/text":
            a.submit("text", str(body.get("text", "")))
            return self.json({"ok": True})
        if path == "/api/mouse":
            # {"on": true/false} : mode souris ; {"events": [...]} : gestes, si le WebSocket ne passe pas
            if "on" in body:
                a.set_mouse_mode(body.get("on"))
            for ev in (body.get("events") or [])[:100]:
                a.mouse_event(ev)
            return self.json({"ok": True, "on": a.mouse_mode})
        if path == "/api/hello":
            return self.json({"ok": True})
        if path == "/api/wifi/scan":
            threading.Thread(target=a.wifi_scan, daemon=True).start()
            return self.json({"ok": True})
        if path in ("/api/wifi/connect", "/api/wifi/forget"):
            ssid, password = body.get("ssid"), body.get("password")
            if not isinstance(ssid, str) or not 0 < len(ssid) <= 64 or (password is not None and (not isinstance(password, str) or len(password) > 128)):
                return self.json({"error": "réseau invalide"}, 400)
            if path == "/api/wifi/connect":
                threading.Thread(target=a.wifi_connect, args=(ssid, password or None), daemon=True).start()
            else:
                threading.Thread(target=a.wifi_forget, args=(ssid,), daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/apps/update":
            threading.Thread(target=a.update_apps, daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/airplay":
            if "enabled" in body:
                return self.json({"ok": a.set_airplay(bool(body.get("enabled")))})
            if body.get("action") == "check":
                threading.Thread(target=a.check_airplay, daemon=True).start()
                return self.json({"ok": True})
            if body.get("action") == "restart":
                threading.Thread(target=a.restart_airplay, daemon=True).start()
                return self.json({"ok": True})
            if body.get("action") == "stop":
                with a.lock:
                    a.overlay = False  # le menu était devant : on arrête puis on revient au menu
                threading.Thread(target=a.stop_airplay, daemon=True).start()
                return self.json({"ok": True})
            return self.json({"error": "action inconnue"}, 400)
        if path == "/api/update/check":
            threading.Thread(target=a.check_update, daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/update/start":
            if not self.is_local():
                return self.json({"error": "réservé au PC"}, 403)
            return self.json({"ok": a.start_update()})
        if path == "/api/diag/start":
            return self.json({"ok": a.start_diag()})
        if path == "/api/update/ack":
            a.ack_update()
            return self.json({"ok": True})
        if path == "/api/weather/city":
            if body.get("auto"):
                a.set_city(None)
                return self.json({"ok": True})
            try:
                place = {"city": str(body["name"])[:80], "lat": float(body["lat"]), "lon": float(body["lon"]), "auto": False}
            except (KeyError, TypeError, ValueError):
                return self.json({"error": "ville invalide"}, 400)
            if not (-90 <= place["lat"] <= 90 and -180 <= place["lon"] <= 180):
                return self.json({"error": "ville invalide"}, 400)
            a.set_city(place)
            return self.json({"ok": True})
        if path == "/api/weather/refresh":
            threading.Thread(target=a.refresh_weather, daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/prefs":
            ok = a.set_pref(str(body.get("key")), body.get("value"))
            return self.json({"ok": ok}, 200 if ok else 400)
        if path == "/api/audio":
            ok = a.set_audio_mode(str(body.get("mode")), body.get("mac") or None)
            return self.json({"ok": ok}, 200 if ok else 400)
        if path == "/api/bt/scan":
            threading.Thread(target=a.bt_scan, daemon=True).start()
            return self.json({"ok": True})
        if path in ("/api/bt/connect", "/api/bt/disconnect", "/api/bt/remove"):
            mac = str(body.get("mac", ""))
            if not MAC.fullmatch(mac):
                return self.json({"error": "adresse Bluetooth invalide"}, 400)
            action = {"/api/bt/connect": a.bt_connect, "/api/bt/disconnect": a.bt_disconnect,
                      "/api/bt/remove": a.bt_remove}[path]
            threading.Thread(target=action, args=(mac,), daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/pair":
            threading.Thread(target=a.pair, daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/power":
            if not self.is_local():
                return self.json({"error": "réservé au PC"}, 403)
            action = body.get("action")
            if action not in ("off", "restart"):
                return self.json({"error": "action inconnue"}, 400)
            threading.Thread(target=a.power, args=(action,), daemon=True).start()
            return self.json({"ok": True})
        # ---------- accueil, comptes, musique ----------
        m = a.media
        if path == "/api/store/install":
            app_id = str(body.get("id", ""))
            if app_id not in store.BY_ID:
                return self.json({"error": "appli inconnue"}, 404)
            threading.Thread(target=a.store_install, args=(app_id,), daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/store/uninstall":
            app_id = str(body.get("id", ""))
            if app_id not in a.user_apps:
                return self.json({"error": "appli inconnue"}, 404)
            threading.Thread(target=a.store_uninstall, args=(app_id,), daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/store/web":
            app_id, err = a.store_add_web(str(body.get("name", "")), str(body.get("url", "")))
            return self.json({"ok": not err, "error": err, "id": app_id})
        if path == "/api/media/refresh":
            threading.Thread(target=m.refresh, kwargs={"force": bool(body.get("force"))}, daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/media/open":
            res = m.open_item(str(body.get("id", "")))
            return self.json(res, 404 if res.get("error") == "introuvable" else 200)
        if path == "/api/media/request":
            try:
                mtype, tmdb = str(body.get("type")), int(body.get("id"))
            except (TypeError, ValueError):
                return self.json({"error": "demande invalide"}, 400)
            err = m.request(mtype, tmdb, str(body.get("title", ""))[:200])
            return self.json({"ok": not err, "error": err})
        if path == "/api/np/control":
            action = body.get("action")
            if action not in ("toggle", "play", "pause", "next", "prev"):
                return self.json({"error": "action inconnue"}, 400)
            return self.json({"ok": m.control(action), "np": m.now_playing()})
        if path == "/api/accounts/plex":
            action = body.get("action")
            if action == "link":
                ok = m.plex.start_link()
            elif action == "logout":
                m.plex.logout()
                ok = True
            elif action == "manual":
                server, token = str(body.get("server", "")).strip()[:300], str(body.get("token", "")).strip()[:200]
                if not re.match(r"^https?://[^\s/]+", server) or not token:
                    return self.json({"ok": False, "error": "Adresse (http://…:32400) et jeton nécessaires."})
                err = m.plex.set_manual(server, token)
                if err:
                    return self.json({"ok": False, "error": err})
                ok = True
            else:
                return self.json({"error": "action inconnue"}, 400)
            m.changed()
            threading.Thread(target=m.refresh, kwargs={"force": True}, daemon=True).start()
            return self.json({"ok": ok, "accounts": m.public()})
        if path == "/api/accounts/overseerr":
            if body.get("action") == "logout":
                m.overseerr.logout()
                err = None
            else:
                url_, key = str(body.get("url", ""))[:300], str(body.get("key", ""))[:200]
                if not url_.strip() or not key.strip():
                    return self.json({"ok": False, "error": "Adresse et clé API nécessaires."})
                err = m.overseerr.connect(url_, key)
            m.changed()
            if not err:
                threading.Thread(target=m.refresh, kwargs={"force": True}, daemon=True).start()
            return self.json({"ok": not err, "error": err, "accounts": m.public()})
        if path == "/api/accounts/spotify":
            sp, action, err, extra = m.spotify, body.get("action"), None, {}
            if "clientId" in body:
                err = sp.set_client(str(body.get("clientId"))[:64])
            elif action == "auth":  # lien à ouvrir sur le téléphone
                extra["url"] = sp.auth_url()
                err = None if extra["url"] else "Enregistre d'abord le Client ID."
            elif action == "tv":    # connexion sur la télé (clavier et souris de la télécommande)
                u = sp.auth_url()
                if u:
                    a.launch_url("spotifylogin", "Spotify", u, "scaros-spotify")
                else:
                    err = "Enregistre d'abord le Client ID."
            elif action == "code":  # adresse copiée depuis Safari après la connexion sur le téléphone
                err = sp.finish_url(str(body.get("url", ""))[:4000])
                a.spotify_done(err, close=False)
            elif action == "logout":
                sp.logout()
            else:
                return self.json({"error": "action inconnue"}, 400)
            m.changed()
            return self.json({"ok": not err, "error": err, "accounts": m.public(), **extra})
        if DRY and path == "/api/dev/airplay":
            threading.Thread(target=a.dry_airplay, daemon=True).start()
            return self.json({"ok": True})
        if DRY and path == "/api/dev/weather":
            a.dry_weather += 1
            threading.Thread(target=a.refresh_weather, daemon=True).start()
            return self.json({"ok": True})
        if DRY and path == "/api/dev/hdmi":
            threading.Thread(target=a.set_hdmi, args=(not a.hdmi,), daemon=True).start()
            return self.json({"ok": True})
        if DRY and path == "/api/dev/pad":
            a.set_pad(None if a.pad else "Xbox Wireless Controller")
            return self.json({"ok": True})
        self.json({"error": "introuvable"}, 404)


def main():
    global DRY
    ap = argparse.ArgumentParser(description="Serveur local de Scaros OS")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SCAROS_PORT", "8421")))
    ap.add_argument("--dry-run", action="store_true", help="ne lance aucune commande système (tests)")
    ap.add_argument("--pair", action="store_true", help="appaire une manette Bluetooth puis quitte")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    DRY = args.dry_run
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    apps = json.loads((ROOT / "apps.json").read_text())
    app = Scaros(apps, args.port)
    if args.pair:
        sys.exit(0 if app.pair() else 1)

    app.start()
    try:
        server = DualStackServer(("::", args.port), Handler)
    except OSError:  # IPv6 désactivé sur ce PC : IPv4 seulement
        server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True
    server.app = app
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
    log.info("Scaros %s prêt sur le port %s%s", VERSION, args.port, " (dry-run)" if DRY else "")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close_app(focus=False)
        p = app.airplay_proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass


if __name__ == "__main__":
    main()
