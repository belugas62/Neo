# Analyse de l'APK NeoX2 v3.5 (`com.iptv.neox2`) — Rapport & diagnostic

**Fichier analysé :** `neox2-v3.5.apk`
**SHA-256 :** `9a116ea45d286349cfc446f311efdeb8725e0f0df486c26c68ce7ef12e48a149`
**Taille :** 22,8 Mo
**Date d'analyse :** 2026-10-30

> **Résultat en une phrase :** le fichier `NeoX2.m3u` **ne peut pas être généré
> à partir de l'APK**, car l'APK ne contient **aucune donnée de catalogue et
> aucune URL de flux**. L'outil d'analyse et le convertisseur sont livrés et
> fonctionnels ; il leur manque un fichier catalogue (`live.json`) qui n'existe
> que sur un appareil activé. Voir §5 et §6.

---

## 1. Métadonnées de l'application

| Champ | Valeur |
|---|---|
| Package | `com.iptv.neox2` |
| Nom | NeoX2 |
| Version | 3.5 (versionCode 91) |
| SDK min / cible | 19 / 27 |
| Activité principale | `com.iptv.neox2.ui.Intro` |

**Permissions :** `INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE`,
`READ/WRITE_EXTERNAL_STORAGE`, `READ_PHONE_STATE`, `READ_SETTINGS`,
`FOREGROUND_SERVICE`, `WAKE_LOCK`, `RECEIVE_BOOT_COMPLETED`,
`REQUEST_INSTALL_PACKAGES`.

**Composants notables :** lecteurs `PlayerIPTV`, `PlayerMovie`, `PlayerSerie`,
`PlayerReplay`, `PlayerEvent` (ExoPlayer) ; écrans `Login`, `Enter`, `Menu`,
`Vod`, `Movies_*`, `Series_*`, `Sport_*`, `Replay`, `Radio`, `Quran`, `Events` ;
service **OpenVPN** intégré (`de.blinkt.openvpn` / ics-openvpn) ;
`ForceUpdate`/`Update`. C'est une application **cliente IPTV générique** : elle
lit un catalogue fourni par un serveur, elle ne contient pas de contenu.

## 2. Contenu réel de l'APK

| Zone | Contenu | Donnée de catalogue ? |
|---|---|---|
| `assets/` | uniquement `*_openvpn.*` (binaires OpenVPN par ABI) + `dexopt/baseline.prof` | **Non** |
| `lib/` | `libavcodec`, `libavutil`, `libswresample`, `libopenvpn`, `libimagepipeline`, `libgifimage`… | **Non** |
| `res/` | polices, GIF, un `.mp4` (splash), un `.html`, images UI | **Non** |
| `resources.arsc` | chaînes UI / libellés | **Non** |
| `classes.dex` | code applicatif (`com.iptv.*`) + librairies | **Non** |

**Aucun fichier `.json`, `.m3u`, `.m3u8`, `.db` de catalogue n'est présent.**

## 3. URL présentes en clair dans le code

Aucune URL de flux (`m3u8`, `/live/`, `get.php`, `player_api`, `.ts`…) n'existe
en clair. Les seules URL trouvées sont contextuelles et **ne sont pas des flux** :

- `http://ll.ssdlist.xyz:2022/apk/neox2/bg.jpg` — image de fond de l'app
- `https://t.me/neoiptv` — lien Telegram
- `https://%s:%s` — **gabarit** d'URL rempli à l'exécution (voir §5)
- URL de projets tiers (ics-openvpn, BouncyCastle, Xposed, Android docs…)

## 4. Modèle de données du catalogue (reconstitué par analyse statique)

L'app lit son catalogue depuis des fichiers JSON de son **dossier privé**
(`/data/data/com.iptv.neox2/files/`), écrits à l'exécution :

```
live.json, vod.json, radio.json, events.json, epg.json, sports.json,
sp.json, replay.json, replay_new.json, coran.json, movieDetails.json,
episodes.json, status.json
```

Lecture confirmée dans le code (`new JSONObject(Global.M("live"))`) — les
fichiers sont stockés **en clair** (JSON brut) sur l'appareil.

**Structure de `live.json` :**

```json
{ "data": [ { "c_iptv": [ /* entrées chaînes */ ], "b_iptv": [ /* ... */ ] } ] }
```

**Champs d'une entrée (chaîne / VOD)** — tels que lus par l'app :

| Champ | Rôle | Utile au M3U |
|---|---|---|
| `id` | identifiant | `tvg-id` (secours) |
| `name` / `title` / `channelname` | nom affiché | nom de la piste |
| `logo` (`logol`, `logor`) | URL du logo | `tvg-logo` |
| **`link`** | **URL du flux** | **URL de la piste** |
| `parent` | catégorie parente | `group-title` |
| `genre` | genre (VOD) | `group-title` (secours) |
| `lang` | langue | — |
| `epgid` | identifiant EPG | `tvg-id` |

Autres champs liés à l'activation/compte (hors catalogue) :
`ACTIVECODE`, `UID`, `SERIAL`, `MODEL`, `PACK_ID`, `PACK_NAME`, `hash`, `sk/si/sh`.

## 5. Pourquoi le M3U n'est pas générable depuis l'APK

Le flux de données réel de l'application est :

1. `Intro` → `Login`/`Enter` : l'app lit une **configuration serveur chiffrée**
   embarquée dans `com.iptv.neox2.util.Global` (champs base64 → AES/CBC, clés
   dérivées en SHA-256) et construit une URL via le gabarit `https://%s:%s`.
2. Après **activation** (code + identifiants d'appareil), le serveur renvoie le
   catalogue, que l'app **télécharge** et écrit en `*.json` dans son dossier privé.
3. Les lecteurs lisent `live.json`/`vod.json`/… et jouent le champ `link`.

Conséquence : **les chaînes, catégories, logos et URL de flux n'existent pas
dans l'APK**. Ils résident :

- côté **serveur de l'opérateur** (non sollicité ici, conformément à la consigne) ;
- et, sur un appareil **déjà activé**, en clair dans
  `/data/data/com.iptv.neox2/files/live.json` (et les autres `*.json`).

> Je n'ai volontairement **pas** déchiffré la configuration serveur de `Global`
> (blobs AES) : cela reviendrait à contourner le contrôle d'activation/accès, ce
> que votre consigne n°7 exclut — et c'est inutile, puisque le catalogue lui-même
> n'y est de toute façon pas.

### Données manquantes et emplacement normal

| Donnée manquante | Où elle se trouve normalement |
|---|---|
| Liste des chaînes / VOD / radios | `files/live.json`, `files/vod.json`, `files/radio.json` (sur appareil activé) |
| Catégories (`parent`/`genre`) | mêmes fichiers |
| Logos (`logo`) | URL dans ces fichiers (hébergées côté serveur) |
| **URL de flux (`link`)** | **mêmes fichiers**, champ `link` |
| EPG | `files/epg.json` |

## 6. Ce qui est livré et comment générer le M3U

Deux outils autonomes (Python 3, hors-ligne, aucune connexion réseau) dans
`neox2_tool/` :

### `analyze_apk.py` — audit du catalogue d'un APK
```bash
python3 neox2_tool/analyze_apk.py neox2-v3.5.apk
```
Rapporte métadonnées, assets, URL en clair, modèle de données, et conclut si un
M3U est générable. Sur cet APK : **code de sortie 2 — aucune donnée de catalogue**.

### `catalog_to_m3u.py` — convertisseur catalogue → M3U (VLC)
```bash
# À lancer sur le(s) fichier(s) catalogue récupéré(s) sur VOTRE appareil activé
python3 neox2_tool/catalog_to_m3u.py live.json -o NeoX2.m3u
python3 neox2_tool/catalog_to_m3u.py live.json vod.json radio.json -o NeoX2.m3u
```
- Détecte la structure NeoX2 (`data[].c_iptv/b_iptv`) et les tableaux simples.
- Mappe `name/title` → nom, `parent/genre` → `group-title`, `logo` → `tvg-logo`,
  `epgid/id` → `tvg-id`, **`link` → URL**.
- **Ne fabrique jamais de lien** : si `link` n'est pas une URL lisible
  (valeur encodée/chiffrée), l'entrée est ignorée et signalée.

Un exemple de **format** (pas de vrais flux) est fourni dans
`neox2_tool/examples/` pour montrer la sortie attendue :
`example_catalog.json` → `example_output.m3u`.

### Pour obtenir un `NeoX2.m3u` réel, il vous faut (sur un appareil qui vous
appartient et que vous avez légitimement activé) récupérer le fichier
`/data/data/com.iptv.neox2/files/live.json` (p. ex. via la sauvegarde de l'app
ou un explorateur de fichiers sur appareil rooté), puis le passer au
convertisseur. Si les champs `link` y sont des URL en clair, le `NeoX2.m3u` sera
produit directement ; s'ils sont encodés, le convertisseur vous le dira et
s'arrêtera (pas de lien inventé).

## 7. Méthode & périmètre

- Décompilation statique (androguard / DAD) des 376 classes `com.iptv.*`,
  lecture des DEX et de `resources.arsc`, inventaire de `assets/`, `lib/`, `res/`.
- **Aucun serveur contacté. Aucune activation/protection contournée. Aucun
  secret déchiffré. Aucun lien fabriqué.** Travail limité aux fichiers fournis.
