"""Kijiji Long Term Rentals reader.

Reads only the public search-results pages (newest first) for each configured search and
pulls the listing data Kijiji embeds in the page (__NEXT_DATA__). It does not open individual
ads, log in, collect poster contact details, or send anything: Stefan opens the ad and sends
the message himself from the review screen.
"""
import json
import re

from .http import HostCooling, NotModified, fetch

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def search_url(search, page=1):
    """A location's search page. Kijiji already lists organic ads newest first, and its robots.txt asks
    robots not to use the sort, address, ll or radius parameters, so the desk doesn't: it reads whole
    locations and lets the area check sort the ads into areas."""
    slug, loc = search["slug"], search["location_id"]
    path = f"/b-apartments-condos/{slug}/c37l{loc}" if page == 1 else f"/b-apartments-condos/{slug}/page-{page}/c37l{loc}"
    return "https://www.kijiji.ca" + path


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


def collect(settings, log=print, fetcher=fetch, known=None):
    """Run every configured search; return de-duplicated leads (newest version of each ad).

    Pages are read newest first until a page holds no ad the desk hasn't seen (caught up), the page is
    unchanged since last time, or the search's page cap is reached. `known(id)` says whether an ad is
    already in the desk."""
    cfg = settings.get("kijiji", {})
    pages = int(cfg.get("max_pages_per_search", cfg.get("pages_per_search", 2)))
    known = known or (lambda _id: False)
    seen = {}
    for search in cfg.get("searches", []):
        for page in range(1, int(search.get("pages", pages)) + 1):
            url = search_url(search, page)
            try:
                found = parse(fetcher(url), search.get("name", ""))
            except NotModified:
                log(f"  Kijiji {search.get('name')}: no change since last check")
                break
            except HostCooling as e:
                if not seen:  # nothing read at all: the board failed this time
                    raise
                log(f"  ! Kijiji: {e}")
                return list(seen.values())
            except Exception as e:  # network hiccup, robots.txt refusal or page change: report, keep going
                log(f"  ! Kijiji {search.get('name')} page {page}: {e}")
                break
            organic = [l for l in found if not l["is_top_ad"]]
            fresh = [l for l in organic if not known(l["id"]) and l["id"] not in seen]
            log(f"  Kijiji {search.get('name')} page {page}: {len(found)} ads, {len(fresh)} new")
            for lead in found:
                seen.setdefault(lead["id"], lead)
            if not organic or not fresh:
                break
    return list(seen.values())
