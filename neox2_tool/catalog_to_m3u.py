#!/usr/bin/env python3
"""
catalog_to_m3u.py — Convertisseur catalogue NeoX2 (JSON) -> playlist M3U (VLC).

Ce convertisseur transforme un fichier catalogue AU FORMAT NeoX2 (ex. live.json,
vod.json, radio.json) en une playlist M3U standard avec nom, groupe (catégorie)
et logo, à partir du champ `link` (URL de flux).

IMPORTANT — périmètre légitime :
  - Ce script travaille UNIQUEMENT sur un fichier JSON que VOUS fournissez et
    auquel vous avez légitimement accès (p. ex. le fichier live.json que
    l'application a téléchargé sur VOTRE appareil activé, dans
    /data/data/com.iptv.neox2/files/live.json).
  - Il NE contacte aucun serveur, NE déchiffre aucun secret d'activation et NE
    fabrique aucun lien. Si le champ `link` ne contient pas une URL lisible, le
    script le signale et NE l'invente pas.

Usage :
    python3 catalog_to_m3u.py live.json -o NeoX2.m3u
    python3 catalog_to_m3u.py live.json vod.json radio.json -o NeoX2.m3u

Le script détecte automatiquement la structure NeoX2 ({"data":[{"c_iptv":[...]}]})
ainsi que des tableaux d'entrées plus simples.
"""
import argparse
import json
import re
import sys

URL_RE = re.compile(r"^(https?|rtmps?|rtsp|udp|mms)://", re.I)

# Correspondances de champs tolérantes (le format varie entre live/vod/radio).
NAME_KEYS = ("name", "title", "channelname", "NAME", "eventName", "ep_name")
LINK_KEYS = ("link", "url", "stream", "LINK")
LOGO_KEYS = ("logo", "logol", "logor", "LOGO", "thumb", "THUMB")
GROUP_KEYS = ("parent", "genre", "category", "PARENT", "GENRE", "competitionName")
TVGID_KEYS = ("epgid", "id", "idchannel", "ID")


def first(d, keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", "null"):
            return str(d[k])
    return ""


def iter_entries(obj):
    """Parcourt récursivement toute structure JSON et renvoie les dict qui
    ressemblent à une entrée de catalogue (présence d'un champ 'link'-like)."""
    if isinstance(obj, dict):
        if any(k in obj for k in LINK_KEYS):
            yield obj
        for v in obj.values():
            yield from iter_entries(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_entries(v)


def extinf(name, link, logo, group, tvgid):
    attrs = []
    if tvgid:
        attrs.append(f'tvg-id="{tvgid}"')
    if name:
        attrs.append(f'tvg-name="{name}"')
    if logo and URL_RE.match(logo):
        attrs.append(f'tvg-logo="{logo}"')
    if group:
        attrs.append(f'group-title="{group}"')
    head = "#EXTINF:-1"
    if attrs:
        head += " " + " ".join(attrs)
    return f"{head},{name or 'Sans nom'}\n{link}\n"


def main():
    ap = argparse.ArgumentParser(description="Convertit un catalogue NeoX2 JSON en M3U.")
    ap.add_argument("inputs", nargs="+", help="Fichier(s) catalogue JSON (live.json, vod.json, ...)")
    ap.add_argument("-o", "--output", default="NeoX2.m3u", help="Fichier M3U de sortie")
    args = ap.parse_args()

    lines = ["#EXTM3U\n"]
    total = skipped_no_url = 0
    skipped_samples = []

    for path in args.inputs:
        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception as e:
            print(f"[ERREUR] Lecture de {path} : {e}", file=sys.stderr)
            continue

        count = 0
        for e in iter_entries(obj):
            link = first(e, LINK_KEYS)
            name = first(e, NAME_KEYS)
            if not link:
                continue
            if not URL_RE.match(link):
                skipped_no_url += 1
                if len(skipped_samples) < 5:
                    skipped_samples.append((name or "?", link[:60]))
                continue
            logo = first(e, LOGO_KEYS)
            group = first(e, GROUP_KEYS)
            tvgid = first(e, TVGID_KEYS)
            lines.append(extinf(name, link, logo, group, tvgid))
            count += 1
            total += 1
        print(f"[OK] {path} : {count} entrée(s) avec flux exploitable.", file=sys.stderr)

    if total == 0:
        print("\n[DIAGNOSTIC] Aucune entrée avec une URL de flux lisible n'a été trouvée.",
              file=sys.stderr)
        if skipped_no_url:
            print(f"  {skipped_no_url} entrée(s) possèdent un champ 'link' NON reconnu "
                  "comme URL (valeur encodée/chiffrée côté catalogue).", file=sys.stderr)
            for n, v in skipped_samples:
                print(f"    ex. {n!r} -> link={v!r}", file=sys.stderr)
            print("  Ce convertisseur NE fabrique PAS de liens : fournissez un catalogue\n"
                  "  dont les champs 'link' sont des URL en clair.", file=sys.stderr)
        print("  Aucun fichier M3U n'a été écrit.", file=sys.stderr)
        return 2

    with open(args.output, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"\n[SUCCÈS] {total} entrée(s) écrite(s) dans {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
