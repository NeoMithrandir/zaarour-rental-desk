"""Kijiji Long Term Rentals reader.

Reads only the public search-results pages (newest first) for each configured search and
pulls the listing data Kijiji embeds in the page (__NEXT_DATA__). It does not open individual
ads, log in, collect poster contact details, or send anything: Stefan opens the ad and sends
the message himself from the review screen.
"""
import json
import re
import time
import urllib.parse

from .http import fetch

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def search_url(search, page=1):
    slug, loc = search["slug"], search["location_id"]
    path = f"/b-apartments-condos/{slug}/c37l{loc}" if page == 1 else f"/b-apartments-condos/{slug}/page-{page}/c37l{loc}"
    q = {"sort": "dateDesc"}
    if search.get("lat") is not None:
        q.update({"address": search.get("address", ""), "ll": f"{search['lat']},{search['lng']}",
                  "radius": f"{float(search.get('radius_km', 10)):.1f}"})
    return "https://www.kijiji.ca" + path + "?" + urllib.parse.urlencode(q)


def parse(html, search_name=""):
    """Turn one search-results page into a list of lead dicts."""
    m = NEXT_DATA_RE.search(html)
    if not m:
        return []
    data = json.loads(m.group(1))
    state = data.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__", {})
    leads = []
    for key, ad in state.items():
        if not key.startswith("RealEstateListing:"):
            continue
        attrs = {a.get("canonicalName"): (a.get("canonicalValues") or [None])[0]
                 for a in (ad.get("attributes") or {}).get("all", [])}
        loc = ad.get("location") or {}
        coords = loc.get("coordinates") or {}
        cents = (ad.get("price") or {}).get("amount")
        by = attrs.get("forrentbyhousing")
        leads.append({
            "id": f"kijiji:{ad['id']}",
            "source": "kijiji",
            "source_id": str(ad["id"]),
            "url": ad.get("url", ""),
            "title": (ad.get("title") or "").strip(),
            "snippet": (ad.get("description") or "").strip(),
            "address": loc.get("address") or loc.get("name") or "",
            "lat": coords.get("latitude"),
            "lng": coords.get("longitude"),
            "price": round(cents / 100) if isinstance(cents, (int, float)) else None,
            "bedrooms": attrs.get("numberbedrooms"),
            "unit_type": attrs.get("unittype"),
            "poster_id": str((ad.get("posterInfo") or {}).get("posterId") or ""),
            "poster_type": {"ownr": "owner", "reprofessional": "professional"}.get(by, by or ""),
            "posted_at": ad.get("activationDate") or ad.get("sortingDate") or "",
            "image": (ad.get("imageUrls") or [""])[0],
            "is_top_ad": ad.get("adSource") == "TOP_AD",
            "search": search_name,
        })
    return leads


def collect(settings, log=print, fetcher=fetch):
    """Run every configured search; return de-duplicated leads (newest version of each ad)."""
    cfg = settings.get("kijiji", {})
    pages = int(cfg.get("pages_per_search", 2))
    delay = float(cfg.get("delay_seconds", 4))
    seen = {}
    first = True
    for search in cfg.get("searches", []):
        for page in range(1, int(search.get("pages", pages)) + 1):
            if not first:
                time.sleep(delay)
            first = False
            url = search_url(search, page)
            try:
                found = parse(fetcher(url), search.get("name", ""))
            except Exception as e:  # network hiccup or page change: report, keep going
                log(f"  ! Kijiji {search.get('name')} page {page}: {e}")
                continue
            organic = [l for l in found if not l["is_top_ad"]]
            log(f"  Kijiji {search.get('name')} page {page}: {len(found)} ads")
            for lead in found:
                seen.setdefault(lead["id"], lead)
            if not organic:
                break
    return list(seen.values())
