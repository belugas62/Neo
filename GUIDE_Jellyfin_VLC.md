# NeoX2.m3u — chaînes publiques légales (France + Sport) pour Jellyfin / VLC

Playlist générée à partir de sources **publiques et légales** (chaînes diffusées
en clair), **chaque flux testé** le 2026-10-30. Elle remplace l'usage NeoX2 par
un M3U ouvert, lisible dans Jellyfin, VLC, TiviMate, etc.

- **Fichier :** `NeoX2.m3u` (210 chaînes)
- **EPG (guide des programmes) :** `https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz`

## Ce qu'il y a dedans (et ce que valent les groupes)

| Groupe | Nb | Statut |
|---|---|---|
| `TV France` | 134 | ✅ **Testées vivantes** depuis le serveur — fonctionnent. |
| `Sport` | 23 | ✅ **Testées vivantes** (beIN Sports Xtra, Eurosport, L'Équipe…). |
| `TV France (FR uniquement)` | 31 | 🔒 **Géo-restreintes** (refus hors France) → **marcheront depuis ta connexion française**, pas ailleurs. |
| `Sport (FR uniquement)` | 3 | 🔒 Idem, géo-France. |
| `A tester depuis la France` | 19 | ⚠️ **France 3, France 2/4/5, TF1, M6, TMC, W9, Gulli…** Ce sont des **rediffusions non officielles** sur des hôtes IP que mon serveur ne peut pas joindre. Elles peuvent marcher chez toi **ou** être mortes — **instables par nature**. |

> **Sur France 3 (et TF1/M6/France 2…)** : je n'ai **pas pu les vérifier** d'ici,
> et les versions présentes dans les listes gratuites sont des re-streams non
> officiels, peu fiables. Si tu veux ces chaînes **de façon stable**, la vraie
> bonne solution reste la **TNT** (antenne + tuner, pris en charge nativement par
> Jellyfin) ou l'app **france.tv**. La playlist te les propose quand même, dans le
> groupe « A tester depuis la France » — teste-les sur ta X96, garde celles qui
> passent.

## Installer dans Jellyfin (ta box X96)

1. **Tableau de bord → Live TV → Tuner Devices → `+` → type `M3U Tuner`.**
2. Dans **File or URL**, mets le fichier ou une URL :
   - fichier local : copie `NeoX2.m3u` sur la box et indique son chemin, **ou**
   - héberge-le (clé USB / partage réseau) et pointe dessus.
3. **Guide (EPG) : Live TV → TV Guide Data Providers → `+` → `XMLTV`** →
   colle `https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz`.
4. **Refresh Guide Data**, puis ouvre **Live TV**. Les groupes apparaissent en
   catégories.

> **Astuce EPG :** le guide ne s'affiche que si le `tvg-id` d'une chaîne
> correspond à un `channel id` de l'EPG. Comme les deux viennent de sources
> différentes, le mapping n'est pas parfait : dans Jellyfin, tu peux corriger
> chaîne par chaîne (bouton *Map* dans la gestion des chaînes Live TV).

## Lire dans VLC (pour tester vite)

- **Média → Ouvrir un fichier →** `NeoX2.m3u` (ou glisse-le dans VLC).
- Ou une chaîne seule : **Média → Ouvrir un flux réseau →** colle l'URL.

## Régénérer / mettre à jour la playlist

Les chaînes publiques bougent (certaines s'éteignent). Pour refaire une passe de
test et reconstruire le fichier :

```bash
cd neox2_tool && \
curl -sSo fr.m3u      https://iptv-org.github.io/iptv/countries/fr.m3u && \
curl -sSo sports.m3u  https://iptv-org.github.io/iptv/categories/sports.m3u && \
curl -sSo freetv.m3u8 https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8 && \
python3 build_playlist.py        # régénère NeoX2.m3u
```

## Sources (légales, publiques)

- iptv-org — https://github.com/iptv-org/iptv (flux publics indexés, hors adulte)
- Free-TV/IPTV — https://github.com/Free-TV/IPTV (chaînes free-to-air)
- EPG France — https://epgshare01.online/ (guide XMLTV)
