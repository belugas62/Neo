# Scaros OS

Transforme un vieux PC portable en box TV. Au démarrage, l'animation Scaros se lance, puis
un accueil façon Google TV : l'heure, la météo et les scores en haut, tes films et séries Plex à reprendre,
les derniers ajouts et les tendances d'Overseerr en grandes affiches, et la bande des applis en bas : Scaros TV,
YouTube (interface TV), Jellyfin, Plex, Steam Link pour jouer aux jeux de ton PC sur la télé, et Twitch.
Quand personne n'y touche, un écran de veille en widgets affiche une grande horloge, la météo et la musique
Spotify en cours.
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
- **Scaros TV** : la première tuile du menu (chaîne 1 de la télécommande). Elle ouvre
  `http://176.142.205.87:8791/tv` en plein écran, avec son propre profil de navigateur (une connexion
  à un compte reste gardée), et se pilote aux flèches, à OK et à Retour. Si le serveur ne répond pas
  (éteint, ou pas d'internet), Scaros le dit au lieu d'afficher une page d'erreur. Pour changer l'adresse :
  `launcher/apps.json`, ligne `"web"` de `scarostv`, puis mise à jour (ou `/opt/scaros/launcher/apps.json`
  sur le PC, puis redémarrer).
- **Jellyfin** : entre l'adresse de ton serveur, puis dans
  **Paramètres > Affichage > Mode d'affichage**, choisis **TV**.
- **Plex** : connecte-toi avec le code affiché, sur <https://plex.tv/link>.
- **Comptes de l'accueil** (**Réglages > Comptes**) : ce qui remplit l'accueil et l'écran de veille.
  - **Plex** : **Connecter Plex** affiche un code ; tape-le sur <https://plex.tv/link> (téléphone ou PC).
    Scaros trouve ton serveur tout seul. Les onglets **Reprendre** et **Ajoutés récemment** se remplissent.
    Une affiche ouvre l'appli **Plex** (Plex HTPC) et lance directement le film ou l'épisode, là où tu t'étais arrêté
    (comme « Lire sur… » depuis l'appli Plex du téléphone). Si la lecture directe ne marche pas chez toi, Plex reste
    ouvert sur son accueil ; tu peux alors choisir **Plex Web** dans **Réglages > Comptes > Plex** (la fiche du film
    dans le navigateur, où il faudra te connecter une fois).
  - **Overseerr** (ou Jellyseerr) : sur la télécommande du téléphone, touche le bonhomme en haut, puis colle
    l'adresse d'Overseerr (`http://192.168.x.x:5055`) et sa clé API (**Overseerr > Paramètres > Général**).
    Les onglets **Tendances** et **Mes demandes** se remplissent ; OK sur une tendance que tu n'as pas la demande.
  - **Spotify** (compte Premium) : deux choses, qui se complètent.
    **Spotify Connect** marche tout seul : dans l'appli Spotify du téléphone, touche l'icône des appareils et
    choisis **Scaros** ; le son sort par la télé (ou le casque Bluetooth branché à Scaros).
    Pour que l'écran de veille affiche aussi ce que joue ton **téléphone**, crée une fois une appli gratuite :
    sur <https://developer.spotify.com/dashboard>, **Create app**, Redirect URI
    `http://127.0.0.1:8421/api/spotify/callback`, coche **Web API**, puis copie le **Client ID** dans la télécommande
    (bonhomme en haut > Spotify). Termine par **Se connecter sur la télé** (Réglages > Comptes), ou connecte-toi
    sur le téléphone : Safari affiche ensuite une page introuvable, c'est normal ; copie son adresse et colle-la
    dans la télécommande.
  - **Sport** (**Réglages > Sport**) : choisis tes équipes (**Ajouter une équipe**) et les compétitions suivies
    (Ligue 1, Ligue des champions et Premier League par défaut).
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
**1 à 6** ouvrent directement Scaros TV, YouTube, Jellyfin, Plex, Steam Link et Twitch ; **+ / −** pour le volume, **0** coupe le son.

**Télécommande du téléphone :** glisse sur le pavé pour te déplacer, touche OK pour valider. Sous le pavé, les
touches sont rangées comme sur une télécommande Apple TV : **Retour**, **Accueil**, **Lecture** et **Menu** à gauche,
et le **volume** en bascule verticale à droite (**+** en haut, **−** en bas, reste appuyé pour aller vite ; touche le
chiffre du milieu pour couper ou remettre le son). En haut, elle affiche ce qui est à l'écran.

**Mode souris** (pour une appli ou un site qui se pilote mal aux flèches) : sur la télécommande, touche **Souris**
au-dessus du pavé. Le pavé devient un trackpad : glisse un doigt pour déplacer le curseur (plus tu vas vite, plus il
va loin), **touche** pour cliquer, **appui long** ou **touche à deux doigts** pour le clic droit, **glisse à deux
doigts** pour faire défiler. **Flèches** revient au pavé habituel. À la manette, active-le avec la tuile **Souris** du
centre de contrôle (**≡**), ou dans **Réglages > Manette** : par-dessus une appli, le **stick gauche** déplace le
curseur, **A** clique (maintenu : glisser), **X** fait un clic droit et le **stick droit** fait défiler ; B, la croix,
LB/RB, ≡ et le bouton Xbox gardent leur rôle. Dans le menu de Scaros, la manette garde les flèches. Le mode est le
même pour la télécommande et la manette : l'activer d'un côté l'active de l'autre.

**Dans Steam Link**, la manette sert au jeu : tous ses boutons vont au jeu, et un appui court sur
le bouton Xbox ouvre le menu de Steam. Pour revenir au menu Scaros, **maintiens le bouton Xbox**
une seconde.

**Casque ou enceinte Bluetooth :** **Réglages > Bluetooth > Rechercher des appareils**, mets
l'appareil en mode appairage, puis choisis-le dans la liste. Le son passe dessus tout seul.
Les fois suivantes, plus besoin du mode appairage : choisis-le dans **Mes appareils** et il se reconnecte
(la manette, elle, se reconnecte seule quand tu l'allumes). Les appareils restent mémorisés après un redémarrage :
Scaros rallume le Bluetooth s'il s'est éteint et met les appareils appairés « de confiance ».
Pour des AirPods, ouvre le boîtier près du PC ; s'ils sont pris par ton téléphone, déconnecte-les de celui-ci.
N'utilise **Oublier cet appareil** que s'il ne se connecte plus du tout.
Dans **Réglages > Son**, tu choisis où sort le son : **Auto** (l'appareil Bluetooth s'il est
connecté, sinon l'écran utilisé), **Télé**, **Haut-parleurs du PC** ou **Appareil Bluetooth**.

**AirPlay (iPhone, iPad, Mac) :** sur l'iPhone, ouvre le **Centre de contrôle**, touche **Recopie de l'écran**
puis choisis **Scaros** (même Wi-Fi). Le bouton AirPlay d'une appli (vidéo, photos, musique) marche aussi.
**B** ou le **bouton Xbox** arrête la recopie ; **≡** ouvre le centre de contrôle par-dessus. Réglages > AirPlay
pour le désactiver.

**Volume par-dessus les applis :** pendant une vidéo, le volume, le son coupé, la manette ou les AirPods qui se
connectent s'affichent en haut de l'écran, sans interrompre l'appli.

**Accueil :** en haut à gauche l'heure et la date, puis les onglets **Reprendre**, **Ajoutés récemment**,
**Tendances** et **Mes demandes** ; à droite le score du moment, la météo et quatre boutons ronds (centre de contrôle,
AirPlay, Wi-Fi, Réglages). Au milieu, les affiches de l'onglet choisi : la première en grand, avec la progression de
ce que tu as commencé et l'état des demandes (« Disponible », « Téléchargement · 62 % »…). En bas, la bande des applis.
**Haut** et **bas** passent d'une rangée à l'autre ; **gauche** et **droite** avancent dans la rangée ; un onglet
s'affiche dès que tu le choisis. Le fond reprend l'affiche choisie, floutée. Tant qu'un compte n'est pas relié,
l'onglet le dit ; OK ouvre **Réglages > Comptes**.

**App Store :** la tuile **App Store**, au bout de la bande des applis (ou **Réglages > App Store**), installe en un appui
des applis qui se pilotent bien à la télé, venues de Flathub : Kodi, Stremio, FreeTube, Grayjay, VLC, Plexamp, Spotify,
Shortwave (radios), Moonlight, Greenlight (Xbox Cloud Gaming), chiaki-ng (PlayStation à distance), RetroArch, PPSSPP,
Pegasus. L'appli arrive au bout de la bande des applis ; **Gérer** permet de la désinstaller. Les applis marquées
**Manette** lisent la manette elles-mêmes (maintiens le bouton Xbox pour revenir au menu) ; celles marquées **Souris**
se pilotent mieux avec le mode souris. **Ajouter un site web** en fait une appli (un nom, une adresse), ouverte en plein
écran comme Twitch ; Netflix, Disney+ et Prime Video ne marchent pas (leurs protections bloquent la lecture sous Linux).
Tout se fait aussi depuis la télécommande du téléphone (le sac en haut), plus pratique pour taper une adresse.
Les touches **1 à 9** ouvrent les applis dans l'ordre de la bande ; la télécommande du téléphone garde les 6 premières.

**Personnaliser :** **Réglages > Accueil** : les onglets affichés et leur ordre, les applis de la bande et leur ordre
(OK affiche ou masque, gauche et droite déplacent), le fond (**Affiche** du programme choisi, **Paysage** animé qui suit
la météo, ou **Uni**, le plus léger) et la couleur d'accent. **Réglages > Affichage** : sombre, clair ou automatique pour
les réglages, la taille du texte, l'horloge sur **24 h** ou **12 h**, et la veille automatique. **Réglages > Fond et
météo** : le paysage (temps réel, nuit étoilée, aurore, pluie, neige) et la ville de la météo, devinée au premier
démarrage grâce à l'adresse internet de la box ; la météo et la qualité de l'air viennent d'Open-Meteo (gratuit,
sans compte).

**Scores :** en haut à droite, le match de tes équipes passe en premier (en direct, du jour, sinon le prochain), puis
les autres matchs en cours des compétitions suivies, un à la fois. Ils viennent d'ESPN, sans compte.

**Écran de veille :** après 5 minutes sans toucher au menu. Par défaut, le style **Widgets**, sur fond noir : une grande
horloge à aiguilles, l'heure en chiffres fins, la météo (lever et coucher du soleil), la qualité de l'air, l'indice UV,
la **musique en cours** (pochette, titre, progression, ◀◀ ⏯ ▶▶ : Spotify sur Scaros ou sur ton téléphone, ou la
pochette de la musique envoyée en AirPlay) et le match de ton équipe. L'ensemble glisse de quelques pixels chaque
minute pour ne pas marquer la télé. **Lecture** (Y à la manette, ou la touche Lecture de la télécommande) met en pause
ou relance la musique sans fermer l'écran de veille ; n'importe quelle autre touche le ferme sans rien lancer. Le style
**Paysages** montre à la place le paysage de Scaros en plein écran, qui change d'ambiance chaque minute. Tout se règle
dans **Réglages > Écran de veille** : activé ou non, le style, les widgets affichés, le délai (2, 5, 10 ou 15 minutes),
et **Voir l'écran de veille** pour le montrer tout de suite. Il ne passe jamais par-dessus une appli, une recopie
AirPlay, une mise à jour ou un diagnostic.

**Musique dans le menu :** la touche **Lecture** met aussi en pause ou relance la musique Spotify quand le menu est
à l'écran.

**Télé :** branche le câble HDMI, l'image et le son passent sur la télé. Tu peux fermer
l'écran du portable, il ne se met pas en veille. Le choix Auto / Écran du PC / Télé est
dans **Réglages > Écran**. L'image est envoyée en **1080p**, même sur une télé 4K : ce petit PC
resterait poussif en 4K. Si les vidéos saccadent, passe en **720p** au même endroit.

## Si quelque chose coince

- **Console de secours :** Ctrl+Alt+F2, puis connecte-toi. Ctrl+Alt+F1 pour revenir à Scaros.
- **Journaux :** dans `~/.local/state/scaros/` (`scaros.log`, `chromium.log`, `airplay.log`, `spotifyd.log`, `osd.log`,
  et un fichier par appli, par exemple `youtube.log`). Mise à jour lancée du menu : `journalctl -u scaros-update`. Envoie-les si tu demandes de l'aide.
- **Démarrer sans Scaros** (pour réparer) : `touch ~/.scaros-off` puis `reboot`.
  Pour le remettre : `rm ~/.scaros-off`.
- **Changer de Wi-Fi :** **Réglages > Wi-Fi**, choisis le réseau et tape le mot de passe avec le clavier à l'écran
  (ou un vrai clavier, ou la télécommande du téléphone). En secours : console, `su -` puis `nmtui`.
- **Vérifier le décodage vidéo :** `vainfo` doit lister VP9 et HEVC.
- **Le téléphone n'arrive pas à joindre Scaros (télécommande ou AirPlay) : lance le diagnostic réseau.**
  **Réglages > Wi-Fi > Diagnostic réseau** (aussi sur l'écran Télécommande et dans AirPlay). Scaros vérifie sa
  connexion (économie d'énergie Wi-Fi, adresse utilisée par un autre appareil, pare-feu, box), puis écoute 30 secondes
  pendant que tu ouvres la télécommande puis la **Recopie de l'écran** sur le téléphone : il voit si les demandes
  arrivent jusqu'à lui (connexion de la télécommande, recherche AirPlay), et dit où elles se perdent. Pour copier le rapport, ouvre `http://<adresse de Scaros>:8421/diag` sur un PC.
  À la main : `su -` puis `scaros-diag`. Si « rien n'est arrivé », c'est le réseau entre le téléphone et le PC :
  souvent un CPL ou un répéteur Wi-Fi qui isole les appareils. Le plus sûr est alors de brancher le PC par câble.
- **CPL ou répéteur Wi-Fi :** mets Scaros sur le Wi-Fi **2,4 GHz** du boîtier (souvent le même nom, sans « _5G »).
  Vu en vrai avec un CPL TP-Link : sur son 5 GHz, l'iPhone voyait Scaros mais ne pouvait ni ouvrir la télécommande
  ni lancer AirPlay ; sur son 2,4 GHz, tout marche. Le diagnostic réseau le signale.
- **La télécommande du téléphone ne s'ouvre pas (« Ce site est inaccessible ») :** sur iPhone, ouvre-la dans **Safari**.
  Les autres navigateurs (Brave, Chrome, Firefox…) ne peuvent joindre les appareils de la maison qu'avec l'accès
  « Réseau local » : **Réglages > Confidentialité et sécurité > Réseau local**, active ton navigateur. Attention, l'appareil
  photo ouvre les QR codes dans le navigateur par défaut.
- **La télécommande met du temps à s'ouvrir :** fais la mise à jour de Scaros (elle coupe l'économie d'énergie
  Wi-Fi du PC, qui le rendait lent à répondre). La page est ensuite gardée par le téléphone : elle s'affiche tout de
  suite et se connecte en une seconde ; après une mise à jour de Scaros, elle se recharge seule.
- **Toujours rien :** ouvre **≡ > Télécommande** sur la télé et scanne le QR code :
  il donne toujours l'adresse actuelle du PC. L'écran indique aussi le Wi-Fi de Scaros : le téléphone doit être sur le
  même (pas le réseau invité). Si le QR code marche mais pas l'icône de l'écran d'accueil, c'est que l'adresse du PC a
  changé : supprime l'icône et ajoute-la de nouveau depuis la page ouverte par le QR code. Pour qu'elle ne change
  plus, réserve l'adresse du PC dans les réglages de la box (« bail DHCP statique »).
- **Scaros n'apparaît pas dans la recopie de l'écran de l'iPhone (alors que la télécommande marche) :** regarde
  **Réglages > AirPlay**, qui dit ce qui coince. « Prêt » veut dire que Scaros s'annonce bien sur le Wi-Fi. La
  télécommande passe par une connexion directe, mais l'iPhone trouve AirPlay grâce à des annonces envoyées à tout le
  réseau (Bonjour, en multicast), que beaucoup de CPL et de répéteurs filtrent, surtout entre leurs bandes 2,4 et
  5 GHz. Donc :
  1. mets l'iPhone sur le même Wi-Fi **et la même bande** que Scaros (le 2,4 GHz du même boîtier) ;
  2. sur l'iPhone, coupe puis rallume le Wi-Fi (il oublie les appareils qu'il avait en mémoire) ;
  3. toujours rien : lance **Réglages > Wi-Fi > Diagnostic réseau** et, pendant l'écoute, ouvre la télécommande
     puis **Recopie de l'écran** sur l'iPhone. La partie « AirPlay » du rapport dit si la recherche de l'iPhone
     arrive jusqu'à Scaros, si Scaros lui répond, ou si le réseau bloque toutes les annonces ;
  4. dans les réglages du CPL ou de la box, désactive l'isolation des appareils et le filtrage multicast (IGMP).

  Sinon, « Redémarrer AirPlay », et le journal est dans `~/.local/state/scaros/airplay.log`.
- **Une appli met du temps à s'ouvrir :** la première ouverture peut prendre jusqu'à une minute sur ce PC.
  Si elle se ferme aussitôt, Scaros la relance une fois tout seul.
- **Les affiches ne s'affichent pas :** **Réglages > Comptes** dit ce qui coince (serveur Plex éteint, clé
  Overseerr refusée…). Si ton serveur Plex a changé d'adresse, **Reconnecter Plex**. En dernier recours, la
  télécommande permet de taper l'adresse et le jeton à la main (Comptes > Plex > « Adresse et jeton à la main »).
- **Scaros n'apparaît pas dans l'appli Spotify :** il faut un compte Premium et le même Wi-Fi.
  **Réglages > Comptes > Spotify Connect** doit être activé ; sinon regarde `spotifyd.log`.
- **L'écran de veille n'affiche pas la musique du téléphone :** l'appli Spotify (developer.spotify.com) doit avoir
  exactement la Redirect URI `http://127.0.0.1:8421/api/spotify/callback`. Pour la musique jouée par Scaros
  lui-même (Spotify Connect), rien à faire.
- **Une affiche ouvre Plex mais pas le film :** la lecture directe a besoin que Plex HTPC accepte les ordres de
  lecture (comme quand tu lances un film depuis l'appli Plex du téléphone) ; regarde dans les réglages de Plex HTPC
  qu'il est bien visible comme lecteur sur le réseau. Sinon, choisis **Plex Web** dans **Réglages > Comptes > Plex**.
- **Une appli de l'App Store ne s'installe pas :** il faut internet, et de la place sur le disque (certaines applis
  téléchargent plusieurs centaines de Mo la première fois). Le détail est dans `scaros.log`.
- **Mettre à jour les applis :** **Réglages > Applis > Mettre à jour les applis**.
- **Mettre à jour Scaros :** **Réglages > Mise à jour de Scaros > Mettre à jour Scaros**, à la manette. Scaros
  télécharge la dernière version, l'installe et redémarre l'écran (environ une minute). Les applis et réglages sont gardés.
  À la main : `su -` puis `scaros-update`. **La toute première fois**, fais-le à la main : c'est cette mise à jour
  qui installe AirPlay, l'affichage par-dessus les applis et le bouton de mise à jour du menu.
  Le dépôt GitHub suivi est indiqué dans **Réglages > Mise à jour de Scaros** (fichier `/etc/scaros/source.conf`).
  Pour tout réinstaller (paquets, applis), retélécharge l'archive (étape 4) et relance `install.sh`.

## Mettre Scaros sur ton propre dépôt GitHub

Scaros peut vivre dans n'importe quel dépôt GitHub **public** : les mises à jour du menu viendront alors de lui.

1. Sur github.com, crée un dépôt vide, par exemple `scaros-os`.
2. Mets-y le contenu de ce dossier, à la racine du dépôt (`install.sh`, `update.sh`, `source.conf`, `launcher/`,
   `system/`…). Le plus simple : **Add file > Upload files** sur la page du dépôt, puis glisse tous les fichiers et
   dossiers. Ou en ligne de commande :

   ```bash
   cd scaros-os
   git init -b main
   git add .
   git commit -m "Scaros OS"
   git remote add origin https://github.com/TON-PSEUDO/scaros-os.git
   git push -u origin main
   ```
3. Dans `source.conf`, mets `REPO=TON-PSEUDO/scaros-os` et laisse `BRANCH=` vide (branche principale), puis
   enregistre ce changement dans le dépôt. Les prochaines installations suivront ton dépôt.
4. Sur le PC déjà installé, une seule fois : `su -` puis `scaros-update --depuis TON-PSEUDO/scaros-os`.
   Ensuite le bouton **Mettre à jour** du menu prend tes versions.

Pour installer Scaros sur un autre PC depuis ton dépôt (étape 4) :

```bash
su -
wget -O scaros.tar.gz https://github.com/TON-PSEUDO/scaros-os/archive/HEAD.tar.gz
tar xf scaros.tar.gz
bash scaros-os-*/install.sh
```

## Ce qui reste à vérifier sur le vrai portable

Le menu, la télécommande téléphone et la lecture de la manette ont été testés en simulation,
mais pas encore sur ta machine. À surveiller au premier essai :

- la bascule HDMI (les noms des sorties vidéo varient d'un PC à l'autre) et le passage en 1080p ;
- le centre de contrôle par-dessus une appli, puis le retour dans l'appli ;
- le mode souris : la vitesse du curseur au téléphone et à la manette (testée ici avec une souris simulée,
  pas avec la vraie souris virtuelle du portable) ;
- AirPlay : la fluidité de la recopie et le son ;
- le volume affiché par-dessus YouTube, Jellyfin ou Steam Link ;
- la fluidité de l'accueil (fond flouté, défilement des affiches) et de l'écran de veille sur la télé ;
- Plex, Overseerr et Spotify avec tes vrais comptes (testés ici contre des serveurs simulés) ;
- la lecture directe dans Plex HTPC depuis une affiche (testée contre un lecteur Plex simulé) ;
- les installations de l'App Store (téléchargements Flathub réels) et les icônes des applis installées ;
- Spotify Connect (spotifyd) : « Scaros » dans la liste des appareils, le son, et les touches lecture et suivant ;
- la mise à jour lancée depuis le menu ;
- le son sur la télé ;
- l'appairage Bluetooth de la manette, et sa reconnexion toute seule après un redémarrage ;
- la fluidité de YouTube avec le décodage matériel activé, et celle de Twitch.

## Comment c'est fait

```
scaros-os/
├── install.sh              installe tout sur un Debian 13 minimal
├── update.sh               mise à jour (commande scaros-update, lancée aussi depuis le menu)
├── source.conf             le dépôt GitHub d'où viennent les mises à jour
├── launcher/
│   ├── scaros.py           serveur local : applis, écrans, son, Bluetooth, manette, télécommande, AirPlay, météo
│   ├── media.py            accueil et veille : Plex, Overseerr, Spotify, pochette AirPlay, scores (ESPN)
│   ├── store.py            le catalogue de l'App Store (applis Flathub qui se pilotent bien à la télé)
│   ├── osd.py              volume et notifications affichés par-dessus les applis
│   ├── apps.json           les applis du menu et la commande qui les lance
│   └── ui/
│       ├── index.html      le menu (marche aussi seul, en maquette)
│       ├── remote.html     la télécommande téléphone (http://<adresse du PC>:8421/remote)
│       ├── icons/          icônes de la télécommande (kit du logo Scaros)
│       └── fonts/          police Inter (licence OFL)
└── system/
    ├── session.sh          session graphique : Openbox, serveur, menu plein écran
    ├── scaros-diag.sh      diagnostic réseau (commande scaros-diag, lancé aussi depuis le menu)
    ├── openbox/rc.xml      toutes les fenêtres en plein écran
    └── plymouth/scaros/    écran de démarrage
```

Au démarrage : GRUB (masqué), écran Plymouth Scaros, connexion automatique sur le compte,
puis `startx` lance `session.sh`. Le menu tourne dans Chromium en mode kiosque. Les applis sont des
Flatpak de Flathub : VacuumTube (`rocks.shy.VacuumTube`) pour l'interface TV de YouTube,
Jellyfin Desktop (`org.jellyfin.JellyfinDesktop`), Plex HTPC (`tv.plex.PlexHTPC`) et
Steam Link (`com.valvesoftware.SteamLink`). Scaros TV et Twitch (l'appli web SmartTwitchTV,
<https://github.com/fgl27/SmartTwitchTV>) sont des applis web, ouvertes dans un Chromium plein écran avec
leur propre profil ; Scaros vérifie que leur adresse répond avant de les ouvrir.

`scaros.py` réserve la manette et la transforme en touches clavier, pour qu'elle marche pareil dans
toutes les applis. En mode souris, il crée une souris virtuelle (uinput, avec repli sur `xdotool`) ; la télécommande
lui envoie ses gestes par une connexion WebSocket gardée ouverte (`/api/mouse`).

`media.py` remplit l'accueil : Plex (connexion par code `plex.tv/link`, puis Reprendre et Ajoutés récemment),
Overseerr (tendances, demandes), Spotify (API Web en PKCE, sans secret, et spotifyd par MPRIS avec `playerctl`), la
pochette AirPlay d'UxPlay (`-ca`) et les scores d'ESPN. Les jetons et les clés restent dans
`~/.local/state/scaros/accounts.json` (lisible par le compte de la box seulement) : le menu et la télécommande ne
reçoivent que des titres et des adresses d'images servies par Scaros (`/api/img/…`, gardées en cache dans
`~/.cache/scaros/img`). Pour tester sans le portable : `python3 launcher/scaros.py --dry-run`, puis
ouvre <http://localhost:8421>. Dans ce mode, rien n'est lancé pour de vrai, et la touche H simule
le branchement de la télé.
