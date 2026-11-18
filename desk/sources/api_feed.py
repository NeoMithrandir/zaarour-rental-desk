"""Boards the desk reads only through access the board itself issues: an API key or a listings feed.

Rentals.ca, PadMapper/Zumper, Louer.ca and Facebook show every page behind a bot check or a sign-in, so the desk
does not read their pages. Each has a reader here that stays idle until its settings entry (`rentalsca`,
`padmapper`, `louer`, `facebook`) has the endpoint and key the company issues. The response is JSON; where its
listings sit and what each field is called are set in the entry (`items`, `fields`), because each company's format
is only known once it grants access. Requests go through the polite fetcher (honest user agent, spacing, back-off,
the permission's limits).
"""
from .http import HostCooling, NotModified, fetch

import json

BOARDS = ("rentalsca", "padmapper", "louer", "facebook")
LEAD_FIELDS = ("source_id", "url", "title", "snippet", "address", "lat", "lng", "price", "bedrooms", "unit_type",
               "poster_id", "poster_type", "posted_at", "image")


def missing(cfg):
    """What the board's entry still lacks before it can be read, or None."""
    api = (cfg or {}).get("api") or {}
    gaps = [k for k in ("endpoint", "key") if not str(api.get(k) or "").strip()]
    if not api.get("items") or not (api.get("fields") or {}).get("source_id"):
        gaps.append("items and fields (the format, from the company's documentation)")
    return ", ".join(gaps) or None


def hosts(settings, source):
    """The API host of a board read through issued access, for the permission's spacing."""
    import urllib.parse
    endpoint = (((settings.get(source) or {}).get("api") or {}).get("endpoint") or "") if source in BOARDS else ""
    return [urllib.parse.urlsplit(endpoint).netloc.lower()] if endpoint else []


def pick(item, path):
    """'location.address.street' or 'photos.0.url' from a JSON item; None when absent."""
    for part in str(path or "").split("."):
        if isinstance(item, list) and part.isdigit():
            item = item[int(part)] if int(part) < len(item) else None
        elif isinstance(item, dict):
            item = item.get(part)
        else:
            return None
    return item


def parse(source, data, api):
    leads = []
    for item in pick(data, api["items"]) or []:
        lead = {k: pick(item, p) for k, p in (api.get("fields") or {}).items() if k in LEAD_FIELDS}
        if not lead.get("source_id"):
            continue
        lead["source_id"] = str(lead["source_id"])
        for k in LEAD_FIELDS:
            lead.setdefault(k, None if k in ("lat", "lng", "price") else "")
        try:
            lead["price"] = int(float(lead["price"])) if lead["price"] not in (None, "") else None
        except (TypeError, ValueError):
            lead["price"] = None
        lead.update(id=f"{source}:{lead['source_id']}", source=source, search=f"{source} feed")
        leads.append(lead)
    return leads


def collect(source, settings, log=print, fetcher=fetch, known=None):
    cfg = settings.get(source) or {}
    gap = missing(cfg)
    if gap:
        raise RuntimeError(f"no access from {source} yet (still needed in settings.{source}.api: {gap})")
    api = cfg["api"]
    headers = {api.get("key_header", "Authorization"): api.get("key_prefix", "Bearer ") + api["key"],
               "Accept": "application/json"}
    known = known or (lambda _id: False)
    seen = {}
    for page in range(1, int(api.get("max_pages", 3)) + 1):
        sep = "&" if "?" in api["endpoint"] else "?"
        url = api["endpoint"] + (f"{sep}{api['page_param']}={page}" if api.get("page_param") else "")
        try:
            found = parse(source, json.loads(fetcher(url, headers=headers, issued_api=True)), api)
        except NotModified:
            break
        except HostCooling:
            if not seen:
                raise
            break
        fresh = [l for l in found if not known(l["id"]) and l["id"] not in seen]
        log(f"  {source} page {page}: {len(found)} ads, {len(fresh)} new")
        for lead in found:
            seen.setdefault(lead["id"], lead)
        if not api.get("page_param") or not fresh:
            break
    return list(seen.values())
