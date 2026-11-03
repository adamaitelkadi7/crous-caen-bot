#!/usr/bin/env python3
"""
🏠 CROUS Caen + Hérouville — Surveillance des PLACES LIBRES
Vérifie, pour chaque logement de la zone, si une période est réellement
réservable (individuel, coloc, couple) et envoie une alerte Telegram dès
qu'une place s'ouvre.

Déployable sur GitHub Actions (gratuit, 24/7, PC éteint).
"""

import datetime as dt
import functools
import html
import json
import os
import sys
import time
from pathlib import Path

import requests
from concurrent.futures import ThreadPoolExecutor

# Affichage immédiat des logs dans GitHub Actions
print = functools.partial(print, flush=True)

# ── Configuration ──────────────────────────────────────────────────
BASE = "https://trouverunlogement.lescrous.fr"

# Campagne CROUS en cours (le numéro dans l'URL : /tools/48/...).
# Si la campagne change, le bot essaie automatiquement les numéros suivants.
TOOL_ID = int(os.getenv("CROUS_TOOL_ID") or 48)

# Zone : tout Caen + Hérouville-Saint-Clair (Bacot, Côte de Nacre, Lebisey,
# Satie, Gremillon, André Breton, Flora Tristan…)
LOCATION = [
    {"lon": -0.50, "lat": 49.25},   # coin nord-ouest
    {"lon": -0.25, "lat": 49.12},   # coin sud-est
]
LOCATION_NAME = "Caen + Hérouville-Saint-Clair"

# Logements que la recherche CROUS cache parfois quand ils sont complets :
# on les vérifie quand même à chaque passage.
EXTRA_LOGEMENTS = [
    (3040, "STUDIO BACOT 20M", "RESIDENCE BACOT", ["alone"]),
    (2032, "T1 BACOT 20M", "RESIDENCE BACOT", ["alone"]),
    (1431, "T1 BIS BACOT 30M CL", "RESIDENCE BACOT", ["house_sharing", "couple"]),
    (95, "T1 BIS BACOT 30M PMR CL", "RESIDENCE BACOT", ["house_sharing"]),
    (1432, "T2 BACOT 50M CP", "RESIDENCE BACOT", ["couple"]),
    (2054, "Chambre confort CDN", "RESIDENCE COTE DE NACRE", ["alone"]),
]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = Path(os.getenv("STATE_FILE", "logements_vus.json"))

# Heure (UTC) à partir de laquelle le message de contrôle quotidien part
DAILY_REPORT_HOUR_UTC = 7   # = 8h ou 9h à Paris

# Pause entre deux requêtes : le CROUS bloque les rafales venant de GitHub
REQUEST_PAUSE = 1.0

OCCUPATION_LABELS = {
    "alone": "Individuel",
    "couple": "Couple",
    "house_sharing": "Colocation",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": f"{BASE}/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


class CrousError(Exception):
    """L'API du CROUS n'a pas répondu correctement."""


# ── Recherche des logements de la zone ─────────────────────────────
def search(tool_id: int) -> list[dict]:
    """Liste tous les logements de la zone (complets ou non).
    Plusieurs variantes de requête sont essayées : l'API CROUS renvoie parfois
    503 selon la forme de la requête ou la charge du serveur."""
    referer = (f"{BASE}/tools/{tool_id}/search?bounds="
               f"{LOCATION[0]['lon']}_{LOCATION[0]['lat']}_{LOCATION[1]['lon']}_{LOCATION[1]['lat']}")
    variants = [("flow", 100), ("residual", 100), ("flow", 50)]
    last_error = None
    for attempt, (mechanism, size) in enumerate(variants * 2, start=1):
        body = {
            "idTool": tool_id,
            "need_aggregation": False,
            "page": 1,
            "pageSize": size,
            "sector": None,
            "occupationModes": [],
            "location": LOCATION,
            "residence": None,
            "precision": 6,
            "equipment": [],
            "price": {"max": 10000000},
            "area": {"min": 0},
            "toolMechanism": mechanism,
        }
        try:
            resp = SESSION.post(f"{BASE}/api/fr/search/{tool_id}", json=body,
                                headers={"Referer": referer}, timeout=(5, 20))
            print(f"   recherche tools/{tool_id} ({mechanism}, {size}) -> HTTP {resp.status_code}")
            if resp.status_code == 200:
                return resp.json()["results"]["items"]
            last_error = f"HTTP {resp.status_code}"
        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            last_error = str(e)
            print(f"   recherche tools/{tool_id} -> {type(e).__name__}")
        time.sleep(4 * attempt)
    raise CrousError(f"Recherche impossible ({last_error})")


def find_active_tool() -> tuple[int, list[dict]]:
    """Trouve la campagne en cours : TOOL_ID, sinon les numéros suivants."""
    for tool_id in range(TOOL_ID, TOOL_ID + 4):
        items = search(tool_id)
        if items:
            return tool_id, items
    return TOOL_ID, []


# ── Disponibilités réelles ─────────────────────────────────────────
def check_window(tool_id: int, acc_id: int, mode: str, arr: dt.date, dep: dt.date) -> list | None:
    """Une requête de disponibilité. None = pas de réponse exploitable."""
    url = (f"{BASE}/api/fr/tools/{tool_id}/accommodations/{acc_id}/availabilities"
           f"?occupationMode={mode}&arrivalDate={arr}&departureDate={dep}")
    for retry in range(3):   # le serveur CROUS est lent : on réessaie
        try:
            resp = SESSION.get(url, timeout=(8, 20))
        except requests.exceptions.RequestException as e:
            print(f"   ⚠️ {acc_id}/{mode} : {type(e).__name__} (essai {retry + 1})")
            time.sleep(5 + 5 * retry)
            continue
        finally:
            time.sleep(REQUEST_PAUSE)
        # 400 = pas de stock pour ce mode (fréquent pour « couple ») -> rien
        if resp.status_code != 200:
            return []
        try:
            return resp.json().get("periodsAvailable") or []
        except ValueError:
            return None
    return None


def best_period(tool_id: int, acc_id: int, mode: str, today: dt.date) -> dict | None:
    """Renvoie une période réservable pour ce logement/mode, sinon None.
    1 requête pour savoir s'il y a une place (au moins 1 mois dès maintenant),
    puis, seulement si oui, 1 requête pour voir si c'est dispo jusqu'au 31/08."""
    arrival = today + dt.timedelta(days=2)
    periods = check_window(tool_id, acc_id, mode, arrival, arrival + dt.timedelta(days=31))
    if not periods:
        return None
    end_of_year = dt.date(today.year + (1 if today.month >= 9 else 0), 8, 31)
    if end_of_year > arrival + dt.timedelta(days=31):
        longer = check_window(tool_id, acc_id, mode, arrival, end_of_year)
        if longer:
            return longer[0]
    return periods[0]


# ── État ───────────────────────────────────────────────────────────
def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


# ── Telegram ───────────────────────────────────────────────────────
def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram non configuré, message affiché ici :")
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
    value = cents / 100
    return f"{value:.2f}".replace(".", ",") + " €"


def fmt_date(s: str) -> str:
    return dt.date.fromisoformat(s[:10]).strftime("%d/%m/%Y")


def format_alert(tool_id: int, lg: dict, mode: dict, period: dict) -> str:
    esc = lambda s: html.escape(str(s)) if s else ""
    res = lg.get("residence") or {}
    rent = (mode.get("rent") or {}).get("min")
    lines = [
        f"🏠 <b>{esc(lg.get('label') or 'Logement')}</b>",
        f"🏢 {esc(res.get('label'))}",
        f"📍 {esc(res.get('address'))}",
        f"👤 {OCCUPATION_LABELS.get(mode.get('type'), mode.get('type'))}"
        + (f" — 💰 {euros(rent)}/mois" if rent else ""),
        f"📅 Dispo du {fmt_date(period['arrivalDate'])} au {fmt_date(period['departureDate'])}",
        f'🔗 <a href="{BASE}/tools/{tool_id}/accommodations/{lg["id"]}">Réserver vite !</a>',
    ]
    return "\n".join(lines)


# ── Programme principal ────────────────────────────────────────────
def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    print(f"🔍 Vérification CROUS — {LOCATION_NAME} — {now:%Y-%m-%d %H:%M UTC}")

    state = load_state()

    try:
        tool_id, logements = find_active_tool()
    except CrousError as e:
        print(f"❌ API CROUS : {e}")
        return 1

    print(f"   Campagne : tools/{tool_id} — {len(logements)} logement(s) dans la zone")

    # Garde-fou : zone vide = campagne changée ou site modifié -> prévenir 1x/jour
    if not logements:
        if state.get("empty_warned") != str(today):
            send_telegram("⚠️ <b>Bot CROUS : 0 logement trouvé dans la zone.</b>\n"
                          "La campagne CROUS a peut-être changé. Vérifie le numéro "
                          "/tools/XX/ dans l'URL du site.")
            state["empty_warned"] = str(today)
            save_state(state)
        return 0

    # Vérifie chaque logement × chaque type d'occupation
    available: dict[str, dict] = {}
    alerts: list[str] = []
    found_ids = {lg.get("id") for lg in logements}
    for acc_id, label, residence, modes in EXTRA_LOGEMENTS:
        if acc_id not in found_ids:
            logements.append({"id": acc_id, "label": label,
                              "residence": {"label": residence, "address": "Caen"},
                              "occupationModes": [{"type": m} for m in modes]})
    jobs = [(lg, mode) for lg in logements for mode in (lg.get("occupationModes") or [])
            if mode.get("type")]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=1) as pool:
        results = list(pool.map(
            lambda j: best_period(tool_id, j[0]["id"], j[1]["type"], today), jobs))
    print(f"   {len(jobs)} vérifications en {time.time() - t0:.1f}s")
    for (lg, mode), period in zip(jobs, results):
        if not period:
            continue
        mtype = mode["type"]
        key = f"{lg['id']}|{mtype}"
        available[key] = {"arrival": period["arrivalDate"][:10],
                          "departure": period["departureDate"][:10]}
        print(f"   ✅ PLACE : {lg.get('label')} ({mtype}) {period['arrivalDate'][:10]}")
        if key not in (state.get("available") or {}):
            alerts.append(format_alert(tool_id, lg, mode, period))

    print(f"   Places libres maintenant : {len(available)}")

    if alerts:
        msg = (f"🚨 <b>{len(alerts)} PLACE(S) CROUS LIBRE(S) — {html.escape(LOCATION_NAME)} !</b>\n\n"
               + "\n\n".join(alerts[:8]))
        if len(alerts) > 8:
            msg += f"\n\n… et {len(alerts) - 8} autre(s)"
        if not send_telegram(msg):
            return 1   # on réessaiera au prochain passage

    # Message de contrôle quotidien : preuve que le bot tourne
    if now.hour >= DAILY_REPORT_HOUR_UTC and state.get("last_daily") != str(today):
        send_telegram(f"🤖 <b>Bot CROUS OK</b> ({now:%d/%m})\n"
                      f"📍 {html.escape(LOCATION_NAME)} — campagne tools/{tool_id}\n"
                      f"🏢 {len(logements)} logements surveillés\n"
                      f"🔑 {len(available)} place(s) libre(s) en ce moment")
        state["last_daily"] = str(today)

    state.update({
        "tool_id": tool_id,
        "logements_surveilles": len(logements),
        "available": available,
        "heartbeat": now.strftime("%Y-%m-%d %Hh UTC"),
    })
    state.pop("seen_ids", None)
    state.pop("count", None)
    state.pop("initialized", None)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
