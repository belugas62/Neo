# Rated Chats

Transforme des captures de conversation en vidéo TikTok façon « Game Review » d'échecs : chaque message reçoit une icône (Brillant, Meilleur coup, Gaffe…), avec une barre d'évaluation et un écran de bilan (précision, Elo estimé).

## Lancer le logiciel

1. Télécharge `RatedChats.html`.
2. Double-clique dessus : il s'ouvre dans Chrome ou Edge. Rien à installer.
3. Clique sur ⚙️, puis colle ta clé Groq gratuite :
   - va sur [console.groq.com/keys](https://console.groq.com/keys) ;
   - connecte-toi (pas de carte bancaire) ;
   - clique sur **Create API Key**.

La clé reste enregistrée dans ton navigateur, sur ton PC.

## Utilisation

1. **Captures** : glisse tes captures d'écran (ou Ctrl+V), puis clique sur « Lire les captures avec l'IA ».
2. **Conversation** : vérifie les messages lus, corrige si besoin et ajoute la photo du contact.
3. **Analyse IA** : clique sur « Analyser la conversation ». Clique sur une icône pour la changer à la main.
4. **Vidéo** : « Exporter la vidéo » produit un MP4 1080×1920 (9:16). « Exporter l'image » enregistre ce que montre l'aperçu.

Sans capture, « Coller du texte » accepte une ligne par message : `G:` pour l'autre personne (à gauche), `D:` pour toi (à droite).

## IA utilisée (gratuite)

| Étape | Modèle Groq par défaut |
|---|---|
| Lecture des captures | `qwen/qwen3.8-27b` (vision) |
| Analyse de la conversation | `openai/gpt-oss-120b` |

Ces deux modèles se changent dans ⚙️. Si Groq retire un modèle, « Tester la clé » affiche la liste des modèles disponibles.

L'offre gratuite de Groq a des limites par minute. Si tu les atteins, le logiciel attend et réessaie tout seul.

## Notes

- L'export vidéo utilise [Mediabunny](https://mediabunny.dev), chargé depuis jsDelivr au moment de l'export. Il faut donc une connexion internet, comme pour l'IA. Si l'encodeur rapide n'est pas disponible, la vidéo est enregistrée en temps réel : garde alors la fenêtre au premier plan.
- Les icônes sont dessinées par le logiciel. Elles s'inspirent du style des analyses d'échecs mais ne reprennent pas les fichiers de chess.com.
