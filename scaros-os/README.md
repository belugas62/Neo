# Scaros OS

Transforme un vieux PC portable en box TV. Au démarrage, l'animation Scaros se lance, puis
un menu plein écran façon console : YouTube (interface TV), Jellyfin, Plex, Steam Link
pour jouer aux jeux de ton PC sur la télé, et Twitch en interface télé.
Tout se pilote à la manette Xbox ou depuis ton téléphone. Quand tu branches la télé en HDMI,
l'image et le son passent dessus tout seuls.

Prévu pour : PC portable Intel Celeron N4000, 4 Go de RAM, mémoire eMMC (affichée « SD » dans Windows).

> **Attention :** l'installation efface tout le disque du portable, Windows compris.
> Sauvegarde tes fichiers avant de commencer.

Pour voir la maquette sans rien installer, ouvre `launcher/ui/index.html` dans Chrome.

---

## Ce qu'il te faut

- le portable, branché sur secteur, et ton Wi-Fi (nom + mot de passe) ;
- une clé USB de 2 Go ou plus, **qui sera effacée** ;
- ton PC principal (Windows) pour préparer la clé ;
- la manette Xbox et ton téléphone.

Compte environ une heure, surtout des téléchargements.

## 1. Préparer la clé USB (sur ton PC principal)

1. Télécharge Debian 13 « netinst » pour PC 64 bits sur <https://www.debian.org/download>
   (un fichier `debian-13.x.x-amd64-netinst.iso` d'environ 700 Mo).
2. Télécharge Rufus sur <https://rufus.ie> et lance-le.
3. Choisis ta clé USB, puis l'image ISO, puis **Démarrer**. Si Rufus demande un mode
   d'écriture, choisis **« Écrire en mode Image DD »**.

## 2. Démarrer le portable sur la clé

Le plus simple, depuis Windows sur le portable :
**Paramètres > Système > Récupération > Démarrage avancé > Redémarrer maintenant**,
puis **Utiliser un périphérique** et choisis la clé USB.

Sinon, allume le portable en tapotant la touche du menu de démarrage :
HP : Échap puis F9 · Lenovo : F12 (ou le petit bouton Novo) · Acer : F12 · Asus : Échap · Dell : F12.

## 3. Installer Debian (environ 20 minutes)

Choisis **Graphical install**, puis :

| Écran | Quoi choisir |
|---|---|
| Langue, pays, clavier | Français, France, Français |
| Réseau | ton Wi-Fi et son mot de passe |
| Nom de machine | `scaros` (domaine : laisse vide) |
| Mot de passe du superutilisateur (root) | choisis-en un et **note-le** |
| Nouvel utilisateur | par exemple `scaros`, avec un mot de passe |
| Partitionnement | **Assisté – utiliser un disque entier**, puis le disque interne (souvent « MMC/SD card », **pas la clé USB**), puis « Tout dans une seule partition », puis « Terminer » et « Oui » |
| Sélection des logiciels | **décoche** « Environnement de bureau Debian » et « GNOME », **coche** « serveur SSH » et « utilitaires usuels du système » |
| GRUB | Oui, sur le disque interne |

À la fin, retire la clé et laisse redémarrer. Tu arrives sur un écran noir avec `scaros login:`.

## 4. Installer Scaros (environ 30 minutes)

Tu peux taper les commandes directement sur le portable. Plus confortable : depuis ton PC
principal, ouvre un **Terminal Windows** et connecte-toi en SSH pour copier-coller.
Pour connaître l'adresse du portable, connecte-toi dessus et tape `ip -br a`
(une adresse du genre `192.168.1.42`), puis sur ton PC : `ssh scaros@192.168.1.42`.

Ensuite :

```bash
su -
wget https://github.com/belugas62/Neo/archive/scaros-os.tar.gz
tar xf scaros-os.tar.gz
bash Neo-scaros-os/scaros-os/install.sh
```

Le script installe tout, propose d'appairer la manette, puis te dit de redémarrer :

```bash
reboot
```

## 5. À faire une seule fois au premier démarrage

- **Manette Xbox** : si tu ne l'as pas appairée pendant l'installation, va dans
  **Réglages > Manette**, puis maintiens le petit bouton d'appairage (sur le dessus,
  à côté du port USB) jusqu'à ce que le logo Xbox clignote vite.
- **Télécommande téléphone** : bouton **≡** de la manette puis **Télécommande** (ou touche **Q**) :
  un grand QR code s'affiche, scanne-le avec ton téléphone (même Wi-Fi).
  **Sur iPhone, garde-la comme une appli :** ouvre-la de préférence par l'adresse `http://scaros.local:8421/remote`
  (elle ne change pas, contrairement à l'adresse IP), puis dans Safari touche **Partager > Sur l'écran d'accueil**.
  Elle s'ouvre alors en plein écran, sans barre Safari. Sur le pavé, glisse pour te déplacer et touche OK pour valider.
- **YouTube** : à la première ouverture, branche un clavier et appuie sur **Ctrl+O**
  pour ouvrir les réglages de l'appli :
  - active **Hardware Decoding** (la puce Intel décode la vidéo, le processeur respire) ;
  - dans **Filter Video Codecs**, bloque **AV1** : ta puce ne le décode pas, YouTube
    passera au VP9 qu'elle gère très bien ;
  - active **Fullscreen**.

  Pour te connecter à ton compte, YouTube affiche un code à entrer sur
  <https://youtube.com/activate> depuis ton téléphone.
- **Jellyfin** : entre l'adresse de ton serveur, puis dans
  **Paramètres > Affichage > Mode d'affichage**, choisis **TV**.
- **Plex** : connecte-toi avec le code affiché, sur <https://plex.tv/link>.
- **Steam Link** : sur ton PC principal, ouvre Steam, puis **Paramètres > Remote Play** et active
  Remote Play. Au premier lancement de Steam Link sur le portable, choisis ton PC : un code
  s'affiche, entre-le dans Steam sur ton PC. Ton PC doit être allumé avec Steam ouvert.
  Si l'image saccade, baisse la qualité en 720p dans les réglages de Steam Link.
- **Twitch** : c'est SmartTwitchTV, une appli Twitch non officielle faite pour les télés (elle se pilote
  aux flèches, comme le reste). Tu peux regarder sans compte ; pour retrouver tes chaînes suivies,
  ajoute ton compte Twitch dans ses réglages. Si l'image saccade, choisis 720p dans la qualité du lecteur.
  Elle tourne dans le navigateur du PC et se met à jour toute seule : rien à installer.

## Utilisation

| Manette Xbox | Action |
|---|---|
| Croix ou stick gauche | se déplacer |
| A | ouvrir, valider |
| B | retour |
| Bouton Xbox | retour au menu (ferme l'appli en cours) |
| LB / RB | volume − / + |
| Y | lecture / pause |
| ≡ (Menu) | centre de contrôle : volume, son, écran, Bluetooth, veille. Pendant une vidéo, l'appli reste ouverte derrière : B pour y revenir |

Au clavier : flèches, Entrée, Échap ; la **touche Windows** ramène au menu ; **M** ouvre le centre de contrôle ;
**1 à 5** ouvrent directement YouTube, Jellyfin, Plex, Steam Link et Twitch ; **+ / −** pour le volume, **0** coupe le son.

**Télécommande du téléphone :** glisse sur le pavé pour te déplacer, touche OK pour valider. Touche la barre
de volume pour couper ou remettre le son. En haut, elle affiche ce qui est à l'écran.

**Dans Steam Link**, la manette sert au jeu : tous ses boutons vont au jeu, et un appui court sur
le bouton Xbox ouvre le menu de Steam. Pour revenir au menu Scaros, **maintiens le bouton Xbox**
une seconde.

**Casque ou enceinte Bluetooth :** **Réglages > Bluetooth > Rechercher des appareils**, mets
l'appareil en mode appairage, puis choisis-le dans la liste. Le son passe dessus tout seul.
Les fois suivantes, plus besoin du mode appairage : choisis-le dans **Mes appareils** et il se reconnecte.
Pour des AirPods, ouvre le boîtier près du PC ; s'ils sont pris par ton téléphone, déconnecte-les de celui-ci.
Dans **Réglages > Son**, tu choisis où sort le son : **Auto** (l'appareil Bluetooth s'il est
connecté, sinon l'écran utilisé), **Télé**, **Haut-parleurs du PC** ou **Appareil Bluetooth**.

**AirPlay (iPhone, iPad, Mac) :** sur l'iPhone, ouvre le **Centre de contrôle**, touche **Recopie de l'écran**
puis choisis **Scaros** (même Wi-Fi). Le bouton AirPlay d'une appli (vidéo, photos, musique) marche aussi.
**B** ou le **bouton Xbox** arrête la recopie ; **≡** ouvre le centre de contrôle par-dessus. Réglages > AirPlay
pour le désactiver.

**Volume par-dessus les applis :** pendant une vidéo, le volume, le son coupé, la manette ou les AirPods qui se
connectent s'affichent en haut de l'écran, sans interrompre l'appli.

**Fond et météo :** l'accueil est un paysage (montagnes, lac, sapins) dont le ciel suit le temps qu'il fait
et l'heure : soleil, nuages, pluie, neige, orage, brouillard, aube, soir, nuit étoilée. La température s'affiche
avec l'heure dans la pastille en haut à droite. Dans **Réglages > Fond et météo**, choisis un autre fond
(nuit étoilée, aurore, pluie, neige, ou les couleurs de l'appli choisie) ou change de ville. La ville est devinée au premier
démarrage grâce à l'adresse internet de la box ; la météo vient d'Open-Meteo (gratuit, sans compte).

**Apparence :** dans **Réglages > Affichage et veille**, choisis **Sombre**, **Clair**, ou **Automatique**
(clair de 7 h à 19 h, sombre le soir). La taille du texte et la veille automatique se règlent au même endroit.

**Télé :** branche le câble HDMI, l'image et le son passent sur la télé. Tu peux fermer
l'écran du portable, il ne se met pas en veille. Le choix Auto / Écran du PC / Télé est
dans **Réglages > Écran**. L'image est envoyée en **1080p**, même sur une télé 4K : ce petit PC
resterait poussif en 4K. Si les vidéos saccadent, passe en **720p** au même endroit.

## Si quelque chose coince

- **Console de secours :** Ctrl+Alt+F2, puis connecte-toi. Ctrl+Alt+F1 pour revenir à Scaros.
- **Journaux :** dans `~/.local/state/scaros/` (`scaros.log`, `chromium.log`, `airplay.log`, `osd.log`,
  et un fichier par appli, par exemple `youtube.log`). Mise à jour lancée du menu : `journalctl -u scaros-update`. Envoie-les si tu demandes de l'aide.
- **Démarrer sans Scaros** (pour réparer) : `touch ~/.scaros-off` puis `reboot`.
  Pour le remettre : `rm ~/.scaros-off`.
- **Changer de Wi-Fi :** **Réglages > Wi-Fi**, choisis le réseau et tape le mot de passe avec le clavier à l'écran
  (ou un vrai clavier, ou la télécommande du téléphone). En secours : console, `su -` puis `nmtui`.
- **Vérifier le décodage vidéo :** `vainfo` doit lister VP9 et HEVC.
- **La télécommande du téléphone ne s'ouvre pas :** ouvre **≡ > Télécommande** sur la télé et scanne le QR code :
  il donne toujours l'adresse actuelle du PC. L'écran indique aussi le Wi-Fi de Scaros : le téléphone doit être sur le
  même (pas le réseau invité). Si le QR code marche mais pas l'icône de l'écran d'accueil, c'est que l'adresse du PC a
  changé : supprime l'icône et ajoute-la de nouveau depuis la page ouverte par le QR code. Pour qu'elle ne change
  plus, réserve l'adresse du PC dans les réglages de la box (« bail DHCP statique »).
- **Scaros n'apparaît pas dans la recopie de l'écran de l'iPhone :** regarde **Réglages > AirPlay**, qui dit ce qui
  coince. « Prêt » veut dire que Scaros est bien annoncé sur le Wi-Fi : c'est alors la box qui empêche l'iPhone de le
  voir (réseau invité ou option « isolation » des appareils Wi-Fi). Test simple : si `scaros.local:8421/remote` ne
  s'ouvre pas sur l'iPhone, la découverte entre appareils est bloquée. Sinon, « Redémarrer AirPlay », et le journal
  est dans `~/.local/state/scaros/airplay.log`.
- **Une appli met du temps à s'ouvrir :** la première ouverture peut prendre jusqu'à une minute sur ce PC.
  Si elle se ferme aussitôt, Scaros la relance une fois tout seul.
- **Mettre à jour les applis :** **Réglages > Applis > Mettre à jour les applis**.
- **Mettre à jour Scaros :** **Réglages > Mise à jour de Scaros > Mettre à jour Scaros**, à la manette. Scaros
  télécharge la dernière version, l'installe et redémarre l'écran (environ une minute). Les applis et réglages sont gardés.
  À la main : `su -` puis `scaros-update`. **La toute première fois**, fais-le à la main : c'est cette mise à jour
  qui installe AirPlay, l'affichage par-dessus les applis et le bouton de mise à jour du menu.
  Pour tout réinstaller (paquets, applis), retélécharge l'archive (étape 4) et relance `install.sh`.

## Ce qui reste à vérifier sur le vrai portable

Le menu, la télécommande téléphone et la lecture de la manette ont été testés en simulation,
mais pas encore sur ta machine. À surveiller au premier essai :

- la bascule HDMI (les noms des sorties vidéo varient d'un PC à l'autre) et le passage en 1080p ;
- le centre de contrôle par-dessus une appli, puis le retour dans l'appli ;
- AirPlay : la fluidité de la recopie et le son ;
- le volume affiché par-dessus YouTube, Jellyfin ou Steam Link ;
- la mise à jour lancée depuis le menu ;
- le son sur la télé ;
- l'appairage Bluetooth de la manette ;
- la fluidité de YouTube avec le décodage matériel activé, et celle de Twitch.

## Comment c'est fait

```
scaros-os/
├── install.sh              installe tout sur un Debian 13 minimal
├── launcher/
│   ├── scaros.py           serveur local : applis, écrans, son, Bluetooth, manette, télécommande, AirPlay, météo
│   ├── osd.py              volume et notifications affichés par-dessus les applis
│   ├── apps.json           les applis du menu et la commande qui les lance
│   └── ui/
│       ├── index.html      le menu (marche aussi seul, en maquette)
│       ├── remote.html     la télécommande téléphone (http://<adresse du PC>:8421/remote)
│       ├── icons/          icônes de la télécommande (kit du logo Scaros)
│       └── fonts/          police Figtree (licence OFL)
└── system/
    ├── session.sh          session graphique : Openbox, serveur, menu plein écran
    ├── openbox/rc.xml      toutes les fenêtres en plein écran
    └── plymouth/scaros/    écran de démarrage
```

Au démarrage : GRUB (masqué), écran Plymouth Scaros, connexion automatique sur le compte,
puis `startx` lance `session.sh`. Le menu tourne dans Chromium en mode kiosque. Les applis sont des
Flatpak de Flathub : VacuumTube (`rocks.shy.VacuumTube`) pour l'interface TV de YouTube,
Jellyfin Desktop (`org.jellyfin.JellyfinDesktop`), Plex HTPC (`tv.plex.PlexHTPC`) et
Steam Link (`com.valvesoftware.SteamLink`). Twitch est l'appli web SmartTwitchTV
(<https://github.com/fgl27/SmartTwitchTV>), ouverte dans un Chromium plein écran avec son propre profil.

`scaros.py` réserve la manette et la transforme en touches clavier, pour qu'elle marche pareil dans
toutes les applis. Pour tester sans le portable : `python3 launcher/scaros.py --dry-run`, puis
ouvre <http://localhost:8421>. Dans ce mode, rien n'est lancé pour de vrai, et la touche H simule
le branchement de la télé.
