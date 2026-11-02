# 🏠 Bot CROUS Caen — Surveillance de logements

Surveille automatiquement [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr/tools/47/search?bounds=-0.413777_49.2162655_-0.3307282_49.1530145&locationName=Caen+%2814000%29) **toutes les heures** et t'envoie une **notification Telegram** dès qu'un nouveau logement apparaît à Caen.

Tourne sur **GitHub Actions** — gratuit, 24/7, PC éteint.

## Installation rapide (10 min)

### 1. Créer le bot Telegram

1. Ouvre Telegram → cherche **@BotFather**
2. Envoie `/newbot` → donne un nom (ex: "CROUS Caen Bot")
3. Note le **token** (format : `123456:ABC-DEF...`)
4. Envoie un message quelconque à ton nouveau bot
5. Va sur `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
6. Note ton **chat_id** (nombre dans `"chat":{"id": XXXXXX}`)

### 2. Créer le dépôt GitHub

1. Va sur [github.com/new](https://github.com/new)
2. Nom : `crous-caen-bot` — **Public** (= minutes Actions illimitées)
3. Pousse ces fichiers dedans

### 3. Ajouter les secrets

Sur GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret** :

| Nom | Valeur |
|-----|--------|
| `TELEGRAM_BOT_TOKEN` | Le token du BotFather |
| `TELEGRAM_CHAT_ID` | Ton chat_id |

### 4. Lancer !

Onglet **Actions** → workflow "Surveillance CROUS Caen" → **Run workflow**

Le premier run enregistre l'existant sans t'alerter. Ensuite, toutes les heures, tu reçois une notif Telegram s'il y a du nouveau.

## FAQ

**Changer la fréquence ?** → Modifier le `cron` dans `.github/workflows/surveillance.yml`
- Toutes les 30 min : `*/30 * * * *`
- Toutes les 2h : `0 */2 * * *`

**Changer la ville ?** → Sur le site CROUS, filtre ta ville, copie l'URL, mets-la dans la variable `SEARCH_URL` (Settings → Variables → Actions)

**GitHub désactive le workflow ?** → Après 60j sans activité, GitHub envoie un mail. Clique "Enable" pour réactiver.
