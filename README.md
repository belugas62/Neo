# NeoX2 — Outil d'analyse de catalogue & convertisseur M3U

Outils hors-ligne pour analyser l'APK **NeoX2 v3.5** (`com.iptv.neox2`) et
convertir un catalogue NeoX2 (JSON) en playlist **M3U** compatible VLC.

- **Diagnostic complet :** [`RAPPORT_NeoX2.md`](RAPPORT_NeoX2.md)
- **Outils :** [`neox2_tool/`](neox2_tool/)

## TL;DR

L'APK **ne contient aucune donnée de catalogue ni URL de flux** : chaînes,
logos et flux sont téléchargés à l'exécution depuis le serveur de l'opérateur et
stockés en clair dans `/data/data/com.iptv.neox2/files/*.json` sur un appareil
activé. Un `NeoX2.m3u` **ne peut donc pas être produit depuis l'APK seul**, mais
le convertisseur le génère immédiatement à partir d'un `live.json` que vous
récupérez légitimement sur votre appareil.

## Utilisation

```bash
# 1) Auditer un APK (lecture seule, aucune connexion)
python3 neox2_tool/analyze_apk.py chemin/vers/neox2-v3.5.apk

# 2) Convertir un catalogue NeoX2 en M3U (sur VOTRE live.json)
python3 neox2_tool/catalog_to_m3u.py live.json -o NeoX2.m3u
python3 neox2_tool/catalog_to_m3u.py live.json vod.json radio.json -o NeoX2.m3u
```

Exemple de format (pas de vrais flux) : `neox2_tool/examples/`.

## Garanties

Aucun serveur n'est contacté, aucune activation/protection n'est contournée,
aucun secret n'est déchiffré et **aucun lien n'est fabriqué**. Si un champ
`link` n'est pas une URL lisible, l'entrée est signalée et ignorée.
