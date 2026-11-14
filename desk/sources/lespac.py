"""LesPAC housing rentals (Immobilier > Location > Logements), newest first, 20 ads per page.

LesPAC draws its result list in the browser, but the page itself carries the data the list is drawn from
(`var searchResponse = {...}`), so one plain GET is enough. Sorting by date is a preference LesPAC keeps in its
own `searchSortOrder` cookie; the desk sends that cookie, as a browser would after choosing "Plus récentes".
Sorted by date the list covers all of Quebec (the city only changes the distance shown), about 30 to 40 ads a
day, so page 1 every few hours keeps up; the area check sets aside ads outside Stefan's areas.
Further pages come only through LesPAC's search API (a POST), which the desk does not use.
"""
import json
import re
from datetime import datetime

from .http import fetch

URL = "https://www.lespac.com/montreal/immobilier-location-logements_b457g17567k1R2.jsa"
SORT_NEWEST = {"Cookie": "searchSortOrder=Date+desc"}
MARKER = "var searchResponse = "
POSTER = {"INDIVIDUAL": "owner", "CORPORATE": "professional"}


def _tracking(ad, key):
    return ((ad.get("searchPageTrackingInfo") or {}).get(key) or {}).get("value", "")


def _address(city_label):
    """'Montréal / Centre-Sud / Centre-Ville' -> 'Centre-Ville, Centre-Sud, Montréal, QC'."""
    parts = [p.strip() for p in (city_label or "").split("/") if p.strip()]
    return ", ".join(list(reversed(parts)) + ["QC"]) if parts else ""


def parse(page):
    i = page.find(MARKER)
    if i < 0:
        raise ValueError("LesPAC page has no search results data (the page layout may have changed)")
    data, _end = json.JSONDecoder().raw_decode(page[i + len(MARKER):])
    leads = []
    for ad in data.get("searchResults") or []:
        ad_id = str(ad.get("listingPublicId") or "")
        if not ad_id:
            continue
        stamp = ad.get("publicReleaseTimestamp")
        leads.append({
            "id": f"lespac:{ad_id}", "source": "lespac", "source_id": ad_id,
            "url": (ad.get("listingDisplayUrl") or "").split("?")[0],
            "title": (ad.get("title") or "").strip(),
            "snippet": re.sub(r"\*+", " ", ad.get("description") or "").strip()[:500],
            "address": _address(ad.get("cityLabel")),
            "lat": None, "lng": None,
            "price": int(ad["price"]) if ad.get("price") else None,
            "bedrooms": "", "unit_type": "",
            "poster_id": _tracking(ad, "listing-dealerid"),
            "poster_type": POSTER.get(_tracking(ad, "listing-advertiser-type"), ""),
            "posted_at": datetime.fromtimestamp(stamp / 1000).isoformat(timespec="seconds") if stamp else "",
            "image": ad.get("mainImageUrl") or "",
            "search": "LesPAC newest",
        })
    return leads


def collect(settings, log=print, fetcher=fetch, known=None):
    cfg = settings.get("lespac", {})
    known = known or (lambda _id: False)
    leads = parse(fetcher(cfg.get("url", URL), headers=SORT_NEWEST))
    fresh = [l for l in leads if not known(l["id"])]
    log(f"  LesPAC newest: {len(leads)} ads, {len(fresh)} new")
    if leads and len(fresh) == len(leads):
        log("  LesPAC: every ad on the page was new, so a few older ones may have been missed since the last read")
    return leads
