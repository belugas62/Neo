"""Scaros OS — ce qui remplit l'accueil et l'écran de veille.

- Plex : « Reprendre » et « Ajoutés récemment » (connexion par code, sur plex.tv/link)
- Overseerr (ou Jellyseerr) : « Tendances » et « Mes demandes », et les nouvelles demandes
- Spotify : la musique en cours, jouée sur Scaros (spotifyd) ou ailleurs (API Web de Spotify)
- AirPlay : la pochette de la musique envoyée par l'iPhone (UxPlay -ca)
- ESPN : les scores des compétitions suivies

Les jetons et les clés restent sur le PC (accounts.json, lisible par Scaros seulement) : le menu
et la télécommande ne reçoivent que des titres et des adresses d'images servies par Scaros.
Bibliothèque standard uniquement, comme scaros.py.
"""

import base64
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("scaros")

PLEX_TV = "https://plex.tv"
SPOTIFY_ACCOUNTS = "https://accounts.spotify.com"
SPOTIFY_API = "https://api.spotify.com/v1"
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
TMDB_IMG = "https://image.tmdb.org/t/p"
SPOTIFY_SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"

# compétitions proposées dans Réglages › Sport (identifiants ESPN)
LEAGUES = {
    "fra.1": ("Ligue 1", "soccer"), "uefa.champions": ("Ligue des champions", "soccer"),
    "eng.1": ("Premier League", "soccer"), "esp.1": ("Liga", "soccer"), "ita.1": ("Serie A", "soccer"),
    "ger.1": ("Bundesliga", "soccer"), "uefa.europa": ("Ligue Europa", "soccer"),
    "fra.2": ("Ligue 2", "soccer"), "fifa.world": ("Coupe du monde", "soccer"),
    "uefa.euro": ("Euro", "soccer"), "nba": ("NBA", "basketball"),
}
DEFAULT_LEAGUES = ["fra.1", "uefa.champions", "eng.1"]
TABS = ("continue", "recent", "trending", "requests", "matches")
STAT_FR = {"Possession": "Possession", "SHOTS": "Tirs", "Shots": "Tirs", "ON GOAL": "Tirs cadrés", "Shots on Target": "Tirs cadrés",
           "Corner Kicks": "Corners", "Fouls": "Fautes", "Yellow Cards": "Cartons jaunes", "Red Cards": "Cartons rouges",
           "Offsides": "Hors-jeu", "Saves": "Arrêts"}
MAX_ITEMS = 24


def http(url, method="GET", headers=None, form=None, data=None, timeout=10, raw=False):
    """Requête HTTP(S) ; renvoie le JSON (ou les octets avec raw=True). Lève OSError/ValueError."""
    h = {"User-Agent": "Scaros-OS", "Accept": "application/json"}
    h.update(headers or {})
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = r.read(8_000_000)
        if raw:
            return payload, r.headers.get("Content-Type", "")
        if not payload.strip():
            return None
        return json.loads(payload)


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def image_type(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"GIF8":
        return "image/gif"
    return None


def year_of(s):
    return (s or "")[:4] if re.match(r"\d{4}", s or "") else ""


class Accounts:
    """Comptes reliés (Plex, Overseerr, Spotify), dans un fichier réservé au compte de Scaros."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text())
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, section):
        with self.lock:
            return dict(self.data.get(section) or {})

    def update(self, section, **values):
        with self.lock:
            cur = self.data.setdefault(section, {})
            for k, v in values.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
            self._save()

    def clear(self, section, keep=()):
        with self.lock:
            old = self.data.get(section) or {}
            self.data[section] = {k: old[k] for k in keep if k in old}
            self._save()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("comptes non enregistrés : %s", e)


class Images:
    """Affiches, pochettes et logos : téléchargés une fois par Scaros, gardés sur le disque.

    Le menu ne reçoit que /api/img/<clé> : il ne voit jamais le jeton Plex, et Scaros ne va
    chercher que des images qu'il a lui-même référencées (pas d'adresse arbitraire)."""

    def __init__(self, media, folder):
        self.media = media
        self.dir = folder
        self.map = {}
        self.lock = threading.Lock()
        self.writes = 0

    def url(self, kind, ref, w=0, h=0):
        if not ref:
            return None
        key = hashlib.sha1(f"{kind}|{ref}|{w}x{h}".encode()).hexdigest()[:20]
        with self.lock:
            self.map[key] = (kind, ref, w, h)
        return f"/api/img/{key}"

    def get(self, key):
        if not re.fullmatch(r"[0-9a-f]{20}", key or ""):
            return None
        f = self.dir / key
        try:
            data = f.read_bytes()
            kind = image_type(data)
            if kind:
                return data, kind
        except OSError:
            pass
        with self.lock:
            src = self.map.get(key)
        if not src or self.media.dry:
            return None
        try:
            url, headers = self.media.image_source(*src)
            data, _ = http(url, headers=headers, timeout=12, raw=True)
        except (OSError, ValueError, KeyError) as e:
            log.debug("image %s indisponible : %s", src[0], e)
            return None
        kind = image_type(data)
        if not kind:
            return None
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, f)
            self.writes += 1
            if self.writes % 50 == 0:
                threading.Thread(target=self.prune, daemon=True).start()
        except OSError:
            pass
        return data, kind

    def prune(self, keep=900):
        try:
            files = sorted((p for p in self.dir.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
            for p in files[:-keep]:
                p.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
class Plex:
    def __init__(self, media):
        self.m = media
        self.link = None            # code plex.tv/link en cours
        self.error = None
        self.lock = threading.Lock()
        self.discovered_at = 0.0

    @property
    def conf(self):
        return self.m.accounts.get("plex")

    def client_id(self):
        cid = self.conf.get("clientId")
        if not cid:
            cid = "scaros-" + uuid.uuid4().hex
            self.m.accounts.update("plex", clientId=cid)
        return cid

    def headers(self, token=None):
        h = {"X-Plex-Product": "Scaros", "X-Plex-Version": self.m.version, "X-Plex-Client-Identifier": self.client_id(),
             "X-Plex-Platform": "Linux", "X-Plex-Device": "Scaros OS", "X-Plex-Device-Name": "Scaros", "X-Plex-Model": "Scaros"}
        if token:
            h["X-Plex-Token"] = token
        return h

    def ready(self):
        c = self.conf
        return bool(c.get("server") and c.get("serverToken"))

    def public(self):
        c = self.conf
        link = self.link if self.link and self.link["expires"] > time.time() else None
        return {"connected": self.ready(), "server": c.get("serverName"), "error": self.error,
                "link": {"code": link["code"], "expires": int(link["expires"])} if link else None}

    def start_link(self):
        if self.m.dry:
            self.link = {"id": 0, "code": "X7K2", "expires": time.time() + 600}
            threading.Timer(6, self._dry_linked).start()
            self.m.changed()
            return True
        with self.lock:
            if self.link and self.link["expires"] > time.time() + 60:
                return True
            try:
                d = http(PLEX_TV + "/api/v2/pins", method="POST", headers=self.headers(), form={})
                self.link = {"id": d["id"], "code": d["code"], "expires": time.time() + int(d.get("expiresIn") or 900)}
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("Plex : code de connexion impossible : %s", e)
                self.error = "plex.tv ne répond pas. Vérifie la connexion internet."
                self.m.changed()
                return False
        self.error = None
        threading.Thread(target=self._poll_link, args=(self.link,), daemon=True).start()
        self.m.changed()
        return True

    def _dry_linked(self):
        self.link = None
        self.m.accounts.update("plex", server="http://192.168.1.20:32400", serverToken="demo", machineId="demo", serverName="Plex de la maison")
        self.m.notify("check", "Plex est connecté.")
        self.m.changed()
        self.m.refresh(force=True)

    def _poll_link(self, link):
        while time.time() < link["expires"] and self.link is link:
            time.sleep(2)
            try:
                d = http(f"{PLEX_TV}/api/v2/pins/{link['id']}", headers=self.headers())
            except (OSError, ValueError):
                continue
            token = (d or {}).get("authToken")
            if not token:
                continue
            self.m.accounts.update("plex", token=token)
            self.link = None
            if self.discover():
                self.m.notify("check", f"Plex est connecté : {self.conf.get('serverName') or 'serveur trouvé'}.")
            else:
                self.m.notify("alert", "Compte Plex relié, mais aucun serveur joignable depuis Scaros.", 6000)
            self.m.changed()
            self.m.refresh(force=True)
            return
        if self.link is link:
            self.link = None
            self.m.changed()

    def discover(self):
        """Trouve le serveur Plex du compte et la meilleure adresse pour le joindre (réseau local d'abord)."""
        self.discovered_at = time.monotonic()
        token = self.conf.get("token")
        if not token:
            return False
        try:
            res = http(PLEX_TV + "/api/v2/resources?includeHttps=1&includeRelay=1", headers=self.headers(token)) or []
        except (OSError, ValueError) as e:
            self.error = "plex.tv ne répond pas."
            log.warning("Plex : liste des serveurs impossible : %s", e)
            return False
        servers = [r for r in res if "server" in (r.get("provides") or "")]
        servers.sort(key=lambda r: (not r.get("owned"), not r.get("presence")))
        for r in servers:
            tok = r.get("accessToken") or token
            conns = sorted(r.get("connections") or [], key=lambda c: (bool(c.get("relay")), not c.get("local"), c.get("protocol") != "https"))
            for c in conns:
                uri = (c.get("uri") or "").rstrip("/")
                if not uri:
                    continue
                try:
                    http(uri + "/identity", headers=self.headers(tok), timeout=4)
                except (OSError, ValueError):
                    continue
                self.m.accounts.update("plex", server=uri, serverToken=tok, machineId=r.get("clientIdentifier"),
                                       serverName=r.get("name"))
                self.error = None
                log.info("Plex : serveur « %s » joint via %s", r.get("name"), uri)
                return True
        self.error = "Aucun serveur Plex joignable depuis Scaros." if servers else "Aucun serveur Plex sur ce compte."
        return False

    def set_manual(self, server, token):
        """Adresse et jeton tapés à la main (si la connexion par code ne trouve pas le serveur)."""
        server = server.rstrip("/")
        try:
            d = http(server + "/identity", headers=self.headers(token), timeout=6)
        except (OSError, ValueError) as e:
            return f"Le serveur ne répond pas ({e.__class__.__name__})."
        mc = (d or {}).get("MediaContainer") or {}
        self.m.accounts.update("plex", server=server, serverToken=token, token=token,
                               machineId=mc.get("machineIdentifier"), serverName=mc.get("friendlyName") or "Plex")
        self.error = None
        return None

    def logout(self):
        self.link = None
        self.m.accounts.clear("plex", keep=("clientId",))
        self.error = None

    def _get(self, path):
        c = self.conf
        return http(c["server"] + path, headers=self.headers(c["serverToken"]), timeout=8)

    def _metadata(self, d):
        mc = (d or {}).get("MediaContainer") or {}
        if "Metadata" in mc:
            return mc.get("Metadata") or []
        for hub in mc.get("Hub") or []:
            return hub.get("Metadata") or []
        return [] if mc else None

    def item(self, md):
        t = md.get("type")
        rk = md.get("ratingKey")
        if t == "episode":
            title = md.get("grandparentTitle") or md.get("title")
            ep = md.get("title") or ""
            sub = f"S{md.get('parentIndex', '?')} · É{md.get('index', '?')}" + (f" · {ep}" if ep else "")
            poster = md.get("grandparentThumb") or md.get("parentThumb") or md.get("thumb")
            art = md.get("grandparentArt") or md.get("art")
            kind = "Série"
        elif t == "season":
            title = md.get("parentTitle") or md.get("title")
            sub = md.get("title") or ""
            poster = md.get("thumb") or md.get("parentThumb")
            art = md.get("art") or md.get("parentArt")
            kind = "Série"
        else:
            title = md.get("title")
            sub = str(md.get("year") or "")
            poster, art = md.get("thumb"), md.get("art")
            kind = "Film" if t == "movie" else "Série"
        progress = None
        if md.get("viewOffset") and md.get("duration"):
            progress = round(min(1, md["viewOffset"] / md["duration"]), 3)
        genres = [g.get("tag") for g in (md.get("Genre") or [])[:2] if g.get("tag")]
        return {"id": f"plex:{rk}", "title": title or "Sans titre", "sub": sub, "kind": kind,
                "meta": " · ".join([kind] + genres + ([str(md["year"])] if md.get("year") and t != "movie" else [])),
                "poster": self.m.images.url("plex", poster, 360, 540), "art": self.m.images.url("plex", art, 1280, 720),
                "progress": progress, "src": "Plex",
                "open": {"plex": str(rk), "offset": int(md.get("viewOffset") or 0), "type": t or "movie"}}

    def rows(self):
        cont = None
        for path in ("/hubs/continueWatching/items?count=24", "/hubs/home/continueWatching?count=24", "/library/onDeck"):
            try:
                cont = self._metadata(self._get(path))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise
                continue
            if cont is not None:
                break
        recent = self._metadata(self._get("/library/recentlyAdded?X-Plex-Container-Start=0&X-Plex-Container-Size=40")) or []
        seen, rec = set(), []
        for md in recent:
            if md.get("type") not in ("movie", "season", "show", "episode"):
                continue  # musique, photos
            show = md.get("grandparentRatingKey") or md.get("parentRatingKey") if md.get("type") in ("season", "episode") else None
            if show and show in seen:
                continue
            seen.add(show)
            rec.append(self.item(md))
        return [self.item(md) for md in (cont or [])][:MAX_ITEMS], rec[:MAX_ITEMS]

    # ---------- lecture directe dans Plex HTPC ----------
    # Plex HTPC ne sait pas s'ouvrir sur un film précis. Mais comme toutes les applis Plex « lecteur », il
    # accepte des ordres de lecture (le « Lire sur… » de l'appli Plex du téléphone) : Scaros le trouve sur ce
    # PC (annonce GDM, sinon ses ports habituels), crée une file de lecture sur le serveur et lui demande de
    # lire le film ou l'épisode, à l'endroit où tu t'étais arrêté.
    PLAYER_PORTS = (32433, 32500)

    @staticmethod
    def local_ips():
        ips = {"127.0.0.1"}
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # aucun paquet envoyé : donne juste l'adresse de la carte réseau
            ips.add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
        return ips

    def _gdm(self, wait=1.2):
        """Lecteurs Plex qui répondent à l'annonce GDM (port 32412), sur CE PC seulement."""
        found, mine = [], self.local_ips()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            s.settimeout(0.3)
            for dest in (("127.0.0.1", 32412), ("239.0.0.250", 32412)):
                try:
                    s.sendto(b"M-SEARCH * HTTP/1.1\r\n\r\n", dest)
                except OSError:
                    pass
            end = time.monotonic() + wait
            while time.monotonic() < end:
                try:
                    data, (host, _) = s.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if host not in mine:
                    continue  # un autre appareil de la maison (télé, téléphone) : pas lui
                head = {}
                for line in data.decode(errors="replace").splitlines():
                    k, _, v = line.partition(":")
                    if v:
                        head[k.strip().lower()] = v.strip()
                if head.get("port") and head.get("resource-identifier"):
                    found.append({"host": "127.0.0.1", "port": int(head["port"]), "id": head["resource-identifier"],
                                  "product": head.get("product", ""), "name": head.get("name", "")})
        finally:
            s.close()
        return found

    def _probe(self):
        """Repli : les ports où les lecteurs Plex attendent les ordres (Plex HTPC : 32433)."""
        for port in self.PLAYER_PORTS:
            try:
                raw, _ = http(f"http://127.0.0.1:{port}/resources", headers=self.headers(), timeout=2, raw=True)
                root = ET.fromstring(raw)
            except (OSError, ValueError, ET.ParseError):
                continue
            pl = root.find(".//Player")
            if pl is not None and pl.get("machineIdentifier"):
                return {"host": "127.0.0.1", "port": port, "id": pl.get("machineIdentifier"), "product": pl.get("product", ""),
                        "name": pl.get("title", "")}
        return None

    def find_player(self):
        players = self._gdm()
        players.sort(key=lambda p: "htpc" not in p["product"].lower())
        return players[0] if players else self._probe()

    def play_on(self, player, rating_key, offset=0, mtype="movie", command_id=1):
        """Demande au lecteur de lire (film, épisode) ou d'afficher la fiche (série, saison)."""
        c = self.conf
        mid, url = c.get("machineId") or "", urllib.parse.urlparse(c["server"])
        port = url.port or (443 if url.scheme == "https" else 80)
        key = f"/library/metadata/{rating_key}"
        common = {"key": key, "machineIdentifier": mid, "address": url.hostname, "port": port, "protocol": url.scheme,
                  "token": c["serverToken"], "commandID": command_id}
        headers = {**self.headers(c["serverToken"]), "X-Plex-Target-Client-Identifier": player["id"]}
        base = f"http://{player['host']}:{player['port']}/player"
        if mtype in ("movie", "episode", "clip", "video"):
            uri = f"server://{mid}/com.plexapp.plugins.library{key}"
            q = http(c["server"] + "/playQueues?" + urllib.parse.urlencode({"type": "video", "uri": uri, "shuffle": 0, "repeat": 0,
                                                                            "continuous": 1 if mtype == "episode" else 0, "own": 1}),
                     method="POST", headers=self.headers(c["serverToken"]), form={}, timeout=8) or {}
            pq = (q.get("MediaContainer") or {}).get("playQueueID")
            params = {**common, "offset": int(offset or 0), "type": "video", "providerIdentifier": "com.plexapp.plugins.library"}
            if pq:
                params["containerKey"] = f"/playQueues/{pq}?own=1&window=200"
            http(f"{base}/playback/playMedia?" + urllib.parse.urlencode(params), headers=headers, timeout=6, raw=True)
        else:
            http(f"{base}/mirror/details?" + urllib.parse.urlencode({**common, "type": "video"}), headers=headers, timeout=6, raw=True)
        return True

    def web_url(self, rating_key):
        c = self.conf
        key = urllib.parse.quote(f"/library/metadata/{rating_key}", safe="")
        return f"{c['server']}/web/index.html#!/server/{c.get('machineId') or ''}/details?key={key}"


# ---------------------------------------------------------------------------
class Overseerr:
    def __init__(self, media):
        self.m = media
        self.error = None
        self.details = {}           # (type, tmdb) -> (moment, fiche)

    @property
    def conf(self):
        return self.m.accounts.get("overseerr")

    def ready(self):
        c = self.conf
        return bool(c.get("url") and c.get("key"))

    def public(self):
        c = self.conf
        return {"connected": self.ready(), "url": c.get("url"), "user": c.get("user"), "error": self.error}

    def api(self, path, method="GET", data=None, conf=None, timeout=10):
        c = conf or self.conf
        return http(c["url"].rstrip("/") + "/api/v1" + path, method=method, data=data,
                    headers={"X-Api-Key": c["key"]}, timeout=timeout)

    def connect(self, url, key):
        url = url.strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+", url):
            url = "http://" + url
        conf = {"url": url, "key": key.strip()}
        if self.m.dry:
            self.m.accounts.update("overseerr", url=url, key=conf["key"], user="Sam")
            return None
        try:
            me = self.api("/auth/me", conf=conf, timeout=8) or {}
        except urllib.error.HTTPError as e:
            return "Clé API refusée." if e.code in (401, 403) else f"Overseerr répond « erreur {e.code} »."
        except (OSError, ValueError):
            return "Overseerr ne répond pas à cette adresse."
        self.m.accounts.update("overseerr", url=url, key=conf["key"], user=me.get("displayName") or me.get("username") or me.get("email"))
        self.error = None
        return None

    def logout(self):
        self.m.accounts.clear("overseerr")
        self.error = None

    def _poster(self, path, w=342):
        return self.m.images.url("tmdb", f"/w{w}{path}") if path else None

    def item(self, r, media_type=None):
        mtype = media_type or r.get("mediaType")
        mi = r.get("mediaInfo") or {}
        status = mi.get("status") or 1
        title = r.get("title") or r.get("name") or "Sans titre"
        year = year_of(r.get("releaseDate") or r.get("firstAirDate"))
        kind = "Film" if mtype == "movie" else "Série"
        tmdb = r.get("id") or mi.get("tmdbId")
        open_ = {"request": {"type": mtype, "id": tmdb, "title": title}}
        badge = None
        if status in (4, 5):
            badge = "Disponible" if status == 5 else "En partie"
            if mi.get("ratingKey") and self.m.plex.ready():
                open_ = {"plex": str(mi["ratingKey"]), "type": "movie" if mtype == "movie" else "show"}
            else:
                open_ = {"info": f"« {title} » est déjà dans ta médiathèque."}
        elif status in (2, 3):
            badge = "Demandé"
            open_ = {"info": f"« {title} » est déjà demandé."}
        return {"id": f"tmdb:{mtype}:{tmdb}", "title": title, "sub": year, "kind": kind,
                "meta": " · ".join(x for x in (kind, year) if x), "overview": (r.get("overview") or "")[:400],
                "poster": self._poster(r.get("posterPath")), "art": self._poster(r.get("backdropPath"), 1280),
                "badge": badge, "src": "Overseerr", "open": open_}

    def trending(self):
        d = self.api("/discover/trending?page=1&language=fr") or {}
        items = [self.item(r) for r in d.get("results") or [] if r.get("mediaType") in ("movie", "tv")]
        return items[:MAX_ITEMS]

    def detail(self, mtype, tmdb):
        key = (mtype, tmdb)
        hit = self.details.get(key)
        if hit and time.time() - hit[0] < 86400:
            return hit[1]
        d = self.api(f"/{'movie' if mtype == 'movie' else 'tv'}/{tmdb}?language=fr") or {}
        self.details[key] = (time.time(), d)
        return d

    def requests(self):
        d = self.api("/request?take=20&skip=0&sort=added&filter=all") or {}
        out = []
        for req in d.get("results") or []:
            media = req.get("media") or {}
            mtype, tmdb = media.get("mediaType") or req.get("type"), media.get("tmdbId")
            if not tmdb:
                continue
            try:
                info = self.detail(mtype, tmdb)
            except (OSError, ValueError):
                info = {}
            info = {**info, "mediaInfo": media, "id": tmdb}
            it = self.item(info, mtype)
            rs, ms = req.get("status"), media.get("status")
            if ms == 5:
                label = "Disponible"
            elif ms == 4:
                label = "Disponible en partie"
            elif rs == 3:
                label = "Refusée"
            elif rs == 4:
                label = "Échec"
            elif rs == 1:
                label = "En attente de validation"
            else:
                dl = [x for x in (media.get("downloadStatus") or []) + (media.get("downloadStatus4k") or []) if x.get("size")]
                if dl:
                    size, left = sum(x["size"] for x in dl), sum(x.get("sizeLeft") or 0 for x in dl)
                    pct = max(0, min(100, round(100 * (1 - left / size)))) if size else 0
                    label = f"Téléchargement · {pct} %"
                    it["progress"] = pct / 100
                else:
                    label = "Demandé"
            it["badge"] = label
            who = (req.get("requestedBy") or {}).get("displayName")
            it["sub"] = label if not who else f"{label} · {who}"
            if ms not in (4, 5):
                it["open"] = {"info": f"« {it['title']} » : {label.lower()}."}
            out.append(it)
        return out[:MAX_ITEMS]

    def request(self, mtype, tmdb):
        if mtype not in ("movie", "tv"):
            return "Type inconnu."
        body = {"mediaType": mtype, "mediaId": int(tmdb)}
        if mtype == "tv":
            try:
                info = self.detail("tv", int(tmdb))
                seasons = [s["seasonNumber"] for s in info.get("seasons") or [] if s.get("seasonNumber")]
            except (OSError, ValueError, KeyError):
                seasons = []
            body["seasons"] = seasons or "all"
        if self.m.dry:
            return None
        try:
            self.api("/request", method="POST", data=body)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("message")
            except ValueError:
                msg = None
            if e.code == 409:
                return "Déjà demandé."
            return msg or f"Overseerr a refusé la demande (erreur {e.code})."
        except (OSError, ValueError):
            return "Overseerr ne répond pas."
        return None


# ---------------------------------------------------------------------------
class Spotify:
    def __init__(self, media):
        self.m = media
        self.pending = {}           # state -> (vérificateur PKCE, moment)
        self.error = None
        self.web_cache = (0.0, None)
        self.local_cache = (0.0, None)
        self.lock = threading.Lock()

    @property
    def conf(self):
        return self.m.accounts.get("spotify")

    @property
    def redirect(self):
        return f"http://127.0.0.1:{self.m.port}/api/spotify/callback"

    def ready(self):
        c = self.conf
        return bool(c.get("clientId") and c.get("refresh"))

    def public(self):
        c = self.conf
        return {"configured": bool(c.get("clientId")), "connected": self.ready(), "user": c.get("user"),
                "redirect": self.redirect, "error": self.error, "connect": self.m.connect_state()}

    def set_client(self, client_id):
        client_id = client_id.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{32}", client_id):
            return "Le Client ID fait 32 caractères (chiffres et lettres de a à f)."
        if client_id != self.conf.get("clientId"):
            self.m.accounts.clear("spotify")
            self.m.accounts.update("spotify", clientId=client_id)
        return None

    def auth_url(self):
        cid = self.conf.get("clientId")
        if not cid:
            return None
        verifier = b64url(secrets.token_bytes(48))
        state = b64url(secrets.token_bytes(12))
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v[1] < 900}
        self.pending[state] = (verifier, now)
        challenge = b64url(hashlib.sha256(verifier.encode()).digest())
        return SPOTIFY_ACCOUNTS + "/authorize?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": cid, "redirect_uri": self.redirect, "scope": SPOTIFY_SCOPES,
            "state": state, "code_challenge_method": "S256", "code_challenge": challenge})

    def finish(self, code, state):
        """Fin de la connexion : échange le code contre les jetons (PKCE, sans secret)."""
        entry = self.pending.pop(state or "", None)
        if not entry or not code:
            return "Lien expiré : recommence la connexion."
        cid = self.conf.get("clientId")
        if self.m.dry:
            self.m.accounts.update("spotify", refresh="demo", access="demo", exp=time.time() + 3600, user="Sam")
            return None
        try:
            tok = http(SPOTIFY_ACCOUNTS + "/api/token", method="POST", form={
                "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect,
                "client_id": cid, "code_verifier": entry[0]})
        except urllib.error.HTTPError as e:
            log.warning("Spotify : échange du code refusé (%s)", e.code)
            return "Spotify a refusé la connexion : vérifie l'adresse de redirection dans ton appli Spotify."
        except (OSError, ValueError):
            return "Spotify ne répond pas."
        self._store(tok)
        try:
            me = http(SPOTIFY_API + "/me", headers={"Authorization": f"Bearer {tok['access_token']}"}) or {}
            self.m.accounts.update("spotify", user=me.get("display_name") or me.get("id"))
        except (OSError, ValueError, KeyError):
            pass
        self.error = None
        return None

    def finish_url(self, url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url.strip()).query)
        if q.get("error"):
            return "Connexion annulée sur Spotify."
        return self.finish(q.get("code", [""])[0], q.get("state", [""])[0])

    def _store(self, tok):
        self.m.accounts.update("spotify", access=tok["access_token"], exp=time.time() + int(tok.get("expires_in") or 3600),
                               refresh=tok.get("refresh_token") or self.conf.get("refresh"))

    def token(self):
        c = self.conf
        if not c.get("refresh"):
            return None
        if c.get("access") and c.get("exp", 0) > time.time() + 60:
            return c["access"]
        with self.lock:
            c = self.conf
            if c.get("access") and c.get("exp", 0) > time.time() + 60:
                return c["access"]
            try:
                tok = http(SPOTIFY_ACCOUNTS + "/api/token", method="POST", form={
                    "grant_type": "refresh_token", "refresh_token": c["refresh"], "client_id": c["clientId"]})
            except urllib.error.HTTPError as e:
                if e.code == 400:  # invalid_grant : accès retiré, il faut se reconnecter
                    self.m.accounts.update("spotify", refresh=None, access=None)
                    self.error = "La connexion à Spotify a expiré : reconnecte-toi."
                    self.m.changed()
                return None
            except (OSError, ValueError):
                return None
            self._store(tok)
            return tok["access_token"]

    def logout(self):
        cid = self.conf.get("clientId")
        self.m.accounts.clear("spotify")
        if cid:
            self.m.accounts.update("spotify", clientId=cid)
        self.web_cache = (0.0, None)

    def web(self):
        """Ce que joue le compte Spotify, sur n'importe quel appareil (API Web)."""
        at, val = self.web_cache
        if time.monotonic() - at < 2.5:
            return val
        val = None
        tok = self.token() if self.ready() else None
        if tok:
            try:
                d = http(SPOTIFY_API + "/me/player?additional_types=episode", headers={"Authorization": f"Bearer {tok}"}, timeout=6)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self.m.accounts.update("spotify", exp=0)
                d = None
            except (OSError, ValueError):
                d = None
            item = (d or {}).get("item")
            if item:
                if item.get("type") == "episode":
                    artist = (item.get("show") or {}).get("name") or ""
                    images = item.get("images") or (item.get("show") or {}).get("images") or []
                else:
                    artist = ", ".join(a.get("name", "") for a in item.get("artists") or [])
                    images = (item.get("album") or {}).get("images") or []
                img = images[0]["url"] if images else None
                device = (d.get("device") or {}).get("name")
                val = {"source": "spotify", "via": "web", "title": item.get("name") or "", "artist": artist,
                       "album": (item.get("album") or {}).get("name") or "",
                       "cover": self.m.images.url("url", img) if img else None,
                       "playing": bool(d.get("is_playing")), "position": int(d.get("progress_ms") or 0),
                       "duration": int(item.get("duration_ms") or 0), "device": device, "controls": True}
        self.web_cache = (time.monotonic(), val)
        return val

    def local(self):
        """Musique jouée par Scaros lui-même (spotifyd, via MPRIS et playerctl)."""
        at, val = self.local_cache
        if time.monotonic() - at < 1.0:
            return val
        val = None
        if not self.m.dry and shutil.which("playerctl"):
            fmt = "{{status}}\t{{xesam:title}}\t{{xesam:artist}}\t{{xesam:album}}\t{{mpris:artUrl}}\t{{mpris:length}}\t{{position}}"
            try:
                out = subprocess.run(["playerctl", "-p", "spotifyd", "metadata", "--format", fmt],
                                     capture_output=True, text=True, timeout=3).stdout.rstrip("\n").split("\t")
            except (OSError, subprocess.TimeoutExpired):
                out = []
            if len(out) == 7 and out[0] in ("Playing", "Paused") and out[1]:
                num = lambda s: int(s) // 1000 if s.strip().isdigit() else 0
                art = out[4] if out[4].startswith("https://") else None
                val = {"source": "spotify", "via": "local", "title": out[1], "artist": out[2], "album": out[3],
                       "cover": self.m.images.url("url", art) if art else None, "playing": out[0] == "Playing",
                       "position": num(out[6]), "duration": num(out[5]), "device": "Scaros", "controls": True}
        self.local_cache = (time.monotonic(), val)
        return val

    def control(self, via, action):
        if via == "local":
            cmd = {"toggle": "play-pause", "play": "play", "pause": "pause", "next": "next", "prev": "previous"}.get(action)
            if not cmd:
                return False
            try:
                subprocess.run(["playerctl", "-p", "spotifyd", cmd], capture_output=True, timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                return False
            self.local_cache = (0.0, None)
            return True
        tok = self.token()
        if not tok:
            return False
        if action == "toggle":
            cur = self.web()
            action = "pause" if cur and cur["playing"] else "play"
        method, path = {"play": ("PUT", "/me/player/play"), "pause": ("PUT", "/me/player/pause"),
                        "next": ("POST", "/me/player/next"), "prev": ("POST", "/me/player/previous")}.get(action, (None, None))
        if not method:
            return False
        try:
            req = urllib.request.Request(SPOTIFY_API + path, data=b"", method=method,
                                         headers={"Authorization": f"Bearer {tok}", "User-Agent": "Scaros-OS"})
            urllib.request.urlopen(req, timeout=6).close()
        except urllib.error.HTTPError as e:
            log.info("Spotify : commande %s refusée (%s)", action, e.code)  # 403 : compte non Premium
            return False
        except OSError:
            return False
        self.web_cache = (0.0, None)
        return True


# ---------------------------------------------------------------------------
class Sports:
    def __init__(self, media):
        self.m = media
        self.ticker = []
        self.board = []             # onglet « Matchs » : en direct, à venir (7 jours), résultats récents
        self.days = {}              # (ligue, jour) -> (moment, matchs)
        self.cal_cache = {}         # ligue -> (moment, (jours de match à venir, matchs de la journée en cours))
        self.summaries = {}         # (ligue, match) -> (moment, fiche)
        self.teams_cache = {}       # ligue -> (moment, équipes)
        self.next_cache = {}        # ligue:équipe -> (moment, prochain match)
        self.live = False
        self.lock = threading.Lock()

    def leagues(self):
        prefs = self.m.prefs()
        ls = prefs.get("sportLeagues")
        return [x for x in (ls if isinstance(ls, list) else DEFAULT_LEAGUES) if x in LEAGUES]

    def favorites(self):
        fav = self.m.prefs().get("sportTeams")
        return [x for x in fav if isinstance(x, str)] if isinstance(fav, list) else []

    @staticmethod
    def path(league):
        return f"{LEAGUES[league][1]}/{league}"

    def team(self, t, league):
        logo = t.get("logo") or ((t.get("logos") or [{}])[0].get("href"))
        return {"id": f"{league}:{t.get('id')}", "name": t.get("shortDisplayName") or t.get("displayName") or t.get("name") or "?",
                "abbr": t.get("abbreviation") or "", "logo": self.m.images.url("url", logo, 96, 96) if logo else None}

    def match(self, e, league):
        comp = (e.get("competitions") or [{}])[0]
        st = (comp.get("status") or e.get("status") or {})
        typ = st.get("type") or {}
        side = {}
        for c in comp.get("competitors") or []:
            score = c.get("score")
            if isinstance(score, dict):
                score = score.get("displayValue") or score.get("value")
            side[c.get("homeAway")] = {**self.team(c.get("team") or {}, league), "score": None if score in (None, "") else str(score).split(".")[0]}
        if "home" not in side or "away" not in side:
            return None
        state = typ.get("state") or "pre"
        name = typ.get("name") or ""
        if state == "in":
            clock = "MT" if "HALFTIME" in name else (st.get("displayClock") or typ.get("shortDetail") or "En cours")
        elif state == "post":
            clock = "Reporté" if "POSTPONED" in name else "Terminé"
        else:
            clock = "Reporté" if "POSTPONED" in name else ""
        return {"id": str(e.get("id")), "league": league, "leagueName": LEAGUES[league][0], "state": state,
                "clock": clock, "start": e.get("date"), "home": side["home"], "away": side["away"]}

    def scoreboard(self, league, day=None):
        q = f"?dates={day:%Y%m%d}" if day else ""
        d = http(f"{ESPN}/{self.path(league)}/scoreboard{q}", timeout=10) or {}
        return [m for m in (self.match(e, league) for e in d.get("events") or []) if m]

    def calendar(self, league):
        """Les jours de match de la saison (Ligue 1, Premier League, NBA…) et la journée en cours selon ESPN."""
        hit = self.cal_cache.get(league)
        if hit and time.time() - hit[0] < 12 * 3600:
            return hit[1]
        d = http(f"{ESPN}/{self.path(league)}/scoreboard", timeout=10) or {}
        lg = (d.get("leagues") or [{}])[0]
        days = []
        if lg.get("calendarType") == "day":
            for x in lg.get("calendar") or []:
                try:
                    days.append(dt.date.fromisoformat(str(x)[:10]))
                except ValueError:
                    pass
        current = [m for m in (self.match(e, league) for e in d.get("events") or []) if m]
        self.cal_cache[league] = (time.time(), (days, current))
        return days, current

    def days_to_fetch(self, league, today):
        """Hier, aujourd'hui, puis les 3 prochains jours de match (dans les 3 semaines) ; sans calendrier : les 3 jours suivants."""
        days = {today - dt.timedelta(days=1), today}
        try:
            cal, _ = self.calendar(league)
        except (OSError, ValueError):
            cal = []
        nxt = [x for x in sorted(set(cal)) if today < x <= today + dt.timedelta(days=21)][:3]
        days.update(nxt or [today + dt.timedelta(days=i) for i in (1, 2, 3)])
        return sorted(days)

    def day(self, league, day, today):
        """Matchs d'un jour, gardés plus ou moins longtemps selon qu'ils peuvent encore changer."""
        key = (league, day.isoformat())
        hit = self.days.get(key)
        ttl = (55 if self.live else 300) if day == today else 1800 if day > today else 6 * 3600
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        ms = self.scoreboard(league, day)
        self.days[key] = (time.time(), ms)
        if len(self.days) > 200:  # les vieux jours ne servent plus
            for k in sorted(self.days, key=lambda k: self.days[k][0])[:80]:
                self.days.pop(k, None)
        return ms

    @staticmethod
    def event_kind(k):
        t = ((k.get("type") or {}).get("type") or "").lower()
        if k.get("scoringPlay") or t.startswith("goal") or "penalty---scored" in t or "own-goal" in t:
            return "goal"
        if "yellow" in t and "red" not in t:
            return "yellow"
        if "red" in t:
            return "red"
        if "substitution" in t:
            return "sub"
        if t in ("halftime", "end-regular-time"):
            return "half"
        return None

    def summary(self, league, event_id):
        """Fiche d'un match : score, temps forts (buts, cartons, remplacements), statistiques."""
        if league not in LEAGUES or not re.fullmatch(r"[A-Za-z0-9]{1,20}", event_id or ""):
            raise ValueError("match inconnu")
        key = (league, event_id)
        hit = self.summaries.get(key)
        if hit and time.time() - hit[0] < (25 if hit[1].get("state") == "in" else 600):
            return hit[1]
        if self.m.dry:
            out = self._demo_summary(event_id)
        else:
            d = http(f"{ESPN}/{self.path(league)}/summary?event={event_id}", timeout=10) or {}
            comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
            ev = {"id": event_id, "date": comp.get("date"), "competitions": [comp], "status": comp.get("status")}
            m = self.match(ev, league) or {}
            sides = {}
            for c in comp.get("competitors") or []:
                sides[str((c.get("team") or {}).get("id"))] = c.get("homeAway")
            events = []
            for k in d.get("keyEvents") or []:
                kind = self.event_kind(k)
                if not kind:
                    continue
                names = [((p.get("athlete") or {}).get("displayName") or "") for p in k.get("participants") or []]
                t = ((k.get("type") or {}).get("type") or "")
                if kind == "goal":
                    who = names[0] if names else ""
                    who += " (pén.)" if "penalty" in t else " (csc)" if "own" in t else ""
                elif kind == "sub":
                    who = f"{names[0]} ← {names[1]}" if len(names) > 1 else (names[0] if names else "")
                elif kind == "half":
                    who = "Mi-temps" if t == "halftime" else "Fin du temps réglementaire"
                else:
                    who = names[0] if names else ""
                events.append({"clock": (k.get("clock") or {}).get("displayValue") or "", "kind": kind, "text": who,
                               "side": sides.get(str((k.get("team") or {}).get("id")))})
            stats = []
            teams = (d.get("boxscore") or {}).get("teams") or []
            if len(teams) == 2:
                by = {}
                for tm in teams:
                    side = sides.get(str((tm.get("team") or {}).get("id")))
                    for st in tm.get("statistics") or []:
                        by.setdefault(st.get("label") or st.get("name"), {})[side] = st.get("displayValue")
                soccer = LEAGUES[league][1] == "soccer"
                for label, v in by.items():
                    if "home" in v and "away" in v and (not soccer or label in STAT_FR):
                        stats.append({"label": STAT_FR.get(label, label), "home": v["home"], "away": v["away"]})
                order = list(dict.fromkeys(STAT_FR.values()))
                stats.sort(key=lambda x: order.index(x["label"]) if x["label"] in order else 99)
            venue = ((d.get("gameInfo") or {}).get("venue") or {}).get("fullName")
            out = {**m, "events": events, "stats": stats[:9], "venue": venue,
                   "detail": ((comp.get("status") or {}).get("type") or {}).get("detail")}
        self.summaries[key] = (time.time(), out)
        return out

    def teams(self, league):
        if league not in LEAGUES:
            return []
        hit = self.teams_cache.get(league)
        if hit and time.time() - hit[0] < 86400:
            return hit[1]
        if self.m.dry:
            names = {"fra.1": ["Lyon", "Marseille", "Paris SG", "Lens", "Monaco", "Nice", "Rennes", "Nantes"],
                     "eng.1": ["Arsenal", "Liverpool", "Man City", "Chelsea"], "uefa.champions": ["Real Madrid", "Barcelone", "Bayern"]}.get(league, ["Équipe A", "Équipe B"])
            out = [{"id": f"{league}:{i}", "name": n, "abbr": n[:3].upper(), "logo": None} for i, n in enumerate(names)]
        else:
            d = http(f"{ESPN}/{self.path(league)}/teams", timeout=10) or {}
            teams = []
            for sp in d.get("sports") or []:
                for lg in sp.get("leagues") or []:
                    teams += [t.get("team") or {} for t in lg.get("teams") or []]
            out = sorted((self.team(t, league) for t in teams if t.get("id")), key=lambda t: t["name"])
        self.teams_cache[league] = (time.time(), out)
        return out

    def next_match(self, fav):
        league, _, tid = fav.partition(":")
        if league not in LEAGUES or not tid:
            return None
        hit = self.next_cache.get(fav)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        best = None
        try:
            d = http(f"{ESPN}/{self.path(league)}/teams/{tid}/schedule?fixture=true", timeout=10) or {}
            now = dt.datetime.now(dt.timezone.utc)
            for e in d.get("events") or []:
                when = self._when(e.get("date"))
                if when and when > now:
                    m = self.match(e, league)
                    if m and m["state"] == "pre":
                        best = {**m, "next": True}
                        break
        except (OSError, ValueError):
            pass
        self.next_cache[fav] = (time.time(), best)
        return best

    @staticmethod
    def _when(s):
        try:
            return dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except ValueError:
            return None

    def refresh(self):
        leagues, favs = self.leagues(), self.favorites()
        if self.m.dry:
            ticker = self._demo(favs)
            board = self._demo_board()
        else:
            now = dt.datetime.now(dt.timezone.utc).astimezone()
            today = now.date()
            seen = {}
            for lg in leagues:
                try:
                    for day in self.days_to_fetch(lg, today):
                        for m in self.day(lg, day, today):
                            seen[m["id"]] = m
                    if not self.calendar(lg)[0]:  # pas de calendrier (Ligue des champions) : la journée en cours d'ESPN
                        for m in self.calendar(lg)[1]:
                            w = self._when(m["start"])
                            if m["state"] != "in" and w and w.astimezone().date() != today:
                                seen.setdefault(m["id"], m)
                except (OSError, ValueError) as e:
                    log.info("scores %s indisponibles : %s", lg, e)
            matches = list(seen.values())

            def recent(m):  # en direct, ou aujourd'hui (heure de Scaros), ou fini il y a moins de 6 h
                w = self._when(m["start"])
                if m["state"] == "in":
                    return True
                if not w:
                    return False
                lw = w.astimezone()
                return lw.date() == today or (m["state"] == "post" and now - lw < dt.timedelta(hours=8))

            fav_set = set(favs)
            mine = [m for m in matches if (m["home"]["id"] in fav_set or m["away"]["id"] in fav_set) and recent(m)]
            others = [m for m in matches if m not in mine and m["state"] == "in"]
            order = {"in": 0, "pre": 1, "post": 2}
            mine.sort(key=lambda m: (order.get(m["state"], 3), m["start"] or ""))
            others.sort(key=lambda m: m["start"] or "")
            for m in mine:
                m["fav"] = True
            playing = {t for m in mine for t in (m["home"]["id"], m["away"]["id"])}
            nexts = []
            for f in favs:
                if f not in playing and len(nexts) < 3:
                    n = self.next_match(f)
                    if n and n["id"] not in {x["id"] for x in nexts}:
                        nexts.append({**n, "fav": True})
            ticker = mine + others[:6] + nexts
            # onglet « Matchs » : le direct, puis les 7 prochains jours, puis les résultats des 36 dernières heures
            for m in matches:
                m["fav"] = m["home"]["id"] in fav_set or m["away"]["id"] in fav_set
            live = sorted((m for m in matches if m["state"] == "in"), key=lambda m: (not m["fav"], m["start"] or ""))
            soon = sorted((m for m in matches if m["state"] == "pre"), key=lambda m: (not m["fav"], m["start"] or ""))
            past = [m for m in matches if m["state"] == "post" and (w := self._when(m["start"])) and now - w.astimezone() < dt.timedelta(hours=36)]
            past.sort(key=lambda m: m["start"] or "", reverse=True)
            past.sort(key=lambda m: not m["fav"])
            board = (live + soon + past)[:30]
            ids = {m["id"] for m in board}
            board += [n for n in nexts if n["id"] not in ids]
        with self.lock:
            changed = ticker != self.ticker or board != self.board
            self.ticker, self.board = ticker, board
            self.live = any(m["state"] == "in" for m in ticker + board)
        if changed:
            self.m.bus.emit("sport", ticker=ticker, board=board)
        return ticker

    def _demo_board(self):
        t = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        team = lambda i, n, a: {"id": f"fra.1:{i}", "name": n, "abbr": a, "logo": None}
        mk = lambda i, lg, st, clock, h, a, hs, as_, start, fav=False: {"id": f"b{i}", "league": lg, "leagueName": LEAGUES[lg][0], "state": st,
              "clock": clock, "start": start.isoformat(), "home": {**h, "score": hs}, "away": {**a, "score": as_}, "fav": fav}
        return self._demo([]) + [
            mk(3, "fra.1", "pre", "", team(4, "Paris SG", "PSG"), team(5, "Lens", "RCL"), None, None, t + dt.timedelta(days=1, hours=3)),
            mk(4, "uefa.champions", "pre", "", team(6, "Real Madrid", "RMA"), team(7, "PSV", "PSV"), None, None, t + dt.timedelta(days=5)),
            mk(5, "fra.1", "post", "Terminé", team(8, "Monaco", "ASM"), team(9, "Nice", "OGCN"), "3", "2", t - dt.timedelta(hours=20)),
        ]

    def _demo_summary(self, event_id):
        base = next((m for m in self._demo([]) + self._demo_board() if m["id"] == event_id), self._demo([])[0])
        if base["state"] == "pre":
            return {**base, "events": [], "stats": [], "venue": "Parc des Princes", "detail": ""}
        return {**base, "venue": "Groupama Stadium", "detail": base["clock"],
                "events": [{"clock": "12'", "kind": "goal", "text": "A. Lacazette", "side": "home"},
                           {"clock": "28'", "kind": "yellow", "text": "L. Balerdi", "side": "away"},
                           {"clock": "41'", "kind": "goal", "text": "M. Greenwood (pén.)", "side": "away"},
                           {"clock": "45'+2'", "kind": "half", "text": "Mi-temps", "side": None},
                           {"clock": "58'", "kind": "sub", "text": "E. Mikautadze ← A. Lacazette", "side": "home"},
                           {"clock": "63'", "kind": "goal", "text": "R. Cherki", "side": "home"}],
                "stats": [{"label": "Possession", "home": "54.2", "away": "45.8"}, {"label": "Tirs", "home": "14", "away": "9"},
                          {"label": "Tirs cadrés", "home": "6", "away": "3"}, {"label": "Corners", "home": "5", "away": "4"},
                          {"label": "Fautes", "home": "11", "away": "13"}, {"label": "Cartons jaunes", "home": "1", "away": "2"}]}

    def _demo(self, favs):
        lyon = {"id": "fra.1:0", "name": "Lyon", "abbr": "OL", "logo": None}
        om = {"id": "fra.1:1", "name": "Marseille", "abbr": "OM", "logo": None}
        ars = {"id": "eng.1:0", "name": "Arsenal", "abbr": "ARS", "logo": None}
        liv = {"id": "eng.1:1", "name": "Liverpool", "abbr": "LIV", "logo": None}
        start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        return [
            {"id": "d1", "league": "fra.1", "leagueName": "Ligue 1", "state": "in", "clock": "67'", "start": start.isoformat(),
             "home": {**lyon, "score": "2"}, "away": {**om, "score": "1"}, "fav": True},
            {"id": "d2", "league": "eng.1", "leagueName": "Premier League", "state": "in", "clock": "MT", "start": start.isoformat(),
             "home": {**ars, "score": "0"}, "away": {**liv, "score": "0"}},
        ]


# ---------------------------------------------------------------------------
class Media:
    """Tout ce qui vient d'internet pour l'accueil, la veille et les comptes."""

    def __init__(self, bus, state_dir, cache_dir, version, port, dry, prefs, notify, launch_url):
        self.bus = bus
        self.dir = state_dir
        self.version = version
        self.port = port
        self.dry = dry
        self.prefs = prefs              # () -> réglages de l'interface
        self.notify = notify            # (icône, texte[, ms]) -> notification à l'écran
        self.launch_url = launch_url    # (id, nom, adresse, profil) -> ouvre une page en plein écran
        self.accounts = Accounts(state_dir / "accounts.json")
        self.images = Images(self, cache_dir / "img")
        self.plex = Plex(self)
        self.overseerr = Overseerr(self)
        self.spotify = Spotify(self)
        self.sports = Sports(self)
        self.connect_state = lambda: None   # remplacé par scaros.py (spotifyd)
        self.open_plex = None               # remplacé par scaros.py (lecture directe dans Plex HTPC)
        self.airplay_np = lambda: None      # remplacé par scaros.py (pochette UxPlay)
        self.rows = self._load_rows()
        self.status = {}                    # onglet -> ok | off | error | loading
        self.refreshed = 0.0
        self.refresh_lock = threading.Lock()
        self.wake = threading.Event()
        self.np_cache = (0.0, None)
        if dry:
            self._demo_accounts()

    # ---------- état ----------
    def public(self):
        return {"plex": self.plex.public(), "overseerr": self.overseerr.public(), "spotify": self.spotify.public()}

    def changed(self):
        self.bus.emit("accounts", accounts=self.public())

    def state(self):
        return {"rows": self.rows, "status": self.status, "accounts": self.public(), "sport": self.sports.ticker, "board": self.sports.board}

    def image_source(self, kind, ref, w, h):
        if kind == "plex":
            c = self.plex.conf
            q = urllib.parse.urlencode({"width": w, "height": h, "minSize": 1, "upscale": 1, "url": ref})
            return f"{c['server']}/photo/:/transcode?{q}", self.plex.headers(c["serverToken"])
        if kind == "tmdb":
            return TMDB_IMG + ref, {}
        if kind == "url":
            host = urllib.parse.urlparse(ref).hostname or ""
            if ref.startswith("https://") and (host.endswith(".scdn.co") or host.endswith(".spotifycdn.com")
                                               or host.endswith(".espncdn.com") or host in ("image.tmdb.org", "dl.flathub.org")):
                return ref, {}
        raise KeyError(kind)

    def _load_rows(self):
        try:
            d = json.loads((self.dir / "media.json").read_text())
            rows = {k: v for k, v in d.items() if k in TABS and isinstance(v, list)}
        except (OSError, ValueError, AttributeError):
            rows = {}
        for items in rows.values():  # les images gardées sur le disque restent servies
            for it in items:
                for f in ("poster", "art"):
                    if isinstance(it.get(f), str) and not it[f].startswith("/api/img/"):
                        it[f] = None
        return rows

    def _save_rows(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "media.json").write_text(json.dumps(self.rows, ensure_ascii=False))
        except OSError:
            pass

    # ---------- rangées de l'accueil ----------
    def refresh(self, force=False):
        """Recharge les quatre rangées (au plus une fois par minute, sauf si force)."""
        if not force and time.monotonic() - self.refreshed < 60:
            return False
        if not self.refresh_lock.acquire(blocking=False):
            return False
        try:
            self.refreshed = time.monotonic()
            rows, status = dict(self.rows), {}
            if self.dry:
                rows, status = self._demo_rows()
            else:
                if self.plex.ready():
                    try:
                        rows["continue"], rows["recent"] = self.plex.rows()
                        status["continue"] = status["recent"] = "ok"
                        self.plex.error = None
                    except urllib.error.HTTPError as e:
                        status["continue"] = status["recent"] = "error"
                        self.plex.error = "Accès refusé par le serveur Plex : reconnecte Plex." if e.code in (401, 403) else f"Le serveur Plex répond « erreur {e.code} »."
                    except (OSError, ValueError, KeyError, TypeError) as e:
                        status["continue"] = status["recent"] = "error"
                        self.plex.error = "Le serveur Plex ne répond pas."
                        log.info("Plex injoignable : %s", e)
                        if time.monotonic() - self.plex.discovered_at > 600:  # adresse changée ?
                            threading.Thread(target=self.plex.discover, daemon=True).start()
                else:
                    status["continue"] = status["recent"] = "off"
                    rows.pop("continue", None)
                    rows.pop("recent", None)
                if self.overseerr.ready():
                    for tab, fn in (("trending", self.overseerr.trending), ("requests", self.overseerr.requests)):
                        try:
                            rows[tab] = fn()
                            status[tab] = "ok"
                            self.overseerr.error = None
                        except urllib.error.HTTPError as e:
                            status[tab] = "error"
                            self.overseerr.error = "Clé API refusée par Overseerr." if e.code in (401, 403) else f"Overseerr répond « erreur {e.code} »."
                        except (OSError, ValueError, KeyError, TypeError) as e:
                            status[tab] = "error"
                            self.overseerr.error = "Overseerr ne répond pas."
                            log.info("Overseerr injoignable : %s", e)
                else:
                    status["trending"] = status["requests"] = "off"
                    rows.pop("trending", None)
                    rows.pop("requests", None)
            self.rows, self.status = rows, status
            self._save_rows()
            self.bus.emit("media", rows=rows, status=status, accounts=self.public())
            return True
        finally:
            self.refresh_lock.release()

    def watch(self):
        """Rafraîchit les affiches toutes les 5 minutes, et les scores (chaque minute pendant un match)."""
        next_rows = 0.0
        while True:
            now = time.monotonic()
            if now >= next_rows:
                try:
                    self.refresh(force=True)
                except Exception as e:  # une source qui renvoie n'importe quoi ne doit pas tout arrêter
                    log.warning("rangées de l'accueil : %s", e)
                next_rows = now + 300
            try:
                self.sports.refresh()
            except Exception as e:
                log.warning("scores : %s", e)
            self.wake.wait(60 if self.sports.live or self.dry else 300)
            self.wake.clear()

    def find(self, item_id):
        for items in self.rows.values():
            for it in items:
                if it.get("id") == item_id:
                    return it
        return None

    def open_item(self, item_id):
        """OK sur une affiche : fiche Plex Web, ou demande Overseerr (renvoie ce que le menu doit faire)."""
        it = self.find(item_id)
        if not it:
            return {"error": "introuvable"}
        op = it.get("open") or {}
        if "plex" in op:
            if not self.plex.ready():
                return {"error": "Connecte Plex dans Réglages › Comptes."}
            if self.prefs().get("plexOpen") == "web" or not self.open_plex:
                self.launch_url("plexweb", "Plex", self.plex.web_url(op["plex"]), "scaros-plexweb")
                return {"ok": True, "opened": "plexweb"}
            self.open_plex(op["plex"], op.get("offset") or 0, op.get("type") or "movie", it.get("title") or "")
            return {"ok": True, "opened": "plex"}
        if "request" in op:
            return {"ok": True, "ask": op["request"]}
        return {"ok": True, "info": op.get("info") or it.get("title")}

    def request(self, mtype, tmdb, title=""):
        if not self.overseerr.ready():
            return "Connecte Overseerr dans Réglages › Comptes."
        err = self.overseerr.request(mtype, tmdb)
        if not err:
            for items in self.rows.values():  # l'affiche passe tout de suite à « Demandé »
                for it in items:
                    if it.get("id") == f"tmdb:{mtype}:{tmdb}":
                        it["badge"] = "Demandé"
                        it["open"] = {"info": f"« {it['title']} » est déjà demandé."}
            self.bus.emit("media", rows=self.rows, status=self.status, accounts=self.public())
            threading.Timer(4, self.refresh, kwargs={"force": True}).start()
        return err

    # ---------- musique en cours ----------
    def now_playing(self):
        at, val = self.np_cache
        if time.monotonic() - at < 1.2:
            return val
        if self.dry:
            val = self._demo_np()
        else:
            local = self.spotify.local()
            web = self.spotify.web() if self.spotify.ready() else None
            air = self.airplay_np()
            if web and web["playing"]:
                val = web
            elif local and local["playing"]:
                val = local
            elif air:
                val = air
            else:
                val = web or local
        if val:
            val = {**val, "at": int(time.time() * 1000)}
        self.np_cache = (time.monotonic(), val)
        return val

    def control(self, action):
        np = self.now_playing()
        if not np or not np.get("controls"):
            return False
        if self.dry:
            self.demo_playing = not getattr(self, "demo_playing", True) if action in ("toggle", "play", "pause") else True
            self.np_cache = (0.0, None)
            return True
        ok = self.spotify.control(np.get("via"), action)
        self.np_cache = (0.0, None)
        return ok

    # ---------- démo (--dry-run) ----------
    def _demo_accounts(self):
        if not self.accounts.get("demo").get("done"):
            self.accounts.update("plex", server="http://192.168.1.20:32400", serverToken="demo", machineId="demo", serverName="Plex de la maison")
            self.accounts.update("overseerr", url="http://192.168.1.20:5055", key="demo", user="Sam")
            self.accounts.update("spotify", clientId="0" * 32, refresh="demo", user="Sam")
            self.accounts.update("demo", done=True)

    def _demo_rows(self):
        def it(i, title, sub, kind, prog=None, badge=None, open_=None, meta=None):
            return {"id": f"demo:{i}", "title": title, "sub": sub, "kind": kind, "meta": meta or kind, "poster": None, "art": None,
                    "progress": prog, "badge": badge, "src": "Plex", "open": open_ or {"plex": str(i)}, "hue": (i * 47) % 360}
        rows = {
            "continue": [it(1, "Les Phares du Nord", "S2 · É5 · La marée", "Série", .42, meta="Série · Drame"),
                         it(2, "Orbite basse", "2025", "Film", .71, meta="Film · Science-fiction"),
                         it(3, "Le Dernier Train", "S1 · É3 · Minuit", "Série", .15, meta="Série · Thriller"),
                         it(4, "Cuisine de rue", "S4 · É1", "Série", .88, meta="Série · Documentaire"),
                         it(5, "Altitude", "2024", "Film", .3, meta="Film · Aventure")],
            "recent": [it(10 + i, t, y, k) for i, (t, y, k) in enumerate([("Marée haute", "2026", "Film"), ("Les Veilleurs", "Saison 3", "Série"),
                       ("Néon", "2025", "Film"), ("Petit Matin", "2026", "Film"), ("La Vallée", "Saison 1", "Série"), ("Contre-jour", "2024", "Film"),
                       ("Brise", "2026", "Film")])],
            "trending": [it(30 + i, t, y, k, badge=b, open_={"request": {"type": "movie", "id": 100 + i, "title": t}} if not b else None)
                         for i, (t, y, k, b) in enumerate([("Horizon rouge", "2026", "Film", None), ("Les Fondations", "2026", "Série", "Disponible"),
                                                          ("Glace", "2025", "Film", None), ("Signal", "2026", "Série", "Demandé"), ("Lumière noire", "2026", "Film", None),
                                                          ("Archipel", "2025", "Série", None)])],
            "requests": [it(50, "Signal", "Téléchargement · 62 % · Sam", "Série", .62, badge="Téléchargement · 62 %", open_={"info": "« Signal » : en téléchargement."}),
                         it(51, "Les Fondations", "Disponible · Sam", "Série", badge="Disponible"),
                         it(52, "Désert", "En attente de validation · Sam", "Film", badge="En attente de validation", open_={"info": "« Désert » : en attente."})],
        }
        for k, items in rows.items():
            for x in items:
                x["src"] = "Overseerr" if k in ("trending", "requests") else "Plex"
        return rows, {k: "ok" for k in rows}

    def _demo_np(self):
        start = getattr(self, "demo_start", None)
        if start is None:
            start = self.demo_start = time.time()
        playing = getattr(self, "demo_playing", True)
        dur = 214000
        return {"source": "spotify", "via": "web", "title": "Nuit blanche", "artist": "Les Lueurs", "album": "Après minuit",
                "cover": None, "playing": playing, "position": int((time.time() - start) * 1000) % dur, "duration": dur,
                "device": "iPhone de Sam", "controls": True}
