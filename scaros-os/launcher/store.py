"""Scaros OS — l'App Store : une sélection d'applis qui se pilotent bien à la télé (Flathub),
et les sites web ajoutés comme des applis.

native : l'appli lit la manette elle-même (jeux, émulateurs) ; Scaros la lui laisse, comme
pour Steam Link (maintenir le bouton Xbox ramène au menu).
mouse : l'appli se pilote mieux avec le mode souris de la télécommande.
"""

import re
import unicodedata

CATEGORIES = (("video", "Vidéo"), ("music", "Musique"), ("games", "Jeux"))

CATALOG = [
    # vidéo
    {"id": "kodi", "flatpak": "tv.kodi.Kodi", "name": "Kodi", "cat": "video", "color": "#17b2e7",
     "desc": "Le centre multimédia de référence : films, séries, IPTV, extensions. Parfait à la manette."},
    {"id": "stremio", "flatpak": "com.stremio.Stremio", "name": "Stremio", "cat": "video", "color": "#7b5bf5", "mouse": True,
     "desc": "Films et séries en streaming grâce à des extensions."},
    {"id": "freetube", "flatpak": "io.freetubeapp.FreeTube", "name": "FreeTube", "cat": "video", "color": "#e3434b", "mouse": True,
     "desc": "YouTube sans publicité ni compte, tes abonnements gardés sur le PC."},
    {"id": "grayjay", "flatpak": "app.grayjay.Grayjay", "name": "Grayjay", "cat": "video", "color": "#2f6bff", "mouse": True,
     "desc": "YouTube, Twitch, PeerTube… réunis dans une seule appli."},
    {"id": "vlc", "flatpak": "org.videolan.VLC", "name": "VLC", "cat": "video", "color": "#ff8800", "mouse": True,
     "desc": "Lit tous les fichiers vidéo et audio, et les flux réseau."},
    # musique
    {"id": "plexamp", "flatpak": "com.plexamp.Plexamp", "name": "Plexamp", "cat": "music", "color": "#e5a00d", "mouse": True,
     "desc": "La musique de ton serveur Plex, avec une belle interface."},
    {"id": "spotifyapp", "flatpak": "com.spotify.Client", "name": "Spotify", "cat": "music", "color": "#1ed760", "mouse": True,
     "desc": "L’appli Spotify officielle. Pour juste écouter, Spotify Connect suffit (choisis Scaros dans l’appli du téléphone)."},
    {"id": "shortwave", "flatpak": "de.haeckerfelix.Shortwave", "name": "Shortwave", "cat": "music", "color": "#3a86ff", "mouse": True,
     "desc": "Des milliers de radios du monde entier."},
    # jeux
    {"id": "moonlight", "flatpak": "com.moonlight_stream.Moonlight", "name": "Moonlight", "cat": "games", "color": "#5b5bd6", "native": True,
     "desc": "Les jeux de ton PC (Sunshine ou GeForce Experience) sur la télé, à la manette."},
    {"id": "greenlight", "flatpak": "io.github.unknownskl.greenlight", "name": "Greenlight", "cat": "games", "color": "#107c10", "native": True,
     "desc": "Xbox Cloud Gaming, et ta Xbox à distance."},
    {"id": "chiaki", "flatpak": "io.github.streetpea.Chiaki4deck", "name": "chiaki-ng", "cat": "games", "color": "#2e6db4", "native": True,
     "desc": "Joue à ta PlayStation à distance (Remote Play)."},
    {"id": "retroarch", "flatpak": "org.libretro.RetroArch", "name": "RetroArch", "cat": "games", "color": "#585858", "native": True,
     "desc": "Les consoles rétro (NES, Super Nintendo, Mega Drive, PlayStation…), tout à la manette."},
    {"id": "ppsspp", "flatpak": "org.ppsspp.PPSSPP", "name": "PPSSPP", "cat": "games", "color": "#2f8fd8", "native": True,
     "desc": "Émulateur PSP, léger : tourne bien sur un petit PC."},
    {"id": "pegasus", "flatpak": "org.pegasus_frontend.Pegasus", "name": "Pegasus", "cat": "games", "color": "#c03b3b", "native": True,
     "desc": "Une belle bibliothèque pour lancer tes jeux rétro à la manette."},
]
BY_ID = {e["id"]: e for e in CATALOG}


def slug(text):
    """« Mon Site ! » -> « mon-site »."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:24] or "site"


def app_conf(entry):
    """Ce que scaros.py enregistre pour une appli installée depuis l'App Store."""
    conf = {"name": entry["name"], "cmd": ["flatpak", "run", entry["flatpak"]], "flatpak": entry["flatpak"],
            "store": True, "color": entry["color"]}
    if entry.get("native"):
        conf["controller"] = "native"
    return conf
