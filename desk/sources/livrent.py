"""liv.rent city pages. They list buildings with units for rent (mostly managed buildings), 16 per page."""

from .http import HostCooling, NotModified, fetch, ld_json

BASE = "https://liv.rent/rental-listings/city/"


def parse(page, city=""):
    leads = []
    for block in ld_json(page):
        if not (isinstance(block, dict) and block.get("@type") == "CollectionPage"):
            continue
        for e in (block.get("mainEntity") or {}).get("itemListElement", []):
            it = e.get("item", {})
            ent = it.get("mainEntity") or {}
            a = ent.get("address") or {}
            g = ent.get("geo") or {}
            url = it.get("url", "")
            ad_id = url.split("/building/")[-1].split("/")[0] if "/building/" in url else url
            offer = it.get("offers") or {}
            price = offer.get("lowPrice") or offer.get("price")
            leads.append({
                "id": f"livrent:{ad_id}", "source": "livrent", "source_id": ad_id, "url": url,
                "title": it.get("name", ""), "snippet": it.get("description", ""),
                "address": ", ".join(p for p in [a.get("streetAddress"), a.get("addressLocality"),
                                                 f"QC {a.get('postalCode', '')}".strip()] if p),
                "lat": g.get("latitude"), "lng": g.get("longitude"),
                "price": int(float(price)) if price else None, "bedrooms": "", "unit_type": "building",
                "poster_id": "", "poster_type": "professional", "posted_at": "", "image": "",
                "search": f"liv.rent {city}",
            })
    return leads


def collect(settings, log=print, fetcher=fetch, known=None):
    cfg = settings.get("livrent", {})
    seen = {}
    for city in cfg.get("cities", ["montreal", "laval", "longueuil", "brossard"]):
        try:
            found = parse(fetcher(BASE + city), city)
        except NotModified:
            log(f"  liv.rent {city}: no change since last check")
            continue
        except HostCooling as e:
            if not seen:
                raise
            log(f"  ! liv.rent: {e}")
            break
        except Exception as e:
            log(f"  ! liv.rent {city}: {e}")
            continue
        log(f"  liv.rent {city}: {len(found)} buildings")
        for lead in found:
            seen.setdefault(lead["id"], lead)
    return list(seen.values())
