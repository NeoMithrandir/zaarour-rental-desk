"""Craigslist Montréal, apartments posted by owners (purveyor=owner), newest first.

One search page lists every owner ad in the Montreal region. The page carries the ads as
JSON-LD (title, map position, bedrooms) plus a plain list with each ad's link, price and
neighbourhood; the two are in the same order.
"""
import re

from .http import fetch, ld_json, money

URL = "https://montreal.craigslist.org/search/apa?purveyor=owner&sort=date"
LI_RE = re.compile(r'<li class="cl-static-search-result" title="([^"]*)">(.*?)</li>', re.S)


def _div(cls, block):
    m = re.search(rf'<div class="{cls}">\s*([^<]*?)\s*</div>', block)
    return m.group(1) if m else ""


def parse(html):
    items = []
    for block in ld_json(html):
        if isinstance(block, dict) and block.get("@type") == "ItemList":
            items = [e.get("item", {}) for e in block.get("itemListElement", [])]
    leads = []
    for i, (title, block) in enumerate(LI_RE.findall(html)):  # same order as the JSON-LD list
        link = re.search(r'<a href="([^"]+)"', block)
        if not link:
            continue
        url, price, hood = link.group(1), _div("price", block), _div("location", block)
        ld = items[i] if i < len(items) else {}
        addr = ld.get("address") or {}
        locality = addr.get("addressLocality") or ""
        # Craigslist's own locality is coarse (it files Saint-Léonard under Laval), so the poster's
        # neighbourhood is the address; the map position settles the rest.
        address = ", ".join(p for p in [hood.strip(), "QC"] if p)
        ad_id = url.rstrip("/").split("/")[-1]
        leads.append({
            "id": f"craigslist:{ad_id}", "source": "craigslist", "source_id": ad_id, "url": url,
            "title": title.strip(), "snippet": f"Craigslist region: {locality}" if locality else "", "address": address,
            "lat": ld.get("latitude"), "lng": ld.get("longitude"), "price": money(price),
            "bedrooms": str(ld.get("numberOfBedrooms") or ""), "unit_type": (ld.get("@type") or "").lower(),
            "poster_id": "", "poster_type": "owner", "posted_at": "", "image": "", "search": "Craigslist owners",
        })
    return leads


def collect(settings, log=print, fetcher=fetch, known=None):
    leads = parse(fetcher(settings.get("craigslist", {}).get("url", URL)))
    log(f"  Craigslist owners: {len(leads)} ads")
    return leads
