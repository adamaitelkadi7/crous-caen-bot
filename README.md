# 🏠 Bot CROUS Caen + Hérouville — places libres

Vérifie [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr/tools/48/search?bounds=-0.5_49.25_-0.25_49.12&locationName=Caen+%2B+H%C3%A9rouville-Saint-Clair) **toutes les 5 minutes** et envoie une **alerte Telegram** dès qu'une place se libère à **Caen ou Hérouville-Saint-Clair** (individuel, couple ou colocation).

Tourne dans le cloud, gratuitement, PC éteint.

## Comment ça marche

```
cron-job.org (toutes les 5 min)
   └─► API GitHub : lance le workflow « Surveillance CROUS Caen »
          └─► crous_watch.py
                 ├─► GET  /api/fr/tools                      : campagnes ouvertes (48 = « Fil de l'eau 2026-27 »)
                 ├─► POST /api/fr/search/48                   : logements affichés dans la zone
                 ├─► + logements suivis en permanence (Bacot, Côte de Nacre)
                 ├─► GET  /api/fr/tools/48/accommodations/<id>/availabilities : vraie place réservable ?
                 ├─► compare avec logements_vus.json
                 └─► nouvelle place ? → message Telegram (+ un message « Bot CROUS OK » par jour)
```

- **cron-job.org** déclenche le workflow (le cron natif de GitHub est gardé en secours, mais il ne se déclenche pas toujours).
- **logements_vus.json** garde les places déjà signalées. Il n'est commité que s'il change (au moins une trace par heure, `heartbeat`).
- Une place qui disparaît puis revient (désistement) déclenche une nouvelle alerte.
- Le site du CROUS bloque les rafales : les requêtes partent une par une, avec 1 s de pause.

## Configuration

| Où | Quoi |
|----|------|
| GitHub → Settings → Secrets → Actions | `TELEGRAM_BOT_TOKEN` (token BotFather), `TELEGRAM_CHAT_ID` |
| GitHub → Settings → Variables → Actions (optionnel) | `CROUS_TOOL_ID` : numéro de campagne préféré (48 par défaut) |
| cron-job.org | POST `https://api.github.com/repos/adamaitelkadi7/crous-caen-bot/actions/workflows/surveillance.yml/dispatches`, corps `{"ref":"main"}`, en-tête `Authorization: Bearer <token GitHub>` |
| Token GitHub (fine-grained) | Dépôt `crous-caen-bot` uniquement, permission « Actions : Read and write » — expire le 31/10/2027 |

## FAQ

**Changer la fréquence ?** → Sur cron-job.org, modifier l'intervalle du cronjob. Éviter de descendre sous 5 min.

**Changer de zone ?** → `LOCATION` (deux coins de la carte, longitude/latitude) et `EXTRA_LOGEMENTS` dans `crous_watch.py`.

**Nouvelle campagne CROUS ?** → Rien à faire : le bot lit la liste des campagnes ouvertes sur `/api/fr/tools`.

**Plus de notifications ?** → Pas de « Bot CROUS OK » le matin = problème. Onglet Actions : croix rouges = lire le journal du run. Historique des appels : cron-job.org → Cronjobs → Historique. Token expiré : en créer un nouveau et le coller dans cron-job.org.
