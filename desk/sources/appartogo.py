"""Appartogo region pages (apartments, condos), newest first, about a dozen ads per page.

The region pages (`/fr/<category>/canada/quebec/<region>?page=N`) are drawn on the server with every card in
the HTML. robots.txt disallows `/recherche`, `/en/search`, `/search/markers` and `/listing/card`, so the desk
reads only these region pages. Ads are mostly managed buildings.
"""
import html as htmllib
import re

from .http import HostCooling, NotModified, fetch

BASE = "https://appartogo.com/fr/"
CARD_SPLIT = re.compile(r'<div itemscope itemtype="https://schema.org/Product" class="listing listing-card" '
                        r'data-listing-id="(\d+)">')
STREET_RE = re.compile(r"\d+(-\d+)?\s+[^\W\d]")  # '6850 Boul. Newman', not '4 1/2 au rez-de-chaussée'


def _text(pattern, block):
    m = re.search(pattern, block, re.S)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else ""


def page_url(category, region, page=1):
    return f"{BASE}{category}/canada/quebec/{region}" + ("" if page == 1 else f"?page={page}")


def parse(page, region="", category=""):
    parts = CARD_SPLIT.split(page)
    leads = []
    for ad_id, card in zip(parts[1::2], parts[2::2]):
        href = re.search(r'<a class="tile-link" href="([^"]+)"', card)
        price = re.search(r'itemprop="price" content="([\d.]+)"', card)
        img = re.search(r'<img[^>]*src="(https://cdn\.appartogo\.com[^"]+)"', card)
        title = _text(r'<h3[^>]*class="card-title[^"]*">(.*?)</h3>', card)
        city = _text(r'<p class="listing-city">(.*?)</p>', card)
        # Titles look like 'EQ8 Apartments | 6850 Boul. Newman, Montreal · 1 ch.' or a plain sentence.
        street = re.split(r"\s+·\s+", title.split("|")[-1])[0].strip()
        street = re.sub(r",\s*(Montr[ée]al|Laval|Longueuil)\s*$", "", street, flags=re.I) if STREET_RE.match(street) else ""
        leads.append({
            "id": f"appartogo:{ad_id}", "source": "appartogo", "source_id": ad_id,
            "url": "https://appartogo.com" + href.group(1) if href else "",
            "title": title, "snippet": "",
            "address": ", ".join(p for p in [street, city, "QC"] if p),
            "lat": None, "lng": None,
            "price": int(float(price.group(1))) if price else None,
            "bedrooms": _text(r'class="pieces bedrooms"[^>]*>(\d+)', card),
            "unit_type": category.replace("-louer", ""),
            "poster_id": "", "poster_type": "", "posted_at": "", "image": img.group(1) if img else "",
            "search": f"Appartogo {region}",
        })
    return leads


def collect(settings, log=print, fetcher=fetch, known=None):
    """Newest first per category and region; stops at the first page with nothing new (caught up) or unchanged."""
    cfg = settings.get("appartogo", {})
    pages = int(cfg.get("pages_per_region", 3))
    known = known or (lambda _id: False)
    seen = {}
    for category in cfg.get("categories", ["appartement-louer", "condo-louer"]):
        for region in cfg.get("regions", ["ile-de-montreal", "laval", "monteregie"]):
            for page in range(1, pages + 1):
                try:
                    found = parse(fetcher(page_url(category, region, page)), region, category)
                except NotModified:
                    log(f"  Appartogo {category} {region}: no change since last check")
                    break
                except HostCooling as e:
                    if not seen:  # nothing read at all: the board failed this time
                        raise
                    log(f"  ! Appartogo: {e}")
                    return list(seen.values())
                except Exception as e:
                    log(f"  ! Appartogo {category} {region} page {page}: {e}")
                    break
                fresh = [l for l in found if not known(l["id"]) and l["id"] not in seen]
                log(f"  Appartogo {category} {region} page {page}: {len(found)} ads, {len(fresh)} new")
                for lead in found:
                    seen.setdefault(lead["id"], lead)
                if not fresh:
                    break
    return list(seen.values())
