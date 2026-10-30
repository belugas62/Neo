#!/usr/bin/env python3
"""
analyze_apk.py — Analyseur hors-ligne du catalogue d'un APK NeoX2 (com.iptv.neox2).

Objectif : inspecter UNIQUEMENT les fichiers contenus dans l'APK fourni
(assets, ressources, chaînes du/des DEX) pour déterminer :
  - les métadonnées de l'application,
  - si des données de catalogue (chaînes, VOD, logos, flux) sont EMBARQUÉES,
  - les URL/flux présents en clair, le cas échéant,
  - le modèle de données attendu par l'app (noms des fichiers JSON et champs).

Ce script NE CONTACTE AUCUN SERVEUR, ne déchiffre aucun secret d'activation
et ne contourne aucune protection. Lecture seule.

Usage :
    python3 analyze_apk.py /chemin/vers/neox2.apk
"""
import os
import re
import sys
import zipfile

# --- Schéma du catalogue reconstitué par analyse statique de NeoX2 v3.5 -------
# Fichiers JSON que l'app lit dans son dossier privé (filesDir), écrits à
# l'exécution après activation — ils ne sont PAS dans l'APK.
CATALOG_FILES = [
    "live", "vod", "radio", "events", "epg", "sports", "sp",
    "replay", "replay_new", "coran", "movieDetails", "episodes", "status",
]
# Structure de live.json : {"data":[{"c_iptv":[...], "b_iptv":[...]}]}
# Champs d'une entrée (chaîne / VOD) :
CATALOG_FIELDS = {
    "id": "identifiant de l'entrée",
    "name / title / channelname": "nom affiché",
    "logo / logol / logor": "URL du logo (normal / gauche / droite)",
    "link": "URL du flux à lire (champ clé pour un M3U)",
    "parent": "catégorie / groupe parent",
    "genre": "genre (VOD / films)",
    "lang": "langue",
    "epgid": "identifiant EPG",
}

URL_RE = re.compile(rb"(?:https?|rtmps?|rtsp|udp|mms)://[^\x00-\x20\"'<>\\]{4,}")
STREAM_HINT_RE = re.compile(rb"(m3u8|#EXTM3U|#EXTINF|/live/|/movie/|/series/|get\.php|player_api|\.ts(\?|$))", re.I)
# Hôtes de ressources UI non pertinents pour le catalogue (bruit connu).
NOISE_HOST_RE = re.compile(
    rb"(schemas\.android\.com|www\.android\.com|w3\.org|ns\.adobe\.com|"
    rb"google\.com|google\.github\.io|youtube\.com|wallpapers\.com)", re.I)


def human(n):
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.0f} To"


def scan_strings_for_urls(data):
    """Renvoie (urls_catalogue_candidates, urls_bruit, indices_de_flux)."""
    candidates, noise = set(), set()
    for m in URL_RE.finditer(data):
        u = m.group(0)
        (noise if NOISE_HOST_RE.search(u) else candidates).add(u.decode("latin1"))
    hints = sorted({m.group(0).decode("latin1").lower() for m in STREAM_HINT_RE.finditer(data)})
    return sorted(candidates), sorted(noise), hints


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    apk = sys.argv[1]
    if not zipfile.is_zipfile(apk):
        sys.exit(f"[ERREUR] {apk} n'est pas une archive APK/ZIP lisible.")

    print("=" * 70)
    print(f"ANALYSE DU CATALOGUE — {os.path.basename(apk)}")
    print("=" * 70)

    data_assets, catalog_embedded = [], []
    url_cat, url_noise, stream_hints = set(), set(), set()

    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
        # 1) Fichiers de données potentiellement embarqués
        for n in names:
            low = n.lower()
            if low.startswith("assets/"):
                data_assets.append(n)
            if (re.search(r"\.(json|m3u8?|db|sqlite)$", low)
                    and not low.startswith("res/")
                    and not low.startswith("meta-inf/")):
                catalog_embedded.append(n)

        # 2) Balayage des chaînes dans chaque .dex et le resources.arsc
        for n in names:
            if n.endswith(".dex") or n == "resources.arsc":
                raw = z.read(n)
                c, nz, h = scan_strings_for_urls(raw)
                url_cat.update(c)
                url_noise.update(nz)
                stream_hints.update(h)

    # --- Rapport -------------------------------------------------------------
    print("\n[1] FICHIERS DE DONNÉES EMBARQUÉS (JSON/M3U/DB hors UI)")
    if catalog_embedded:
        for n in catalog_embedded:
            print(f"    TROUVÉ : {n}")
    else:
        print("    Aucun. → Le catalogue n'est PAS embarqué dans l'APK.")

    print("\n[2] CONTENU DE assets/")
    for n in data_assets:
        print(f"    {n}")
    if all("openvpn" in a.lower() or "dexopt" in a.lower() for a in data_assets):
        print("    → Uniquement des binaires OpenVPN / profil ART. Aucune donnée de catalogue.")

    print("\n[3] FLUX / URL EN CLAIR DANS LE CODE")
    real_streams = [u for u in url_cat if STREAM_HINT_RE.search(u.encode("latin1"))]
    if real_streams:
        print("    URL de flux détectées :")
        for u in sorted(real_streams):
            print(f"      {u}")
    else:
        print("    Aucune URL de flux (m3u8/live/get.php/...) en clair.")
    if url_cat:
        print("    Autres URL non-framework trouvées (contexte, pas des flux) :")
        for u in sorted(url_cat):
            print(f"      {u}")
    print(f"    Indices de flux dans les chaînes : {sorted(stream_hints) or 'aucun'}")

    print("\n[4] MODÈLE DE DONNÉES ATTENDU PAR L'APP (reconstitué)")
    print("    Fichiers catalogue lus dans filesDir (écrits à l'exécution) :")
    print("      " + ", ".join(f"{c}.json" for c in CATALOG_FILES))
    print("    Structure live.json : {\"data\":[{\"c_iptv\":[...], \"b_iptv\":[...]}]}")
    print("    Champs d'une entrée :")
    for k, v in CATALOG_FIELDS.items():
        print(f"      - {k:<28} {v}")

    print("\n[5] CONCLUSION")
    producible = bool(real_streams) or bool(catalog_embedded)
    if producible:
        print("    Des données de catalogue exploitables ont été trouvées DANS l'APK.")
        print("    → Voir catalog_to_m3u.py pour générer le M3U.")
    else:
        print("    Aucune donnée de catalogue ni URL de flux n'est présente dans l'APK.")
        print("    → Un M3U ne peut PAS être généré à partir de l'APK seul.")
        print("    → Le catalogue est téléchargé à l'exécution depuis le serveur de")
        print("      l'opérateur (après activation) et stocké en clair sous forme de")
        print("      fichiers <nom>.json dans le dossier privé de l'app :")
        print("      /data/data/com.iptv.neox2/files/*.json")
        print("      C'est le fichier live.json (champ 'link') qui contient les flux.")
    print("=" * 70)
    return 0 if producible else 2


if __name__ == "__main__":
    raise SystemExit(main())
