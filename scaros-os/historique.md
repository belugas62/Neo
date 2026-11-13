# Historique du projet Scaros OS

Ce fichier résume tout ce qui a été fait sur Scaros OS avec Claude (depuis le 31 octobre 2026), pour pouvoir
reprendre le travail dans une nouvelle conversation : il suffit de lui donner ce fichier.
**Il est tenu à jour à chaque changement** (voir le journal tout en bas).

---

## 1. Le projet en bref

Scaros OS transforme un vieux PC portable en box TV, façon Apple TV / Google TV.

- **Machine visée** : PC portable Intel Celeron N4000, 4 Go de RAM, mémoire eMMC (affichée « SD » sous Windows).
- **Système** : Debian 13 minimal, avec une session graphique sans bureau (Openbox). Le menu tourne dans Chromium
  en mode kiosque. Au démarrage : GRUB masqué, écran Plymouth Scaros, connexion automatique, puis `startx` lance
  `system/session.sh`.
- **Pilotage** : manette Xbox en Bluetooth, télécommande dans le navigateur du téléphone
  (`http://<adresse du PC>:8421/remote`, installable comme une appli sur iPhone), clavier.
- **Écran** : bascule seule sur la télé quand le HDMI est branché, ou au choix dans le menu.
- **Applis du menu** (`launcher/apps.json`) : Scaros TV (appli web sur ton serveur), YouTube (VacuumTube,
  interface TV), Jellyfin, Plex (Plex HTPC), Steam Link, Twitch (SmartTwitchTV, appli web). Les autres viennent de
  l'App Store (Flathub).
- **Version actuelle** : 0.10 (`VERSION` dans `launcher/scaros.py`).

## 2. Où est quoi

| Quoi | Où |
|---|---|
| Version perso (avec Scaros TV, mise à jour automatique) | dépôt GitHub `belugas62/Neo`, branche `scaros-os`, dossier `scaros-os/` |
| Version publique (sans Scaros TV, mise à jour à la main) | dépôt GitHub `Sc4ros/Scaros-OS` (contenu du dossier `scaros-os` à la racine) |
| D'où la box télécharge ses mises à jour | `source.conf` → `REPO=belugas62/Neo`, `BRANCH=scaros-os` (copié dans `/etc/scaros/source.conf`) |
| Maquette en ligne (le menu jouable au clavier, privée) | https://claude.ai/artifact/8BeEQ5DgmdZgrSjsfsnt5D |
| Mode d'emploi complet | `scaros-os/README.md` |

La version publique est préparée dans Claude sur une branche locale `scaros-os-sans-tv` (un « worktree » dans
le dossier de travail de la session). Elle n'existe pas sur `belugas62/Neo` : toi, tu la mets sur `Sc4ros/Scaros-OS`
à la main, à partir du zip `scaros-os-public.zip` que Claude t'envoie.

**Si la session Claude a été perdue** (worktree disparu), on recrée la version publique à partir de la branche
`scaros-os` en retirant : l'appli Scaros TV (`apps.json`, menu, télécommande, logo), l'adresse de ton serveur,
`source.conf` et la mise à jour automatique (`update.sh` réduit à une mise à jour manuelle), et les noms
personnels (voir les règles plus bas). Le dépôt public a aussi un README de présentation, une licence GPL-3.0 et
des captures dans `docs/`.

### Fichiers du projet

```
scaros-os/
├── install.sh              installe tout sur un Debian 13 minimal
├── update.sh               mise à jour (commande scaros-update, lancée aussi depuis le menu)
├── source.conf             le dépôt GitHub d'où viennent les mises à jour
├── historique.md           ce fichier
├── launcher/
│   ├── scaros.py           serveur local (Python, sans dépendance) : applis, écrans, son, Bluetooth, Wi-Fi,
│   │                       manette, télécommande, AirPlay, météo, App Store, réglages, mise à jour
│   ├── media.py            accueil et veille : Plex, Overseerr, Spotify, pochette AirPlay, scores (ESPN)
│   ├── store.py            le catalogue de l'App Store (applis Flathub qui se pilotent bien à la télé)
│   ├── osd.py              volume et notifications affichés par-dessus les applis (GTK, fenêtre sans focus)
│   ├── apps.json           les applis du menu et la commande qui les lance
│   └── ui/
│       ├── index.html      le menu TV (marche aussi seul, en maquette, avec des données de démo)
│       ├── remote.html     la télécommande du téléphone
│       ├── icons/          icônes de la télécommande
│       └── fonts/          police Inter
└── system/
    ├── session.sh          session graphique : Openbox, serveur, menu plein écran
    ├── scaros-diag.sh      diagnostic réseau (commande scaros-diag, lancé aussi depuis le menu)
    ├── openbox/rc.xml      toutes les fenêtres en plein écran
    └── plymouth/scaros/    écran de démarrage
```

## 3. Règles de travail (à respecter dans une nouvelle conversation)

- Répondre **en français**, en **tutoyant**, simplement.
- Pour une **nouvelle idée**, poser d'abord 3 à 6 questions de cadrage (skill « mode-cadrage »), sauf si la
  demande est déjà claire. Pour une simple correction, ne pas poser de questions.
- Après chaque changement : tester, **commit + push** sur `belugas62/Neo` branche `scaros-os`, reporter le
  changement sur la version publique (cherry-pick), refaire les deux zips (`scaros-os.zip` perso et
  `scaros-os-public.zip`), mettre à jour la maquette en ligne, et **ajouter une ligne au journal ci-dessous**.
- `historique.md` reste dans la version perso : lors du report vers la version publique, ne pas l'inclure.
- Pas de pull request sauf si demandé.
- Messages de commit en français, qui expliquent ce qui change pour l'utilisateur.
- **La version publique ne doit jamais contenir** : l'appli Scaros TV, l'adresse IP de ton serveur
  (`176.142.205.87`), ton prénom (« Oscar », par exemple dans « iPhone d'Oscar ») ni « Lille ». Vérifier avec
  une recherche avant chaque zip public (`grep -ri "176\.142\|scarostv\|oscar\|lille"` doit renvoyer 0).
- Ne jamais envoyer ton adresse e-mail à un service extérieur.

### Comment Claude teste

- Le serveur se lance en simulation sur n'importe quel PC Linux :
  `HOME=<dossier vide> python3 launcher/scaros.py --dry-run --port 8435`, puis on ouvre
  `http://127.0.0.1:8435/`. En simulation, les comptes, les affiches, la musique et les matchs sont des données de
  démo.
- Tests automatiques avec Playwright (Chromium sans écran) : captures en 1920×1080, navigation au clavier comme
  avec la manette (flèches, Entrée, Échap, `m` pour le centre de contrôle).
- Pour Plex, Overseerr, Spotify et le lecteur Plex HTPC, des faux serveurs locaux ont servi aux tests.
- **Maquette en ligne** : on part de `launcher/ui/index.html`, on enlève tout ce qui précède `<title>`, et on
  remplace le bloc `<!-- fonts:local --> … <!-- /fonts:local -->` par un lien Google Fonts vers Inter. Puis on
  publie sur la même adresse (Artifact `8BeEQ5DgmdZgrSjsfsnt5D`).
- **Zips** : `git archive --format=zip -o scaros-os.zip HEAD scaros-os` (depuis la branche perso), pareil depuis
  la branche publique pour `scaros-os-public.zip`.

### Mettre à jour le dépôt public Sc4ros/Scaros-OS (à la main)

1. Décompresser `scaros-os-public.zip` (il contient un dossier `scaros-os`).
2. Sur GitHub, à la racine du dépôt : **Add file › Upload files**, glisser tout le **contenu** du dossier
   `scaros-os` (pas le dossier lui-même), puis **Commit changes**. Ça ne marche que depuis un ordinateur.
3. Pour une petite mise à jour, on peut ne remplacer que les fichiers modifiés : ouvrir le bon dossier sur
   GitHub (par exemple `launcher/ui`), puis **Add file › Upload files** dans ce dossier.
4. Un fichier supprimé dans le projet doit être supprimé à la main sur GitHub (ouvrir le fichier, **…** ›
   **Delete file**).

Claude a proposé de pousser lui-même sur `Sc4ros/Scaros-OS` si tu relies ce dépôt à la session. Ce n'est pas
encore fait.

## 4. Ce qui a été fait, version par version

### Départ (31 octobre – 1er novembre 2026)
- Maquette du menu TV, puis le vrai système : serveur `scaros.py`, menu, télécommande du téléphone,
  `install.sh` pour Debian 13 minimal, écran de démarrage Plymouth, guide d'installation pas à pas.
- (Tu as un moment parlé de Windows 10 ; le choix final est Debian.)
- Portable gardé joignable sur le Wi-Fi (économie d'énergie coupée, SSH activé). Tu te connectes en SSH depuis
  le PC Windows.
- La manette pilote bien l'appli ouverte (focus rendu à l'appli), commande `scaros-update`.
- Son Bluetooth et choix de la sortie audio (Auto, Télé, Haut-parleurs du PC, Bluetooth). Réparé après tes
  essais avec les AirPods (module son Bluetooth manquant, état de connexion réel).
- Spotify retiré à ta demande (il reviendra en 0.8 autrement). Steam Link ajouté (la manette lui est laissée,
  maintenir le bouton Xbox ramène au menu).

### 0.2 – 0.3 (2 novembre)
- Interface façon Apple TV, nouveau logo, texte agrandi pour être lisible de loin, écran Télécommande avec un
  grand QR code (touche Q).
- Wi-Fi depuis le menu (liste des réseaux, clavier AZERTY à l'écran), Bluetooth : reconnexion sans remettre
  les AirPods en appairage, réglages regroupés.
- Télécommande qui tient sur un écran d'iPhone 13, ajoutable à l'écran d'accueil comme une appli.
- 0.3 : centre de contrôle (bouton ≡) par-dessus les applis, touches exécutées dans l'ordre, télé en 1080p par
  défaut, sourdine, réponses plus rapides.
- Twitch ajouté via SmartTwitchTV (l'appli Twitch faite pour les télés). Apparence claire, sombre ou automatique.

### 0.4 (2 novembre)
- AirPlay avec UxPlay (« Scaros » dans la recopie d'écran de l'iPhone), volume et notifications par-dessus les
  applis (`osd.py`), mise à jour de Scaros depuis le menu, météo (Open-Meteo) avec fond animé.
- Accueil façon tvOS avec un paysage qui suit la météo et l'heure. Nouveau logo (ton kit) et animation de
  démarrage façon Apple.
- Corrections AirPlay (messages d'UxPlay bloqués dans un tube), centre de contrôle enfin devant les applis
  (Chromium avait une fenêtre cachée), télécommande joignable en IPv6, aide « Réseau local » sur iPhone.
- Diagnostic réseau (`scaros-diag`) pour la télécommande et AirPlay.
- **À retenir** : la télécommande ne marchait pas sur ton réseau CPL / Wi-Fi 5 GHz. Elle marche depuis que tu
  t'es connecté au TP-Link en 2,4 GHz. Le diagnostic conseille maintenant le 2,4 GHz dans ce cas.

### 0.5 (3 novembre)
- Menus qui défilent jusqu'au bout, Bluetooth mémorisé (plus besoin de réappairer), télécommande qui s'ouvre
  tout de suite.
- Audit du code et corrections (manette, sécurité des commandes, fenêtre du menu), animation de démarrage
  soignée, dépôt GitHub au choix (`source.conf`, `scaros-update --depuis pseudo/dépôt`).

### 0.6 (3 novembre)
- Mode souris : la télécommande devient un trackpad, la manette pilote un curseur par-dessus une appli.
- Télécommande disposée comme une télécommande Apple TV. AirPlay mieux diagnostiqué (Scaros restait invisible
  sur l'iPhone). Le bon fonctionnement d'AirPlay sur ton réseau n'a pas été confirmé ensuite.

### 0.7 (3 – 6 novembre)
- Économiseur d'écran (paysage plein écran, heure, météo), avec un interrupteur Activé / Désactivé.
- Appli **Scaros TV** (`http://176.142.205.87:8791/tv`) ajoutée en premier, avec son logo.
- Tuiles du menu sur deux rangées de 4, plus grandes.
- Création de la **version publique** sans Scaros TV et sans mise à jour automatique, avec README de
  présentation, licence GPL-3.0 et captures, pour `Sc4ros/Scaros-OS`.

### 0.8 (11 novembre) — grande refonte
- Accueil façon Google TV : heure et date, onglets (Reprendre, Ajoutés récemment, Tendances, Mes demandes),
  grandes affiches, bande des applis en bas, police Inter.
- **Plex** (connexion par code plex.tv/link), **Overseerr** (tendances, demandes, nouvelle demande depuis
  l'accueil), **Spotify** (Spotify Connect avec spotifyd + la musique du téléphone via l'API Spotify).
- **Scores sportifs** (ESPN) : Ligue 1, Ligue des champions, Premier League, tes équipes d'abord.
- Écran de veille **Widgets** sur fond noir : horloge, météo, qualité de l'air, UV, musique avec pochette et
  boutons, match. Le style Paysages est gardé.
- Personnalisation : onglets, applis (ordre, masquer), fond, couleur d'accent, 12/24 h, widgets, compétitions,
  équipes. Page Comptes sur la télécommande.
- Tu as signalé « pas de connexion internet » dans la mise à jour, puis dit que c'était réglé.

### 0.9 (11 novembre)
- **App Store** : sélection d'applis TV de Flathub (Kodi, Stremio, FreeTube, Grayjay, VLC, Plexamp, Spotify,
  Shortwave, Moonlight, Greenlight, chiaki-ng, RetroArch, PPSSPP, Pegasus), sites web ajoutés comme des
  applis, désinstallation. Depuis la télé et depuis le téléphone.
- Les affiches Plex ouvrent **Plex HTPC en lecture directe** (sinon l'accueil de Plex HTPC ; Plex Web en
  option).
- Tuiles d'applis du bas bien plus grandes.

### 0.10 (11 novembre)
- Grandes cartes de widgets en haut de l'accueil (match, météo, musique, air et UV), choisies et rangées dans
  **Réglages › Accueil**.
- Onglet **Matchs**, et **fiche du match** (score, buts, cartons, statistiques) avec OK.
- Correction : logos des clubs trop gros sur la vraie télé, rangées trop serrées (plus d'air, bande du bas
  descendue).

### Après la 0.10 (11 novembre)
- Le bouton **Veille** (centre de contrôle, et Réglages › Alimentation › Mettre en veille) affichait encore
  l'ancien écran « Appuie sur une touche pour revenir ». Il lance maintenant l'écran de veille choisi (widgets
  ou paysages), comme la veille automatique. Une touche le ferme.
- Explications pour mettre à jour le dépôt public à la main (voir plus haut).

## 5. Pas encore vérifié sur le vrai portable

Testé seulement en simulation, à surveiller :
- Plex, Overseerr et Spotify avec tes vrais comptes (Overseerr : tu as confirmé que ça marche) ;
- la lecture directe dans Plex HTPC depuis une affiche ;
- les installations de l'App Store (téléchargements Flathub réels) et les icônes des applis installées ;
- Spotify Connect (« Scaros » dans la liste des appareils, le son, les touches) ;
- la fluidité de l'accueil et de l'écran de veille sur le Celeron ;
- AirPlay (recopie et son), la bascule HDMI, le son sur la télé ;
- la reconnexion de la manette après un redémarrage, la fluidité de YouTube et de Twitch.

## 6. Points connus et idées en attente

- **Fichiers de démo en trop dans la version publique** : `launcher/scaros/accounts.json`, `media.json` et
  `settings.json` ont été ajoutés par erreur à la version publique en 0.8 (restes des captures d'écran). Ils
  ne contiennent que des données de démo (« Sam », « demo », Paris), rien de personnel, et Scaros ne les lit
  pas (ses réglages sont dans `~/.local/state/scaros`). À retirer de la branche publique, et du dépôt
  `Sc4ros/Scaros-OS` à la main s'ils y ont été envoyés.
- Le 13 novembre, tu as demandé de réduire le « bloc d'avertissement » et les notifications sur la télé, puis tu
  as annulé (mauvaise conversation). Rien n'a été modifié. Si la demande revient : les notifications du menu
  sont la classe `.toast` de `index.html` (jusqu'à 48 rem de large, texte à 1,6 rem, 1 rem = 1 % de la largeur
  de l'écran), les cartes « message » de l'accueil (compte à relier, serveur muet) sont `.pc.msg` / `.pc-msg`
  (52 rem de large, 17 rem de haut), et les notifications par-dessus les applis sont dessinées par `osd.py`
  (classe `Note`).

## 7. Journal

| Date | Ce qui a été fait | Commit perso | Commit public |
|---|---|---|---|
| 31 oct. – 6 nov. | Versions 0.1 à 0.7 (détail plus haut) | jusqu'à `babf4cd` | jusqu'à `6fa8f1f` |
| 11 nov. | 0.8 : nouvel accueil, veille widgets, Plex, Overseerr, Spotify, scores | `6ad443a` | `0b2935f` |
| 11 nov. | 0.9 : App Store, lecture directe Plex HTPC, grandes tuiles | `dc8935c` | `a43e347` |
| 11 nov. | 0.10 : widgets en haut, onglet Matchs, fiche du match | `284c22c` | `8495566` |
| 11 nov. | Logos des clubs à leur place, plus d'air entre les rangées | `aed487c` | `062f741` |
| 11 nov. | Le bouton Veille affiche l'écran de veille | `802bb30` | `92ceb90` |
| 13 nov. | Demande de réduire les avertissements et notifications : annulée, rien changé | – | – |
| 13 nov. | Création de ce fichier `historique.md` | (ce commit) | – (fichier perso) |
