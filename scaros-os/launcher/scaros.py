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
import glob
import io
import json
import logging
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

VERSION = "0.4"
ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"
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
UPDATE_API = "https://api.github.com/repos/belugas62/Neo/commits/scaros-os"
INSTALLED_COMMIT = Path("/opt/scaros/commit")              # écrit par scaros-update
UPDATE_STATUS = Path("/var/lib/scaros/update-status.json")  # progression écrite par scaros-update
PHONE_TTL = 40                # un téléphone qui ne donne plus signe de vie depuis 40 s est parti
AUDIO_MODES = ("auto", "tv", "pc", "bluetooth")
AUDIO_NAMES = {"tv": "la télé (HDMI)", "pc": "les haut-parleurs du PC"}
MAC = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", re.I)

log = logging.getLogger("scaros")
DRY = False


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
DRY_WEATHER = [  # --dry-run : réponses d'Open-Meteo simulées
    {"current": {"temperature_2m": 14.2, "weather_code": 63, "is_day": 1},
     "daily": {"temperature_2m_max": [16.1], "temperature_2m_min": [9.4]}},
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


def parse_weather(data, city):
    """Réponse d'Open-Meteo -> ce qu'affiche le menu."""
    cur = data["current"]
    code = int(cur["weather_code"])
    kind, label = WEATHER_CODES.get(code, ("cloudy", "Nuageux"))
    daily = data.get("daily") or {}
    tmax, tmin = (daily.get("temperature_2m_max") or [None])[0], (daily.get("temperature_2m_min") or [None])[0]
    return {"city": city, "temp": round(float(cur["temperature_2m"])), "code": code, "kind": kind, "label": label,
            "day": bool(cur.get("is_day", 1)), "max": None if tmax is None else round(tmax),
            "min": None if tmin is None else round(tmin), "at": int(time.time())}


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
        self.update = {"checking": False, "running": False, "available": None, "step": None}
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
                "apps": {k: {"name": v["name"], "installed": self.installed.get(k, True)}
                         for k, v in self.apps.items()},
                "prefs": self.settings.get("ui", {}),
                "audio": {"mode": self.audio_mode, "target": self.audio_target, "device": self.audio_device},
                "airplay": self.airplay_state(),
            }
        if lite:  # la télécommande n'a besoin que de ça : réponse immédiate
            return s
        # les appels lents (nmcli, bluetoothctl) se font hors du verrou, pour ne jamais retarder une touche
        nets = self.wifi_networks()
        s.update({
            "remote": {"url": f"http://{ip}:{self.port}/remote", "phones": self.phone_count(),
                       "qr": qrcode is not None},
            "wifi": self.wifi(nets), "wifiList": nets, "ip": ip, "info": system_info(),
            "bt": {"devices": self.bt_devices(), "scanning": self.bt_scanning, "audio": bt_audio_available()},
            "weather": self.weather, "place": self.settings.get("weather_place"),
            "update": self.update, "justUpdated": self.just_updated(),
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

    def launch(self, app_id, retry=True):
        conf = self.apps.get(app_id)
        if not conf:
            return False
        if not self.installed.get(app_id, True):
            self.refresh_installed()
        if not self.installed.get(app_id, True):
            self.bus.emit("app", id=app_id, state="missing")
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
        flatpak_id = self.apps[app_id].get("flatpak")
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
                self.windows = new
                if not self.overlay:
                    run(["xdotool", "windowactivate", new[-1]], timeout=3, quiet=True)
                log.info("%s : fenêtre %s activée", app_id, new[-1])
                if not self.shown:
                    self.shown = True
                    self.bus.emit("app", id=app_id, state="shown")
                    if self.apps[app_id].get("controller") == "native":
                        self.notify("gamepad", "La manette sert au jeu. Maintiens le bouton Xbox pour revenir au menu.", 6000)
            time.sleep(0.7)

    def _app_window(self):
        """La fenêtre de l'appli ouverte (la dernière repérée, sinon la plus récente qui n'est pas le menu)."""
        if self.windows:
            return self.windows[-1]
        launcher = set(run(["xdotool", "search", "--class", LAUNCHER_CLASS], timeout=3, quiet=True)[1].split())
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
            self.launcher_ids = set(run(["xdotool", "search", "--class", LAUNCHER_CLASS], timeout=3, quiet=True)[1].split())
        active = run(["xdotool", "getactivewindow"], timeout=3, quiet=True)[1].strip()
        if not active or active in self.launcher_ids:
            log.info("focus rendu à l'appli (était sur %s)", active or "aucune fenêtre")
            run(["xdotool", "windowactivate", self.windows[-1]], timeout=3, quiet=True)

    def focus_launcher(self):
        run(["xdotool", "search", "--class", LAUNCHER_CLASS, "windowactivate"], timeout=3, quiet=True)

    def show_overlay(self):
        """Centre de contrôle pendant une appli : le menu passe devant, l'appli reste ouverte derrière."""
        with self.lock:
            if (self.current is None and not self.mirroring) or self.overlay:
                return
            self.overlay = True
        log.info("centre de contrôle par-dessus %s", "la recopie AirPlay" if self.mirroring else self.current[0])
        self.osd.send(t="hide")
        self.focus_launcher()

    def resume_app(self):
        """Ferme le centre de contrôle et rend l'écran à l'appli restée ouverte."""
        with self.lock:
            if not self.overlay:
                return
            self.overlay = False
            cur = self.current
        if self.mirroring and self.airplay_win:
            run(["xdotool", "windowactivate", self.airplay_win], timeout=3, quiet=True)
            log.info("retour dans la recopie AirPlay")
            return
        if cur is None:
            return
        win = self._app_window()
        if win:
            run(["xdotool", "windowactivate", win], timeout=3, quiet=True)
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
                key = self.apps[cur[0]].get("keys", {}).get(action, key)
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
        return bool(cur and not self.overlay and not self.mirroring and self.apps[cur[0]].get("controller") == "native")

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
                "public": "(public)" in out.splitlines()[0]}
        for line in out.splitlines():
            key, _, value = line.strip().partition(": ")
            if key in ("Name", "Alias") and value:
                info["name"] = value
            elif key == "Paired":
                info["paired"] = value == "yes"
            elif key == "Connected":
                info["connected"] = value == "yes"
            elif key == "Icon":
                info["kind"] = "audio" if value.startswith("audio") else "input" if value.startswith("input") else "other"
        return info

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
            for line in out.splitlines():
                m = re.match(r"Device (\S+) (.+)", line)
                info = self._bt_info(m.group(1)) if m else None
                if not info:
                    continue
                unnamed = re.fullmatch(r"(?:[0-9A-F]{2}[-:]){5}[0-9A-F]{2}", info["name"], re.I)
                if info["paired"] or (info["kind"] in ("audio", "input") and not unnamed):
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
                run(["bluetoothctl", "power", "on"], timeout=10, quiet=True)
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
        info = self._bt_info(mac) or {"name": mac, "paired": False, "kind": "other"}
        self.bus.emit("btmsg", state="connecting", name=info["name"], mac=mac)
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
        self.bus.emit("btmsg", state=state, name=info["name"], mac=mac)
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

    # ---------- réglages de l'interface (apparence, taille du texte, veille auto) ----------
    PREFS = {"textScale": ("normal", "large", "xlarge"), "autoSleep": (0, 15, 30, 60),
             "theme": ("dark", "light", "auto"),
             "bg": ("weather", "stars", "aurora", "rain", "snow", "none")}

    def set_pref(self, key, value):
        if key not in self.PREFS or value not in self.PREFS[key]:
            return False
        self.settings.setdefault("ui", {})[key] = value
        self._save_settings()
        self.bus.emit("prefs", prefs=self.settings["ui"])
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
            run(["bluetoothctl", "power", "on"], timeout=10)
            run(["bluetoothctl", "pairable", "on"], timeout=10)
            scan = None
            if not DRY and shutil.which("bluetoothctl"):
                scan = subprocess.Popen(["bluetoothctl", "--timeout", str(seconds), "scan", "on"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            found = ("00:00:00:00:00:00", "Xbox Wireless Controller") if DRY else None
            deadline = time.time() + seconds
            while not found and time.time() < deadline:
                code, out = run(["bluetoothctl", "devices"], timeout=5, quiet=True)
                for line in out.splitlines():
                    m = re.match(r"Device (\S+) (.+)", line)
                    if m and re.search(r"xbox|controller|gamepad|manette", m.group(2), re.I):
                        found = m.groups()
                        break
                time.sleep(1.5)
            if scan:
                scan.terminate()
            if not found:
                self.bus.emit("pair", state="notfound")
                return False
            mac, name = found
            log.info("appairage de %s (%s)", name, mac)
            run(["bluetoothctl", "--agent", "NoInputNoOutput", "pair", mac], timeout=30)
            run(["bluetoothctl", "trust", mac], timeout=10)
            ok = run(["bluetoothctl", "connect", mac], timeout=30)[0] == 0
            self.bus.emit("pair", state="done" if ok else "failed", name=name)
            return ok
        finally:
            self.pairing = False

    # ---------- par-dessus les applis ----------
    def covered(self):
        """Le menu est-il caché par une appli ou par une recopie AirPlay ?"""
        return (self.current is not None and not self.overlay) or self.mirroring

    def notify(self, icon, text, ms=3500):
        if self.covered():
            self.osd.send(t="note", icon=icon, text=text, ms=ms)

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
            cmd = ["uxplay", "-n", AIRPLAY_NAME, "-nh", "-fs", "-nohold", "-reset", "15"]
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
                req = urllib.request.Request(UPDATE_API, headers={"User-Agent": "Scaros-OS",
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
            return self.settings.get("update_message") or "Scaros est à jour."
        return None

    def ack_update(self):
        self.settings["update_seen"] = self._update_status_file().get("at", 0)
        self._save_settings()

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
                if DRY:
                    data = DRY_WEATHER[self.dry_weather % len(DRY_WEATHER)]
                else:
                    data = self._http_json(
                        "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
                            "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": 1,
                            "current": "temperature_2m,weather_code,is_day",
                            "daily": "temperature_2m_max,temperature_2m_min"}))
                self.weather = parse_weather(data, place["city"])
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
        threading.Thread(target=self._airplay_loop, daemon=True).start()
        threading.Thread(target=self._watch_airplay_window, daemon=True).start()
        threading.Thread(target=self._watch_weather, daemon=True).start()
        threading.Thread(target=self._watch_updates, daemon=True).start()
        threading.Thread(target=self.osd.start, daemon=True).start()
        if evdev is not None:
            threading.Thread(target=Pad(self).run, daemon=True).start()
        else:
            log.warning("python3-evdev absent : la manette sera lue par le menu uniquement")


class Pad:
    """Lit la manette (Xbox en Bluetooth ou autre) et la transforme en touches.

    La manette est réservée à Scaros (grab) : les applis reçoivent des flèches,
    Entrée et Échap, ce qui marche partout. Le bouton Xbox ramène au menu.

    Exception : une appli marquée "controller": "native" dans apps.json (Steam Link)
    lit la vraie manette pour les jeux. Scaros la lui laisse et ne surveille plus que
    le bouton Xbox maintenu, qui ramène au menu (un appui court reste pour l'appli).
    """

    HOLD_HOME = 0.8

    BUTTONS = {}
    if evdev is not None:
        BUTTONS = {E.BTN_SOUTH: "ok", E.BTN_EAST: "back", E.BTN_MODE: "home", E.KEY_HOMEPAGE: "home",
                   E.KEY_BACK: "back", E.BTN_TL: "voldown", E.BTN_TR: "volup",
                   E.BTN_NORTH: "playpause", E.BTN_WEST: "playpause", E.BTN_START: "menu"}
        DPAD = {E.BTN_DPAD_UP: "up", E.BTN_DPAD_DOWN: "down", E.BTN_DPAD_LEFT: "left", E.BTN_DPAD_RIGHT: "right"}

    def __init__(self, app):
        self.app = app
        self.sources = {}            # (source, direction) -> appuyé
        self.next_fire = {}
        self.grabbed = False
        self.guide_down = None       # moment où le bouton Xbox a été enfoncé
        self.guide_fired = False

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
                self.app.set_pad(None)

    def loop(self, main, group):
        absinfo = {}
        for code in (E.ABS_X, E.ABS_Y):
            try:
                info = main.absinfo(code)
                absinfo[code] = ((info.max + info.min) / 2, max(1, (info.max - info.min) / 2))
            except (OSError, KeyError):
                pass
        fds = {d.fd: d for d in group}
        while True:
            native = self.app.native_controller()
            if native == self.grabbed:
                self.set_grab(group, not native)
            ready, _, _ = select.select(list(fds), [], [], 0.04)
            for fd in ready:
                for ev in fds[fd].read():
                    self.event(ev, absinfo, native)
            self.tick(native)

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
                v = (ev.value - center) / half
                neg, pos = ("left", "right") if ev.code == E.ABS_X else ("up", "down")
                for d, val in ((neg, -v), (pos, v)):
                    held = self.sources.get(("stick", d), False)
                    self.press("stick", d, val > (0.4 if held else 0.6))

    def tick(self, native=False):
        now = time.monotonic()
        if native:
            if self.guide_down and not self.guide_fired and now - self.guide_down >= self.HOLD_HOME:
                self.guide_fired = True
                self.app.submit("input", "home")
            return
        for d in ("up", "down", "left", "right"):
            on = any(v for (src, dd), v in self.sources.items() if dd == d)
            nxt = self.next_fire.get(d)
            if on and nxt is None:
                self.app.submit("input", d)
                self.next_fire[d] = now + 0.4
            elif on and now >= nxt:
                self.app.submit("input", d)
                self.next_fire[d] = now + 0.13
            elif not on:
                self.next_fire.pop(d, None)


class Handler(BaseHTTPRequestHandler):
    server_version = "Scaros/" + VERSION
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
        self.send_header("Cache-Control", "max-age=604800" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, data, code=200):
        self.send(code, json.dumps(data, ensure_ascii=False).encode())

    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

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
        if path == "/api/weather/search":
            q = urllib.parse.parse_qs(url.query).get("q", [""])[0].strip()[:60]
            if len(q) < 2:
                return self.json({"results": []})
            try:
                return self.json({"results": self.app.search_city(q)})
            except (OSError, ValueError, KeyError) as e:
                log.info("recherche de ville impossible : %s", e)
                return self.json({"error": "offline", "results": []}, 502)
        if path == "/api/qr.svg":
            return self.qr()
        if path in ("/remote", "/remote/"):
            return self.file(UI / "remote.html")
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
        self.end_headers()
        ip = self.client_address[0]
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

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 10000)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.json({"error": "JSON invalide"}, 400)
        if not isinstance(body, dict):
            body = {}
        a = self.app
        if not self.is_local():
            a.hello(self.client_address[0])

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
