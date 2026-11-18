"""Is a landlord's number a broker's? A web search for each captured number, as an extra broker flag.

Uses a search provider's official API (Brave Search or Google Programmable Search, key in settings
`phone_search`), never the search engines' own pages: those forbid automated queries and sit behind bot checks.
Off while no provider and key are set. Each number is searched once (again after `recheck_days`), a few per
read, `min_interval_seconds` apart. A number counts as a broker's when a result is on a brokerage or listing site
(realtor.ca, centris.ca, RE/MAX...), or when a result shows the number next to broker wording. The finding, with
the page it came from, is kept in the phone_searches table and shown on the listing as a broker flag.
"""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .text import fold

BROKER_SITES = ("realtor.ca", "centris.ca", "remax", "royallepage.ca", "sutton", "viacapitale", "kw.com",
                "kellerwilliams", "century21", "sothebysrealty", "evrealestate.com", "engelvoelkers", "exprealty",
                "oaciq.com", "proprio-direct", "proprio direct", "lacapitaleimmobiliere", "zaarour")
BROKER_WORDS = re.compile(r"courtier|courtiere|broker|realtor|agent immobilier|agence immobiliere|real estate agent|"
                          r"equipe immobiliere|real estate team|oaciq|re/max|remax|royal lepage|sutton|via capitale|"
                          r"keller williams|century 21|sotheby|engel|exp realty|proprio direct")


def _digits(s):
    return re.sub(r"\D", "", s or "")


def query(number):
    """'514-555-1234' -> the number as people write it, any of three ways."""
    a, b, c = number.split("-")
    return f'"{a}-{b}-{c}" OR "{a} {b} {c}" OR "({a}) {b}-{c}"'


def classify(number, results):
    """(label, url, title) of the first result that ties the number to a broker, or None."""
    digits = _digits(number)
    for r in results:
        url, title, text = r.get("url", ""), r.get("title", ""), r.get("snippet", "")
        host = urllib.parse.urlsplit(url).netloc.lower()
        site = next((s for s in BROKER_SITES if s.replace(" ", "") in host), None)
        if site:
            return f"on {host}", url, title
        words = fold(f"{title} {text}")
        if digits in _digits(f"{title} {text}") and BROKER_WORDS.search(words):
            return f"next to broker wording on {host}", url, title
    return None


def _get_json(url, headers, timeout=20):  # pragma: no cover - network
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search(cfg, q, get=_get_json):
    """Results as [{url, title, snippet}] from the configured provider."""
    if cfg.get("provider") == "brave":
        data = get("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": q, "count": 10}),
                   {"Accept": "application/json", "X-Subscription-Token": cfg["brave_api_key"]})
        return [{"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("description", "")}
                for r in (data.get("web") or {}).get("results", [])]
    if cfg.get("provider") == "google":
        data = get("https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
            {"key": cfg["google_api_key"], "cx": cfg["google_cx"], "q": q, "num": 10}), {"Accept": "application/json"})
        return [{"url": r.get("link", ""), "title": r.get("title", ""), "snippet": r.get("snippet", "")}
                for r in data.get("items", [])]
    raise ValueError(f"unknown phone_search provider: {cfg.get('provider')!r}")


def problem(settings):
    """Why the search is off, or None when it can run."""
    cfg = settings.get("phone_search") or {}
    provider = cfg.get("provider") or ""
    if not provider:
        return "no provider set (phone_search.provider: brave or google)"
    if provider == "brave" and not cfg.get("brave_api_key"):
        return "no Brave Search API key (phone_search.brave_api_key)"
    if provider == "google" and not (cfg.get("google_api_key") and cfg.get("google_cx")):
        return "no Google Programmable Search key and engine id (phone_search.google_api_key, google_cx)"
    return None


def found_brokers(conn):
    """number digits -> label, for numbers a search tied to a broker (used by the checks)."""
    return {_digits(r["number"]): r["label"] for r in conn.execute(
        "SELECT number, label FROM phone_searches WHERE verdict='broker'")}


def run(conn, settings, log=print, get=_get_json, sleep=time.sleep):
    """Search the numbers of undecided leads not searched lately. Returns how many came back tied to a broker."""
    why = problem(settings)
    if why:
        return 0
    cfg = settings["phone_search"]
    since = (datetime.now() - timedelta(days=float(cfg.get("recheck_days", 60)))).isoformat(timespec="seconds")
    numbers = []
    for r in conn.execute("SELECT phone FROM leads WHERE status='new' AND COALESCE(phone, '')!='' ORDER BY first_seen DESC"):
        for n in r["phone"].split(", "):
            if n not in numbers:
                numbers.append(n)
    done = {r["number"] for r in conn.execute("SELECT number FROM phone_searches WHERE searched_at>? AND verdict!='error'",
                                              (since,))}
    todo = [n for n in numbers if n not in done][:int(cfg.get("max_per_run", 20))]
    brokers = 0
    for i, number in enumerate(todo):
        if i:
            sleep(float(cfg.get("min_interval_seconds", 3)))
        try:
            hit = classify(number, search(cfg, query(number), get))
        except Exception as e:  # quota, network: stop for this run, try again next time
            conn.execute("INSERT OR REPLACE INTO phone_searches VALUES (?,?,?,?,?,?)",
                         (number, datetime.now().isoformat(timespec="seconds"), "error", None, None, str(e)[:200]))
            conn.commit()
            log(f"  ! Number search stopped: {e}")
            break
        conn.execute("INSERT OR REPLACE INTO phone_searches VALUES (?,?,?,?,?,?)",
                     (number, datetime.now().isoformat(timespec="seconds"), "broker" if hit else "clear",
                      hit[0] if hit else None, hit[1] if hit else None, hit[2] if hit else None))
        conn.commit()
        brokers += bool(hit)
    if todo:
        log(f"  Searched {len(todo)} landlord number{'s' if len(todo) != 1 else ''}: {brokers} found with a broker")
    if brokers:
        from .feed import recheck_all
        recheck_all(conn)
    return brokers
