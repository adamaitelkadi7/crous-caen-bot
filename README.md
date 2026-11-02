# 🏠 Bot CROUS Caen — Surveillance de logements

Surveille [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr/tools/47/search?bounds=-0.413777_49.2247942_-0.304511_49.1530145&locationName=Caen+%2B+H%C3%A9rouville-Saint-Clair) **toutes les 5 minutes** et envoie une **notification Telegram** dès qu'un nouveau logement apparaît à **Caen ou Hérouville-Saint-Clair**.

Tourne dans le cloud, gratuitement, PC éteint.

## Comment ça marche

```
cron-job.org (toutes les 5 min)
   └─► API GitHub : lance le workflow « Surveillance CROUS Caen »
          └─► crous_watch.py
                 ├─► API CROUS (POST /api/fr/search/47) : logements de la zone
                 ├─► compare avec logements_vus.json
                 └─► nouveau logement ? → message Telegram
```

- **cron-job.org** déclenche le workflow (le cron natif de GitHub est gardé en secours, mais il ne se déclenche pas toujours).
- **logements_vus.json** garde les logements déjà signalés. Il n'est commité que si la liste change, plus une trace par heure (`heartbeat`).
- Si un logement est réservé puis se libère à nouveau, une nouvelle alerte est envoyée.
- Si le site du CROUS ou Telegram ne répond pas, le run échoue (croix rouge, mail de GitHub) et l'état n'est pas modifié.

## Configuration

| Où | Quoi |
|----|------|
| GitHub → Settings → Secrets → Actions | `TELEGRAM_BOT_TOKEN` (token BotFather), `TELEGRAM_CHAT_ID` |
| GitHub → Settings → Variables → Actions (optionnel) | `SEARCH_URL` : URL de recherche copiée depuis le site CROUS |
| cron-job.org | POST `https://api.github.com/repos/adamaitelkadi7/crous-caen-bot/actions/workflows/surveillance.yml/dispatches`, corps `{"ref":"main"}`, en-tête `Authorization: Bearer <token GitHub>` |
| Token GitHub (fine-grained) | Dépôt `crous-caen-bot` uniquement, permission « Actions : Read and write » — expire le 31/10/2027 |

## FAQ

**Changer la fréquence ?** → Sur cron-job.org, modifier l'intervalle du cronjob. Éviter de descendre sous 5 min.

**Changer de ville ?** → Sur le site CROUS, cherche ta ville, copie l'URL et mets-la dans la variable `SEARCH_URL`.

**Nouvelle phase CROUS ?** → L'URL du site change (`/tools/47/...` devient un autre numéro) : mets la nouvelle URL dans `SEARCH_URL`.

**Plus de notifications ?** → Onglet Actions : croix rouges = regarder le journal du run. Historique des appels : cron-job.org → Cronjobs → Historique. Token expiré : en créer un nouveau et le coller dans cron-job.org.
