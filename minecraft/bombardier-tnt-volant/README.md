# Bombardier volant à TNT avec duplication — Minecraft Java 26.2

Une machine volante (moteur à slime) qui **avance vers l'est d'un bloc toutes les 0,5 s**
et qui **lâche une TNT amorcée à chaque bloc**, sans jamais consommer sa TNT :
le bloc de TNT de la machine est dupliqué à chaque déplacement.

- Vitesse : 2 blocs/s, soit **2 TNT par seconde** (une par bloc parcouru).
- Coût : 1 seule TNT (réutilisée à l'infini), 14 blocs de slime, 2 pistons collants,
  2 observateurs, 1 rail détecteur + 1 wagonnet, 1 éventail de corail mort.
- Taille : 6 × 5 × 4 blocs, 23 blocs en tout.
- Schématique Litematica prête à charger : [`schematique/bombardier_tnt_volant.litematic`](schematique/bombardier_tnt_volant.litematic).

> ⚠️ **Java Edition uniquement.** La duplication de TNT n'existe pas en Bedrock.
> Elle fonctionne en solo, sur un serveur vanilla ou Fabric, mais **pas sur Paper/Spigot**
> (ils la désactivent par défaut).

> ⚠️ **À tester d'abord en créatif.** Je n'ai pas pu lancer Minecraft pour l'essayer.
> J'ai vérifié l'ordre des déplacements avec un petit simulateur qui reproduit la logique
> des pistons de Java (voir [`simulateur/`](simulateur/)) : 6 duplications sur 6 cycles,
> sans erreur. Mais un simulateur n'est pas le jeu : si quelque chose cloche, voir
> la section *Dépannage*.

---

## 1. Comment ça marche

La machine est faite de **deux modules** qui avancent chacun leur tour :

| Module | Contenu | Rôle |
|---|---|---|
| **Arrière** | observateur, slime, piston collant du haut, verre, **rail détecteur + wagonnet**, slime sous le rail | pousse le module avant ; fournit le courant à la TNT |
| **Avant** | piston collant du bas, slime, observateur, colonne de slime, **TNT**, **corail mort**, bloc de pierre | tire le module arrière ; porte la TNT |

Un cycle (10 ticks = 0,5 s) :

1. L'observateur arrière s'allume → le piston du haut **pousse le module avant** d'un bloc.
2. Pendant cette poussée, le bloc de pierre bouge avant le corail. Privé de support,
   le corail « casse » une fraction de seconde et **envoie une mise à jour à la TNT**.
   Or la TNT est déjà alimentée par le slime situé sous le rail détecteur, sans le savoir
   (on dit qu'elle est en état **BUD**). Elle s'amorce donc : une TNT allumée apparaît
   et tombe. Mais le piston avait déjà « photographié » le bloc de TNT, qui est
   quand même déplacé : c'est la **duplication**. Le corail est déplacé lui aussi
   et réapparaît intact.
3. L'observateur avant, qui vient de bouger, s'allume → le piston du bas sort dans le vide
   puis se rétracte en **tirant le module arrière**.
4. Le module arrière arrive : d'abord le slime (qui se retrouve collé à la TNT), puis
   le rail détecteur, toujours allumé par son wagonnet (poussé par le bloc de verre).
   Le rail alimente le slime, qui alimente la TNT **sans lui envoyer de mise à jour** :
   la TNT est de nouveau en BUD. Retour à l'étape 1.

## 2. Matériel

| Bloc | Quantité |
|---|---|
| Bloc de slime | 14 |
| Piston collant | 2 |
| Observateur | 2 |
| Rail détecteur | 1 |
| Wagonnet (minecart) | 1 |
| TNT | 1 |
| Éventail de corail **mort** (Toucher de soie) | 1 |
| Verre | 1 |
| Pierre (ou n'importe quel bloc plein normal) | 1 |
| Bouton (pour démarrer) | 1 |
| Blocs temporaires (terre, laine…) pour construire en l'air | quelques-uns |

## 3. Où la construire

- **En l'air, au moins 20 blocs au-dessus du sol**, avec rien en dessous : les TNT tombent
  et explosent environ 4 s plus tard, loin sous la machine.
- La machine **vole vers l'est** (+X). Appuie sur F3 et vérifie « Facing: east » pour savoir
  où regarder.
- Elle s'arrête toute seule si elle sort des chunks chargés : suis-la.

## 4. Construction rapide en créatif (commandes)

Le fichier [`commandes-creatif.txt`](commandes-creatif.txt) contient toutes les commandes.

1. Monte à 20 blocs ou plus au-dessus du sol, puis colle les commandes **une par une** dans
   le chat. La première pose un repère invisible (un *marker*) à tes pieds : tu peux ensuite
   bouger entre les commandes, tout est construit par rapport au repère.
   La machine apparaît à l'est de toi, entre 3 et 7 blocs plus haut.
2. Éloigne-toi (pas devant la machine, pas en dessous), puis lance les 2 commandes de
   démarrage : la première pose le bouton qui déclenche l'observateur avant, la seconde
   supprime le repère.

## 4 bis. Schématique Litematica

Fichier : [`schematique/bombardier_tnt_volant.litematic`](schematique/bombardier_tnt_volant.litematic)
(23 blocs + le wagonnet, sans le bouton de démarrage).

1. Copie le fichier dans le dossier `schematics` de ton jeu : `.minecraft/schematics/`,
   ou celui de ton instance si tu utilises un launcher comme Prism ou Modrinth.
2. En jeu : `M` → *Load Schematics* → choisis `bombardier_tnt_volant` → *Load Schematic*.
3. Place-la **en l'air, à 20 blocs ou plus au-dessus du sol**, sans rien en dessous.
   Sans rotation, elle vole vers l'**est**. Tu peux la tourner ou la retourner en miroir
   dans Litematica : elle volera alors dans la nouvelle direction (vérifié dans le
   simulateur pour les 4 rotations et les 2 miroirs).
4. Construis-la :
   - **Créatif** : colle-la avec l'outil de collage de Litematica. Si le wagonnet n'apparaît
     pas (le collage des entités peut être désactivé), pose-le toi-même sur le rail détecteur.
   - **Survie** : sers-toi de l'hologramme comme guide (le mode *Easy Place* aide beaucoup),
     puis pose le wagonnet sur le rail détecteur **en dernier**.
5. Démarre en posant un **bouton** sur la face visage de l'observateur avant
   (voir section 6). Le bouton n'est pas dans la schématique exprès : sinon la machine
   démarrerait pendant le collage, avant d'être finie.

La schématique est construite avec l'écart d'un bloc entre les deux modules : c'est voulu,
ne le « répare » pas. Pour la régénérer : `pip install litemapy`, puis
`python3 schematique/generer_litematic.py`.

## 5. Construction à la main (survie)

### Repère

Les coordonnées sont données par rapport au **slime de référence** (★ ci-dessous) :
`x` = vers l'**est** (sens de vol), `y` = vers le haut, `z` = vers le **sud**.

**Important :** on construit la machine **avec un bloc d'écart** entre les deux modules
(en x = +1 sur les deux couches du haut). C'est voulu : c'est la seule façon de poser le
wagonnet sans allumer la TNT trop tôt.

### Légende

| Code | Bloc | Orientation |
|---|---|---|
| `SL` | bloc de slime | — |
| `P>` | piston collant | tête vers l'**est** (pose-le en regardant vers l'ouest) |
| `<P` | piston collant | tête vers l'**ouest** (pose-le en regardant vers l'est) |
| `<O` | observateur | « visage » vers l'**ouest**, point rouge vers l'est (pose-le en regardant vers l'ouest) |
| `O>` | observateur | « visage » vers l'**est**, point rouge vers l'ouest (pose-le en regardant vers l'est) |
| `VE` | verre | — |
| `RD` | rail détecteur, orienté est-ouest (pose-le en regardant vers l'est) | + wagonnet dessus |
| `TN` | TNT | — |
| `CO` | éventail de corail mort **mural**, posé sur la face ouest de la pierre | pointe vers l'ouest, vers la TNT |
| `PI` | pierre | — |
| `..` | air (rien) | — |

### Couches (vue de dessus, le nord en haut)

```
y = +1 (couche du haut)
        x-1 x+0 x+1 x+2 x+3 x+4
z-1      ..  ..  ..  ..  ..  ..
z+0      ..  P>  ..  SL  O>  ..
z+1      ..  ..  ..  SL  ..  ..
z+2      ..  ..  ..  SL  ..  ..

y = 0
        x-1 x+0 x+1 x+2 x+3 x+4
z-1      ..  ..  ..  ..  ..  ..
z+0      <O  SL★ ..  <P  ..  ..
z+1      ..  ..  ..  ..  ..  ..
z+2      ..  ..  ..  SL  ..  ..

y = -1
        x-1 x+0 x+1 x+2 x+3 x+4
z-1      ..  SL  SL  ..  ..  ..
z+0      ..  SL  ..  ..  ..  ..     <- x+1 doit rester vide (place du wagonnet)
z+1      ..  ..  ..  ..  ..  ..
z+2      ..  ..  ..  SL  ..  ..

y = -2
        x-1 x+0 x+1 x+2 x+3 x+4
z-1      ..  ..  SL  ..  ..  ..
z+0      ..  VE  RD  ..  ..  ..
z+1      ..  ..  ..  ..  ..  ..
z+2      ..  ..  ..  SL  ..  ..

y = -3 (couche du bas)
        x-1 x+0 x+1 x+2 x+3 x+4
z-1      ..  ..  SL  ..  ..  ..
z+0      ..  ..  SL  ..  ..  ..
z+1      ..  ..  ..  TN  CO  PI
z+2      ..  ..  ..  SL  ..  ..
```

Ce qui appartient à quel module :

- **Arrière** : tout ce qui est en `x ≤ +1` et `z ≤ 0` (observateur `<O`, slime ★,
  piston `P>`, slimes en z-1, verre, rail détecteur, slime sous le rail).
- **Avant** : tout ce qui est en `x ≥ +2` (piston `<P`, slimes, observateur `O>`,
  colonne de slime en z+2, TNT, corail, pierre).

### Ordre de pose conseillé

1. Pose tous les **slimes**, le **verre** et la **pierre** (aide-toi de blocs temporaires).
2. Pose les 2 **pistons collants** (attention au sens, voir la légende).
3. Pose les 2 **observateurs** (attention au sens). Ne pose **rien** devant leur visage.
4. Pose le **rail détecteur** sur son slime, puis la **TNT**, puis le **corail mort** sur la
   face ouest de la pierre.
5. **Retire tous les blocs temporaires**. Aucun bloc étranger ne doit toucher la machine :
   un bloc collé au slime serait emporté, ou bloquerait tout.
6. Pose le **wagonnet** sur le rail détecteur : le rail s'allume. La TNT ne doit **pas**
   s'allumer. Si elle s'allume, c'est qu'un bloc est mal placé.

## 6. Démarrer et arrêter

- **Démarrer** : pose un **bouton** sur la face « visage » (côté est) de l'observateur avant
  `O>`, à la case x+4, y+1, z+0. L'observateur réagit à la pose : le module arrière est tiré,
  puis la machine part vers l'est. La première TNT tombe moins d'une seconde après. Le bouton
  est détruit automatiquement au premier mouvement.
- **Arrêter** : place un bloc d'**obsidienne** juste devant (à l'est de) la **pierre** qui
  tient le corail. La machine ne peut plus avancer et s'arrête, les deux modules collés.
  Ne la mets **pas** devant l'observateur avant : il réagirait à la pose et ferait reculer
  la machine.
- **Repartir** après un arrêt : retire l'obsidienne, puis pose un bouton sur la face visage
  (côté ouest) de l'observateur **arrière** `<O`.
- **Règle à respecter** : le moteur peut voler dans les deux sens. On ne déclenche
  l'observateur **avant** que s'il y a un bloc d'écart entre les modules (construction),
  et l'observateur **arrière** que si les modules se touchent (après un arrêt). Sinon la
  machine part vers l'ouest et le duplicateur ne marche plus.

## 7. Dépannage

| Symptôme | Cause probable |
|---|---|
| Rien ne bouge | Le bouton n'est pas sur la face visage de `O>`, ou un bloc temporaire bloque encore. |
| La machine avance mais aucune TNT ne tombe | Le wagonnet n'est plus sur le rail (le rail doit être allumé), ou le corail n'est pas un éventail **mort** posé sur la face ouest de la pierre, ou tu es sur un serveur Paper/Spigot. |
| La TNT s'allume dès que tu poses le wagonnet | La machine n'a pas été construite avec l'écart d'un bloc, ou la TNT touche le slime sous le rail. |
| La machine part vers l'ouest | Elle a été démarrée par l'observateur arrière. Reconstruis-la avec l'écart et démarre par l'avant. |
| La machine s'arrête au bout d'un moment | Elle a quitté les chunks chargés, ou elle a heurté un obstacle. |
| Des TNT explosent sur la machine | Elle vole trop bas. Monte-la à 20 blocs ou plus au-dessus du sol. |

Une mise à jour de Minecraft peut changer ces mécaniques : la duplication de TNT a déjà
été retirée puis remise par le passé.

## 8. Vérification (simulateur)

Le dossier [`simulateur/`](simulateur/) reproduit en Python la logique des pistons de Java :
l'ordre exact des blocs déplacés (`PistonStructureResolver`), le corail qui casse quand
son support bouge, l'ordre d'arrivée des blocs et l'état BUD de la TNT. Il simule aussi
la trajectoire des TNT qui tombent.

```bash
cd simulateur
python3 design.py 6
```

Résultat : `DUPLICATIONS : 6 en 6 cycles`, `ERREURS : aucune`.
Les avertissements signalent qu'une TNT qui tombe peut être légèrement poussée, voire
projetée vers l'avant par un slime si elle dérive. Elle finit quand même par tomber sous
la machine.

Limites : le simulateur ne reproduit pas la physique du wagonnet. Il suppose que le verre
pousse bien le wagonnet sur le rail à chaque déplacement.

## Sources

- [Minecraft Wiki — Tutorial: Large scale TNT duplication](https://minecraft.wiki/w/Tutorial:Large_scale_TNT_duplication) (TNT en BUD, rôle du corail)
- [Minecraft Wiki — Tutorial: Block and item duplication](https://minecraft.wiki/w/Tutorial:Block_and_item_duplication)
- [Technical Minecraft Wiki — TNT duping without entities](https://technical-minecraft.fandom.com/wiki/Tnt_duping_without_entities)
- [TNT Dupers (random-j-farmer)](https://random-j-farmer.github.io/random-minecraft/2021/01/03/tnt-dupers.html) (le corail doit bouger avant la TNT)
- [YouTube — How to build a TNT Duper in Minecraft 26.2](https://www.youtube.com/watch?v=WXMuaw8Ida8) (la duplication marche en 26.2)
- [BuildPaste — Flying TNT Duper](https://buildpaste.net/build/flying-tnt-duper-0nw4ONMl1B52LhBnnbUU) (autre modèle publié par la communauté, lui aussi avec rail détecteur + wagonnet)
- [Tutorials/Flying machines (theminecraftwiki.com)](https://theminecraftwiki.com/wiki/tutorials/flying-machines/) (moteur 2 observateurs / 2 slimes / 2 pistons collants)
