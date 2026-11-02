#!/usr/bin/env python3
"""
🏠 CROUS Caen — Surveillance de logements
Vérifie l'API du CROUS toutes les heures et envoie une notif Telegram
quand un nouveau logement apparaît.

Déployable sur GitHub Actions (gratuit, 24/7, PC éteint).
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

# ── Configuration ──────────────────────────────────────────────────
# L'URL copiée depuis la barre d'adresse du site CROUS
SEARCH_URL = os.getenv(
    "SEARCH_URL",
    "https://trouverunlogement.lescrous.fr/tools/47/search"
    "?bounds=-0.413777_49.2162655_-0.3307282_49.1530145"
    "&locationName=Caen+%2814000%29"
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Fichier d'état : mémorise les logements déjà vus
STATE_FILE = Path(os.getenv("STATE_FILE", "logements_vus.json"))

# ── Extraction des paramètres depuis l'URL ─────────────────────────
def parse_search_url(url: str) -> tuple[str, dict]:
    """Convertit l'URL du site en URL d'API + paramètres."""
    parsed = urlparse(url)
    # /tools/47/search → /api/fr/search/47
    match = re.search(r"/tools/(\d+)/search", parsed.path)
    if not match:
        raise ValueError(f"URL invalide, pas de /tools/XX/search : {url}")
    tool_id = match.group(1)
    api_url = f"https://trouverunlogement.lescrous.fr/api/fr/search/{tool_id}"

    params = parse_qs(parsed.query)
    # Aplatir les listes (parse_qs renvoie des listes)
    flat_params = {k: v[0] for k, v in params.items()}
    return api_url, flat_params


# ── Requête API ────────────────────────────────────────────────────
def fetch_logements() -> list[dict]:
    """Interroge l'API CROUS et retourne la liste des logements."""
    api_url, params = parse_search_url(SEARCH_URL)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://trouverunlogement.lescrous.fr/",
    }

    retries = 3
    for attempt in range(retries):
        try:
            resp = requests.get(api_url, params=params, headers=headers, timeout=30)
            if resp.status_code == 400 and attempt < retries - 1:
                # L'API CROUS renvoie parfois un 400 aléatoire
                time.sleep(2)
                continue
            resp.raise_for_status()
            data = resp.json()
            # La réponse peut être une liste directe ou un objet avec une clé
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # Chercher la liste de résultats dans les clés courantes
                for key in ("results", "items", "data", "logements", "content"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                # Si c'est un dict avec "count" ou "total", la liste peut être vide
                if data.get("count", data.get("total", -1)) == 0:
                    return []
                # Sinon retourner les valeurs si ça ressemble à un résultat unique
                return [data] if "id" in data else []
            return []
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            print(f"❌ Erreur après {retries} tentatives : {e}")
            return []
    return []


# ── Gestion de l'état (logements déjà vus) ─────────────────────────
def load_seen() -> set:
    """Charge les IDs des logements déjà vus."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return set(data.get("seen_ids", []))
        except (json.JSONDecodeError, KeyError):
            return set()
    return set()


def save_seen(seen_ids: set):
    """Sauvegarde les IDs des logements vus."""
    STATE_FILE.write_text(json.dumps({
        "seen_ids": sorted(seen_ids),
        "last_check": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "count": len(seen_ids),
    }, indent=2, ensure_ascii=False))


# ── Notification Telegram ──────────────────────────────────────────
def send_telegram(message: str):
    """Envoie un message via le bot Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram non configuré, message affiché ici :")
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        print("✅ Notification Telegram envoyée")
    except requests.exceptions.RequestException as e:
        print(f"❌ Erreur Telegram : {e}")


def format_logement(logement: dict) -> str:
    """Formate un logement pour le message Telegram."""
    # Extraire les champs courants (l'API peut varier)
    title = logement.get("title", logement.get("label", "Logement"))
    residence = logement.get("residence", logement.get("residenceLabel", ""))
    price = logement.get("price", logement.get("lolessFees", ""))
    surface = logement.get("surface", logement.get("area", ""))
    address = logement.get("address", logement.get("location", ""))
    logement_id = logement.get("id", "")

    lines = [f"🏠 <b>{title}</b>"]
    if residence:
        lines.append(f"📍 {residence}")
    if address:
        lines.append(f"📫 {address}")
    if price:
        lines.append(f"💰 {price} €/mois")
    if surface:
        lines.append(f"📐 {surface} m²")
    if logement_id:
        link = f"https://trouverunlogement.lescrous.fr/tools/47/search?id={logement_id}"
        lines.append(f'🔗 <a href="{link}">Voir / Réserver</a>')

    return "\n".join(lines)


# ── Boucle principale ─────────────────────────────────────────────
def main():
    print(f"🔍 Vérification CROUS Caen — {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

    logements = fetch_logements()
    seen_ids = load_seen()
    is_first_run = not STATE_FILE.exists() or len(seen_ids) == 0

    print(f"   Trouvé : {len(logements)} logement(s) en ligne")
    print(f"   Déjà vus : {len(seen_ids)}")

    # Extraire les IDs des logements actuels
    current_ids = set()
    new_logements = []

    for logement in logements:
        lid = str(logement.get("id", ""))
        if not lid:
            # Fallback : utiliser un hash du titre + résidence
            lid = str(hash(json.dumps(logement, sort_keys=True)))
        current_ids.add(lid)
        if lid not in seen_ids:
            new_logements.append(logement)

    if is_first_run:
        # Premier lancement : enregistrer l'existant sans notifier
        save_seen(current_ids)
        msg = (
            f"✅ Bot CROUS Caen initialisé !\n"
            f"📊 {len(current_ids)} logement(s) enregistré(s).\n"
            f"🔔 Tu seras notifié dès qu'un nouveau apparaît."
        )
        send_telegram(msg)
        print(f"   Premier run : {len(current_ids)} logement(s) enregistré(s)")
        return

    if new_logements:
        print(f"   🆕 {len(new_logements)} NOUVEAU(X) logement(s) !")
        header = f"🔔 <b>{len(new_logements)} nouveau(x) logement(s) CROUS à Caen !</b>\n"
        body = "\n\n".join(format_logement(l) for l in new_logements[:5])

        if len(new_logements) > 5:
            body += f"\n\n… et {len(new_logements) - 5} autre(s)"

        body += (
            "\n\n👉 <a href=\"https://trouverunlogement.lescrous.fr/tools/47/search"
            "?bounds=-0.413777_49.2162655_-0.3307282_49.1530145"
            "&locationName=Caen+%2814000%29\">Ouvrir le site CROUS</a>"
        )

        send_telegram(header + body)
    else:
        print("   Pas de nouveau logement.")

    # Mettre à jour l'état (union pour ne pas perdre les anciens)
    save_seen(seen_ids | current_ids)

    # Logements disparus ?
    removed = seen_ids - current_ids
    if removed:
        print(f"   ℹ️  {len(removed)} logement(s) disparu(s) (probablement réservés)")


if __name__ == "__main__":
    main()
