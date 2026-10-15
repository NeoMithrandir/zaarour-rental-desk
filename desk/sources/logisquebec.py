"""LogisQuébec rentals by region (Île-de-Montréal, Laval, Montérégie...), newest first, 10 per page."""
import html as htmllib
import re

from .http import HostCooling, NotModified, fetch, ld_json, money

BASE = "https://www.logisquebec.com/a-louer/"
CARD_RE = re.compile(r'<a href="(/[^"]*-l(\d+))" class="property-result[^"]*">(.*?)</a>\s*</li>', re.S)


def _text(pattern, block):
    m = re.search(pattern, block, re.S)
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def parse(page, region=""):
    geo = {}
    for block in ld_json(page):
        if isinstance(block, dict) and block.get("@type") == "SearchResultsPage":
            for lst in block.get("mainEntity", []):
                for e in lst.get("itemListElement", []):
                    it = e.get("item", {})
                    g = it.get("geo") or {}
                    geo[it.get("url", "").split("/")[-1]] = (g.get("latitude"), g.get("longitude"))
    leads = []
    for path, ad_id, card in CARD_RE.findall(page):
        street = _text(r'<span class="adresse-rue">(.*?)</span>', card)
        city = _text(r'<span class="adresse-ville">(.*?)</span>', card)
        img = re.search(r'<img src="(https://i\.logisquebec\.com[^"]+)"', card)
        lat, lng = geo.get(path.lstrip("/"), (None, None))
        leads.append({
            "id": f"logisquebec:{ad_id}", "source": "logisquebec", "source_id": ad_id,
            "url": "https://www.logisquebec.com" + path,
            "title": _text(r"<h2>(.*?)</h2>", card) or _text(r'box-result-unit-12A">(.*?)</p>', card),
            "snippet": _text(r'<div class="box-result-unit-3A">(.*?)</div>', card),
            # City like 'Lachine (Montréal)': keep the borough as the city segment.
            "address": ", ".join(p for p in [street, re.sub(r"\s*\(.*?\)", "", city), "QC"] if p),
            "lat": lat, "lng": lng,
            "price": money(_text(r'<span class="prix-valeur">(.*?)</span>', card)),
            "bedrooms": "", "unit_type": _text(r'box-result-unit-12A">(.*?)</p>', card),
            "poster_id": "", "poster_type": "", "posted_at": "", "image": img.group(1) if img else "",
            "search": f"LogisQuébec {region}",
        })
    return leads


def collect(settings, log=print, fetcher=fetch, known=None):
    """Newest first per region; stops at the first page with nothing new (caught up) or unchanged."""
    cfg = settings.get("logisquebec", {})
    pages = int(cfg.get("pages_per_region", 3))
    known = known or (lambda _id: False)
    seen = {}
    for region in cfg.get("regions", ["ile-de-montreal", "laval", "monteregie"]):
        for page in range(1, pages + 1):
            url = BASE + region + ("" if page == 1 else f"/{page}")
            try:
                found = parse(fetcher(url), region)
            except NotModified:
                log(f"  LogisQuébec {region}: no change since last check")
                break
            except HostCooling as e:
                if not seen:  # nothing read at all: the board failed this time
                    raise
                log(f"  ! LogisQuébec: {e}")
                return list(seen.values())
            except Exception as e:
                log(f"  ! LogisQuébec {region} page {page}: {e}")
                break
            fresh = [l for l in found if not known(l["id"]) and l["id"] not in seen]
            log(f"  LogisQuébec {region} page {page}: {len(found)} ads, {len(fresh)} new")
            for lead in found:
                seen.setdefault(lead["id"], lead)
            if not fresh:
                break
    return list(seen.values())
