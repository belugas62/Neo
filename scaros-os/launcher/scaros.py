#!/usr/bin/env python3
"""Scaros OS — le serveur local du menu TV.

Il sert le menu (ui/index.html) et la télécommande téléphone (ui/remote.html),
lance les applis, choisit l'écran (PC ou télé en HDMI), règle le son, lit la
manette et affiche ce que joue Spotify.

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

VERSION = "0.1"
ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "scaros"
LAUNCHER_CLASS = "ScarosLauncher"
INTERNAL = re.compile(r"^(eDP|LVDS|DSI)", re.I)
KEYS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "ok": "Return", "back": "Escape", "playpause": "space"}
ACTIONS = set(KEYS) | {"home", "volup", "voldown", "ch1", "ch2", "ch3", "ch4"}
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


class Bus:
    """Diffuse les événements vers les pages ouvertes (Server-Sent Events)."""

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def emit(self, kind, **data):
        msg = json.dumps({"type": kind, **data}, ensure_ascii=False)
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


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
        self.now = None
        self.current = None          # (id, Popen) de l'appli ouverte
        self.installed = {}
        self.phones = {}             # ip -> dernière visite
        self.pairing = False
        self.windows = []            # fenêtres de l'appli ouverte
        self.launcher_ids = set()
        self.focus_checked = 0.0
        self.audio_mode = self.settings.get("audio_mode", "auto")
        self.audio_target = None
        self.audio_device = None
        self.bt_scanning = False
        self.dry_bt = []             # appareils Bluetooth simulés en --dry-run

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

    def wifi(self):
        if DRY:
            return "Wi-Fi de test"
        code, out = run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"], timeout=4, quiet=True)
        for line in out.splitlines():
            if line.startswith("yes:"):
                return line[4:] or None
        code, out = run(["iwgetid", "-r"], timeout=3, quiet=True)
        return out.strip() or None

    def phone_count(self):
        limit = time.time() - 300
        return sum(1 for t in self.phones.values() if t > limit)

    def state(self):
        ip = lan_ip()
        with self.lock:
            return {
                "version": VERSION, "dry": DRY,
                "mode": self.mode, "hdmi": self.hdmi, "output": self.output,
                "pad": self.pad, "padSupport": evdev is not None,
                "volume": self.volume, "now": self.now,
                "app": self.current[0] if self.current else None,
                "apps": {k: {"name": v["name"], "installed": self.installed.get(k, True)}
                         for k, v in self.apps.items()},
                "remote": {"url": f"http://{ip}:{self.port}/remote", "phones": self.phone_count(),
                           "qr": qrcode is not None},
                "wifi": self.wifi(), "ip": ip,
                "audio": {"mode": self.audio_mode, "target": self.audio_target, "device": self.audio_device},
                "bt": {"devices": self.bt_devices(), "scanning": self.bt_scanning},
            }

    def refresh_installed(self):
        for k, v in self.apps.items():
            ok = True
            if DRY:
                ok = True
            elif v.get("flatpak"):
                ok = run(["flatpak", "info", v["flatpak"]], timeout=15, quiet=True)[0] == 0
            elif v.get("cmd"):
                ok = shutil.which(v["cmd"][0]) is not None
            self.installed[k] = ok
        log.info("applis installées : %s", self.installed)

    # ---------- applis ----------
    def launch(self, app_id):
        conf = self.apps.get(app_id)
        if not conf:
            return False
        if conf.get("builtin"):
            self.close_app()
            self.bus.emit("app", id=app_id, state="running")
            return True
        if not self.installed.get(app_id, True):
            self.refresh_installed()
        if not self.installed.get(app_id, True):
            self.bus.emit("app", id=app_id, state="missing")
            return False
        self.close_app(focus=False)
        cmd = ["sleep", "6"] if DRY else conf["cmd"]
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
        log.info("lancé : %s (pid %s)", app_id, p.pid)
        self.bus.emit("app", id=app_id, state="running")
        threading.Thread(target=self._watch, args=(app_id, p, time.time()), daemon=True).start()
        threading.Thread(target=self._track_windows, args=(app_id, p, before), daemon=True).start()
        return True

    def _watch(self, app_id, p, started):
        code = p.wait()
        with self.lock:
            mine = self.current is not None and self.current[1] is p
            if mine:
                self.current = None
        if not mine:
            return
        quick = time.time() - started < 4
        log.info("%s s'est fermé (code %s)", app_id, code)
        self.bus.emit("app", id=app_id, state="failed" if quick and code else "closed")
        self.focus_launcher()

    def close_app(self, focus=True):
        with self.lock:
            cur, self.current = self.current, None
        if not cur:
            return
        app_id, p = cur
        self.windows = []
        flatpak_id = self.apps[app_id].get("flatpak")
        if flatpak_id:
            run(["flatpak", "kill", flatpak_id], quiet=True)
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
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

    def _track_windows(self, app_id, p, before):
        """Repère les fenêtres de l'appli et lui donne le focus dès qu'elles s'affichent.

        Sans ça, Openbox peut laisser le focus au menu : les touches de la manette
        partiraient alors vers le menu caché derrière l'appli.
        """
        deadline = time.time() + 90
        while time.time() < deadline and p.poll() is None:
            with self.lock:
                if self.current is None or self.current[1] is not p:
                    return
            new = [w for w in self._visible_windows() if w not in before]
            if new and new != self.windows:
                self.windows = new
                run(["xdotool", "windowactivate", new[-1]], timeout=3, quiet=True)
                log.info("%s : fenêtre %s activée", app_id, new[-1])
            time.sleep(0.7)

    def ensure_app_focus(self):
        """Avant d'envoyer une touche : si le menu a repris le focus, le rend à l'appli."""
        if DRY or self.current is None or not self.windows:
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

    # ---------- entrées (manette, téléphone) ----------
    def handle(self, action):
        if action == "home":
            self.close_app()
            self.bus.emit("home")
        elif action in ("volup", "voldown"):
            self.change_volume(5 if action == "volup" else -5)
        elif action.startswith("ch"):
            ids = self.channels()
            n = int(action[2:]) - 1
            if 0 <= n < len(ids):
                self.launch(ids[n])
        elif action == "playpause" and self.current is None and self.now:
            run(["playerctl", "-p", "spotifyd", "play-pause"], timeout=3)
        elif action in KEYS:
            key = KEYS[action]
            cur = self.current
            if cur:
                # une appli peut demander une autre touche, ex. "keys": {"back": "BackSpace"} dans apps.json
                key = self.apps[cur[0]].get("keys", {}).get(action, key)
                self.ensure_app_focus()
            run(["xdotool", "key", "--clearmodifiers", key], timeout=3)

    def type_text(self, text):
        text = text.replace("\n", " ").strip()[:200]
        if text:
            run(["xdotool", "type", "--clearmodifiers", "--delay", "40", "--", text], timeout=20)

    def set_pad(self, name):
        with self.lock:
            if name == self.pad:
                return
            self.pad = name
        log.info("manette : %s", name or "aucune")
        self.bus.emit("pad", name=name)

    def hello(self, ip):
        before = self.phone_count()
        self.phones[ip] = time.time()
        if self.phone_count() != before:
            self.bus.emit("remote", phones=self.phone_count())

    # ---------- son ----------
    def read_volume(self):
        if DRY:
            return self.volume
        code, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=3, quiet=True)
        m = re.search(r"([\d.]+)", out)
        return round(float(m.group(1)) * 100) if code == 0 and m else None

    def change_volume(self, delta):
        if DRY:
            self.volume = max(0, min(100, (self.volume or 0) + delta))
        elif shutil.which("wpctl"):
            run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@",
                 f"{abs(delta)}%{'+' if delta > 0 else '-'}"], timeout=3)
            self.volume = self.read_volume()
        else:
            run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{delta:+d}%"], timeout=3)
        self.bus.emit("volume", level=self.volume)

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
        self.audio_target, self.audio_device = target, device
        if changed:
            log.info("son -> %s (réglage %s)", device, mode)
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
                self.bus.emit("bt", devices=self.bt_devices(), scanning=self.bt_scanning)
            last = current
            time.sleep(2)

    # ---------- Bluetooth : casques, enceintes, manettes ----------
    def _bt_info(self, mac):
        if DRY:
            return next((dict(d) for d in self.dry_bt if d["mac"] == mac), None)
        code, out = run(["bluetoothctl", "info", mac], timeout=5, quiet=True)
        if code != 0 or "Device" not in out:
            return None
        info = {"mac": mac.upper(), "name": mac, "paired": False, "connected": False, "kind": "other"}
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

    def bt_devices(self):
        """Appareils appairés, plus les casques, enceintes et manettes repérés pendant une recherche."""
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
        devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))
        return devices

    def _bt_changed(self):
        self.bus.emit("bt", devices=self.bt_devices(), scanning=self.bt_scanning)

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
                devices = self.bt_devices()
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
        info = self._bt_info(mac) or {"name": mac, "paired": False, "kind": "other"}
        self.bus.emit("btmsg", state="connecting", name=info["name"])
        if DRY:
            time.sleep(1.2)
            for d in self.dry_bt:
                if d["mac"] == mac:
                    d.update(paired=True, connected=True)
            ok = True
        else:
            if not info["paired"]:
                run(["bluetoothctl", "--agent", "NoInputNoOutput", "pair", mac], timeout=30)
            run(["bluetoothctl", "trust", mac], timeout=10)
            ok = run(["bluetoothctl", "connect", mac], timeout=30)[0] == 0
        if ok and info["kind"] == "audio":
            self.settings["audio_bt"] = mac.upper()
            self._save_settings()
        self.bus.emit("btmsg", state="connected" if ok else "failed", name=info["name"])
        self._bt_changed()

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
        code, out = run(["xrandr", "--query"], timeout=8, quiet=True)
        internal, external = [], []
        for line in out.splitlines():
            m = re.match(r"^(\S+) (connected|disconnected)", line)
            if m:
                (internal if INTERNAL.match(m.group(1)) else external).append((m.group(1), m.group(2) == "connected"))
        return internal, external

    def apply_output(self):
        with self.lock:
            target = "tv" if self.mode in ("auto", "tv") and self.hdmi else "pc"
            if self.mode == "pc":
                target = "pc"
        internal, external = self._xrandr_outputs()
        ext = next((n for n, c in external if c), None)
        own = next((n for n, c in internal if c), internal[0][0] if internal else None)
        if not DRY:
            if target == "tv" and not ext:
                target = "pc"
            cmd = ["xrandr"]
            if target == "tv":
                cmd += ["--output", ext, "--auto", "--primary"] + (["--output", own, "--off"] if own else [])
            else:
                cmd += (["--output", own, "--auto", "--primary"] if own else [])
                for name, _ in external:
                    cmd += ["--output", name, "--off"]
            if len(cmd) > 1 and run(cmd, timeout=15)[0] != 0:
                run(["xrandr", "--auto"], timeout=15)
        with self.lock:
            self.output = target
        log.info("écran : mode=%s hdmi=%s -> %s", self.mode, self.hdmi, target)
        self.bus.emit("display", mode=self.mode, hdmi=self.hdmi, output=self.output)
        self.apply_audio()

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

    # ---------- Spotify ----------
    def _watch_spotify(self):
        fmt = "{{status}}\t{{xesam:title}}\t{{xesam:artist}}\t{{xesam:album}}\t{{mpris:artUrl}}"
        while True:
            now = None
            if not DRY:
                code, out = run(["playerctl", "-p", "spotifyd", "metadata", "--format", fmt], timeout=3, quiet=True)
                parts = out.rstrip("\n").split("\t")
                if code == 0 and len(parts) == 5 and parts[0] in ("Playing", "Paused"):
                    now = dict(zip(("status", "title", "artist", "album", "art"), parts))
            else:
                now = self.now
            if now != self.now:
                started = bool(now and now["status"] == "Playing" and not (self.now and self.now["status"] == "Playing"))
                self.now = now
                self.bus.emit("nowplaying", now=now, started=started)
            time.sleep(2)

    def set_now(self, now):
        started = bool(now and now.get("status") == "Playing" and not (self.now and self.now.get("status") == "Playing"))
        self.now = now
        self.bus.emit("nowplaying", now=now, started=started)

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

    # ---------- démarrage ----------
    def start(self):
        self.hdmi = self.hdmi_connected()
        self.volume = self.read_volume()
        threading.Thread(target=self.refresh_installed, daemon=True).start()
        threading.Thread(target=self.apply_output, daemon=True).start()
        threading.Thread(target=self._watch_hdmi, daemon=True).start()
        threading.Thread(target=self._watch_spotify, daemon=True).start()
        threading.Thread(target=self._watch_audio, daemon=True).start()
        if evdev is not None:
            threading.Thread(target=Pad(self).run, daemon=True).start()
        else:
            log.warning("python3-evdev absent : la manette sera lue par le menu uniquement")


class Pad:
    """Lit la manette (Xbox en Bluetooth ou autre) et la transforme en touches.

    La manette est réservée à Scaros (grab) : les applis reçoivent des flèches,
    Entrée et Échap, ce qui marche partout. Le bouton Xbox ramène au menu.
    """

    BUTTONS = {}
    if evdev is not None:
        BUTTONS = {E.BTN_SOUTH: "ok", E.BTN_EAST: "back", E.BTN_MODE: "home", E.KEY_HOMEPAGE: "home",
                   E.KEY_BACK: "back", E.BTN_TL: "voldown", E.BTN_TR: "volup",
                   E.BTN_NORTH: "playpause", E.BTN_WEST: "playpause"}
        DPAD = {E.BTN_DPAD_UP: "up", E.BTN_DPAD_DOWN: "down", E.BTN_DPAD_LEFT: "left", E.BTN_DPAD_RIGHT: "right"}

    def __init__(self, app):
        self.app = app
        self.sources = {}            # (source, direction) -> appuyé
        self.next_fire = {}

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
            try:
                try:
                    main.grab()
                except OSError as e:
                    log.warning("manette non réservée : %s", e)
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
            ready, _, _ = select.select(list(fds), [], [], 0.04)
            for fd in ready:
                for ev in fds[fd].read():
                    self.event(ev, absinfo)
            self.tick()

    def press(self, source, direction, on):
        self.sources[(source, direction)] = on

    def event(self, ev, absinfo):
        if ev.type == E.EV_KEY:
            if ev.code in self.DPAD:
                self.press("dpad", self.DPAD[ev.code], ev.value != 0)
            elif ev.code in self.BUTTONS and ev.value == 1:
                self.app.handle(self.BUTTONS[ev.code])
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

    def tick(self):
        now = time.monotonic()
        for d in ("up", "down", "left", "right"):
            on = any(v for (src, dd), v in self.sources.items() if dd == d)
            nxt = self.next_fire.get(d)
            if on and nxt is None:
                self.app.handle(d)
                self.next_fire[d] = now + 0.4
            elif on and now >= nxt:
                self.app.handle(d)
                self.next_fire[d] = now + 0.13
            elif not on:
                self.next_fire.pop(d, None)


class Handler(BaseHTTPRequestHandler):
    server_version = "Scaros/" + VERSION
    TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
             ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2",
             ".json": "application/json", ".txt": "text/plain; charset=utf-8"}

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, data, code=200):
        self.send(code, json.dumps(data, ensure_ascii=False).encode())

    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            return self.json(self.app.state())
        if path == "/api/events":
            return self.events()
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
        self.send(200, f.read_bytes(), self.TYPES.get(f.suffix, "application/octet-stream"))

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
        q = self.app.bus.subscribe()
        if not self.is_local():
            self.app.hello(self.client_address[0])
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.bus.unsubscribe(q)

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

        if path == "/api/launch":
            return self.json({"ok": a.launch(str(body.get("id")))})
        if path == "/api/close":
            threading.Thread(target=a.close_app, daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/display":
            ok = a.set_mode(str(body.get("mode")))
            return self.json({"ok": ok}, 200 if ok else 400)
        if path == "/api/input":
            action = str(body.get("action"))
            if action not in ACTIONS:
                return self.json({"error": "action inconnue"}, 400)
            threading.Thread(target=a.handle, args=(action,), daemon=True).start()
            return self.json({"ok": True})
        if path == "/api/text":
            a.type_text(str(body.get("text", "")))
            return self.json({"ok": True})
        if path == "/api/hello":
            return self.json({"ok": True})
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
        if DRY and path == "/api/dev/hdmi":
            threading.Thread(target=a.set_hdmi, args=(not a.hdmi,), daemon=True).start()
            return self.json({"ok": True})
        if DRY and path == "/api/dev/pad":
            a.set_pad(None if a.pad else "Xbox Wireless Controller")
            return self.json({"ok": True})
        if DRY and path == "/api/dev/nowplaying":
            a.set_now(body.get("now"))
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


if __name__ == "__main__":
    main()
