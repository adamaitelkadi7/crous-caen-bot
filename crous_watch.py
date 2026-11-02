#!/usr/bin/env python3
"""
🏠 CROUS Caen — Surveillance de logements
Interroge l'API du CROUS toutes les heures et envoie une notif Telegram
quand un nouveau logement apparaît.

Déployable sur GitHub Actions (gratuit, 24/7, PC éteint).
"""

import html
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
# Zone = Caen (14000) + Hérouville-Saint-Clair (14200) : union des deux cadres
# de carte renvoyés par le site CROUS pour chaque ville.
DEFAULT_SEARCH_URL = (
    "https://trouverunlogement.lescrous.fr/tools/47/search"
    "?bounds=-0.413777_49.2247942_-0.304511_49.1530145"
    "&locationName=Caen+%2B+H%C3%A9rouville-Saint-Clair"
)
SEARCH_URL = os.getenv("SEARCH_URL") or DEFAULT_SEARCH_URL

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Fichier d'état : mémorise les logements déjà vus
STATE_FILE = Path(os.getenv("STATE_FILE", "logements_vus.json"))

BASE = "https://trouverunlogement.lescrous.fr"
PAGE_SIZE = 100
MAX_PAGES = 10

OCCUPATION_LABELS = {
    "alone": "Individuel",
    "couple": "Couple",
    "house_sharing": "Colocation",
}


class CrousError(Exception):
    """L'API du CROUS n'a pas répondu correctement."""


# ── Lecture de l'URL de recherche ──────────────────────────────────
def parse_search_url(url: str) -> tuple[str, list[dict], str]:
    """Extrait l'id de la phase (tool), la zone géographique et le nom du lieu."""
    parsed = urlparse(url)
    match = re.search(r"/tools/(\d+)/search", parsed.path)
    if not match:
        raise ValueError(f"URL invalide, pas de /tools/XX/search : {url}")
    tool_id = match.group(1)

    params = parse_qs(parsed.query)
    bounds = params.get("bounds", [None])[0]
    if not bounds:
        raise ValueError("L'URL doit contenir un paramètre bounds=... (zone de la carte)")
    parts = [float(x) for x in bounds.split("_")]
    if len(parts) != 4:
        raise ValueError(f"Paramètre bounds inattendu : {bounds}")
    # bounds = lonOuest_latNord_lonEst_latSud
    location = [
        {"lon": parts[0], "lat": parts[1]},
        {"lon": parts[2], "lat": parts[3]},
    ]
    location_name = params.get("locationName", ["la zone choisie"])[0]
    return tool_id, location, location_name


TOOL_ID, LOCATION, LOCATION_NAME = parse_search_url(SEARCH_URL)


# ── Requête API ────────────────────────────────────────────────────
def fetch_page(page: int) -> dict:
    """Appelle l'API de recherche (POST) pour une page de résultats."""
    body = {
        "idTool": int(TOOL_ID),
        "need_aggregation": False,
        "page": page,
        "pageSize": PAGE_SIZE,
        "sector": None,
        "occupationModes": [],
        "location": LOCATION,
        "residence": None,
        "precision": 6,
        "equipment": [],
        "price": {"max": 10000000},
        "area": {"min": 0},
        "toolMechanism": "flow",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": BASE,
        "Referer": SEARCH_URL,
    }
    url = f"{BASE}/api/fr/search/{TOOL_ID}"

    last_error = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if "results" not in data or "items" not in data["results"]:
                    raise CrousError(f"Réponse inattendue : {str(data)[:200]}")
                return data["results"]
            last_error = f"HTTP {resp.status_code}"
        except (requests.exceptions.RequestException, ValueError) as e:
            last_error = str(e)
        # L'API CROUS renvoie parfois des erreurs aléatoires : on réessaie
        time.sleep(3 * attempt)
    raise CrousError(f"Échec après 3 tentatives ({last_error})")


def fetch_logements() -> list[dict]:
    """Récupère tous les logements de la zone (toutes les pages)."""
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        results = fetch_page(page)
        batch = results["items"]
        items.extend(batch)
        total = results.get("total", {}).get("value", len(items))
        if len(batch) < PAGE_SIZE or len(items) >= total:
            break
    return items


# ── Gestion de l'état (logements déjà vus) ─────────────────────────
def load_state() -> dict | None:
    """Retourne l'état sauvegardé, ou None si le bot n'a jamais été initialisé."""
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return None
    if not data.get("initialized"):
        return None
    return data


def current_hour() -> str:
    return time.strftime("%Y-%m-%d %Hh UTC", time.gmtime())


def save_state(seen_ids: set[str]):
    """Écrit l'état. Le champ 'heartbeat' (à l'heure près) fait qu'il y a au
    plus un commit par heure quand rien ne change : une trace que le bot tourne,
    sans créer un commit à chaque vérification (toutes les 5 min)."""
    STATE_FILE.write_text(json.dumps({
        "initialized": True,
        "seen_ids": sorted(seen_ids),
        "count": len(seen_ids),
        "heartbeat": current_hour(),
    }, indent=2, ensure_ascii=False) + "\n")


# ── Notification Telegram ──────────────────────────────────────────
def send_telegram(message: str) -> bool:
    """Envoie un message via le bot Telegram. Retourne True si c'est passé."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram non configuré, message affiché ici :")
        print(message)
        return True

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.ok:
                print("✅ Notification Telegram envoyée")
                return True
            print(f"❌ Telegram a répondu {resp.status_code} : {resp.text[:200]}")
        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur Telegram : {e}")
        time.sleep(2 * attempt)
    return False


def euros(cents) -> str:
    """L'API donne les loyers en centimes : 26599 -> '265,99 €'."""
    value = cents / 100
    text = f"{value:.0f}" if value == int(value) else f"{value:.2f}".replace(".", ",")
    return f"{text} €"


def format_logement(lg: dict) -> str:
    """Formate un logement pour le message Telegram."""
    esc = lambda s: html.escape(str(s)) if s else ""
    residence = lg.get("residence") or {}
    lines = [f"🏠 <b>{esc(lg.get('label') or 'Logement')}</b>"]

    if residence.get("label"):
        lines.append(f"🏢 {esc(residence['label'])}")
    if residence.get("address"):
        lines.append(f"📍 {esc(residence['address'])}")

    for mode in lg.get("occupationModes") or []:
        rent = mode.get("rent") or {}
        lo, hi = rent.get("min"), rent.get("max")
        if lo is None:
            continue
        price = euros(lo) if lo == hi or hi is None else f"{euros(lo)} – {euros(hi)}"
        kind = OCCUPATION_LABELS.get(mode.get("type"), mode.get("type", ""))
        lines.append(f"💰 {price}/mois ({esc(kind)})")

    area = lg.get("area") or {}
    if area.get("min"):
        surf = area["min"] if area.get("min") == area.get("max") else f"{area['min']}–{area['max']}"
        lines.append(f"📐 {surf} m²")

    if lg.get("id"):
        link = f"{BASE}/tools/{TOOL_ID}/accommodations/{lg['id']}"
        lines.append(f'🔗 <a href="{link}">Voir / Réserver</a>')

    return "\n".join(lines)


# ── Programme principal ────────────────────────────────────────────
def main() -> int:
    print(f"🔍 Vérification CROUS — {LOCATION_NAME} — "
          f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

    try:
        logements = fetch_logements()
    except CrousError as e:
        # On ne touche pas à l'état : le run sera marqué en échec (croix rouge)
        print(f"❌ API CROUS : {e}")
        return 1

    current = {str(lg["id"]): lg for lg in logements if lg.get("id") is not None}
    print(f"   Trouvé : {len(current)} logement(s) en ligne")

    state = load_state()
    if state is None:
        # Premier lancement : on enregistre l'existant sans spammer
        save_state(set(current))
        ok = send_telegram(
            f"✅ <b>Bot CROUS initialisé !</b>\n"
            f"📍 Zone : {html.escape(LOCATION_NAME)}\n"
            f"📊 {len(current)} logement(s) disponible(s) en ce moment.\n"
            f"🔔 Je vérifie toutes les 5 minutes et je te préviens dès qu'un nouveau apparaît."
        )
        print(f"   Premier run : {len(current)} logement(s) enregistré(s)")
        return 0 if ok else 1

    seen_ids = set(state.get("seen_ids", []))
    print(f"   Déjà vus : {len(seen_ids)}")
    new_ids = [i for i in current if i not in seen_ids]

    if new_ids:
        print(f"   🆕 {len(new_ids)} NOUVEAU(X) logement(s) !")
        header = (f"🔔 <b>{len(new_ids)} nouveau(x) logement(s) CROUS — "
                  f"{html.escape(LOCATION_NAME)} !</b>\n\n")
        body = "\n\n".join(format_logement(current[i]) for i in new_ids[:8])
        if len(new_ids) > 8:
            body += f"\n\n… et {len(new_ids) - 8} autre(s)"
        body += f'\n\n👉 <a href="{html.escape(SEARCH_URL)}">Ouvrir la recherche CROUS</a>'

        if not send_telegram(header + body):
            # Notification ratée : on ne marque pas ces logements comme vus,
            # pour réessayer au prochain passage.
            return 1
    else:
        print("   Pas de nouveau logement.")

    # On ne garde que ce qui est en ligne maintenant : si un logement est
    # réservé puis se libère à nouveau (désistement), tu seras re-prévenu.
    gone = seen_ids - set(current)
    if gone:
        print(f"   ℹ️  {len(gone)} logement(s) disparu(s) (probablement réservés)")
    if set(current) != seen_ids or state.get("heartbeat") != current_hour():
        save_state(set(current))
    return 0


if __name__ == "__main__":
    sys.exit(main())
