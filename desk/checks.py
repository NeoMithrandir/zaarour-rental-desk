"""The automatic checks Stefan does by hand today (plan items 1.3, 1.8, 1.9).

Each check adds flags to a lead. A flag is {"code", "level", "text"}:
  level "hide"  -> not a landlord lead at all (tenant ad, out of area); kept in the DB, not shown by default
  level "broker"-> looks like a broker; shown in the "Likely broker" tab
  level "check" -> needs a quick manual look (not sure)
  level "info"  -> just a tag (property manager, small unit)
"""
import csv
import math
import re
from pathlib import Path

from .config import CONFIG_DIR, DATA_DIR
from .text import address_key, city_of, fold, postal_fsa

# Ads by tenants, not landlords: lease transfers, sublets, rooms, "looking for".
TENANT_PATTERNS = [
    r"cession de bail", r"transfert de bail", r"transfer(t)? de bail", r"lease transfer", r"lease takeover",
    r"reprise de bail", r"sous[- ]?locat", r"sous[- ]?lou", r"sublet", r"sub-let", r"sublease",
    r"chambre a louer", r"room for rent", r"room available", r"colocat", r"roommate", r"coloc\b",
    r"\bcherche\b.{0,30}\b(logement|appartement|appart|4 ?1/2|3 ?1/2)", r"looking for (an? )?(apartment|room|place)",
    r"residence etudiante", r"student residence",
]
TENANT_RE = re.compile("|".join(TENANT_PATTERNS))

PM_PATTERNS = re.compile(r"gestion|management|property manager|gestionnaire|immeubles|appartements .{0,20}inc|\binc\b|ltee|\bltd\b")
SMALL_UNIT_RE = re.compile(r"\b1 ?1/2\b|\b1\.5\b|1½|studio|bachelor|garconniere")
PHONE_RE = re.compile(r"(\d{3})\D{0,3}(\d{3})\D{0,2}(\d{4})")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


GENERIC_CITIES = {"montreal", "montreal, qc", "ville de montreal", "quebec", ""}


def _km(lat1, lng1, lat2, lng2):
    """Distance in km (equirectangular; fine at city scale)."""
    x = math.radians(lng2 - lng1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371 * math.hypot(x, y)


def _digits(phone):
    d = re.sub(r"\D", "", phone or "")
    return d[-10:]


class Checker:
    def __init__(self, areas, settings, known_posters=None):
        self.areas = areas
        self.min_rent = settings.get("min_rent", 0)
        self.fsa_to_area, self.prefix_to_area = {}, {}
        self.city_to_area, self.catch_all_cities = {}, {}
        self.phase = {name: int(a.get("phase", 1)) for name, a in areas.items()}
        self.notes = {name: a["note"] for name, a in areas.items() if a.get("note")}
        for name, a in areas.items():
            for f in a.get("fsa", []):
                (self.fsa_to_area if len(f) == 3 else self.prefix_to_area)[f.upper()] = name
            cities = self.catch_all_cities if a.get("catch_all") else self.city_to_area
            for c in a.get("cities", []):
                cities[fold(c)] = name
        self.keywords, self.phones, self.emails, self.poster_ids = [], set(), set(), {}
        self._load_brokers(CONFIG_DIR / "known_brokers.csv")
        for pid, label in (known_posters or {}).items():
            self.poster_ids[pid] = label
        self.centris = self._load_centris(DATA_DIR / "centris_active_rentals.csv")

    # -- reference data ---------------------------------------------------
    def _load_brokers(self, path):
        if not Path(path).exists():
            return
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                kind, value, label = row["kind"].strip(), row["value"].strip(), row.get("label", "").strip()
                if kind == "keyword":
                    self.keywords.append((re.compile(r"(?<![a-z0-9])" + re.escape(fold(value)) + r"(?![a-z0-9])"), label or value))
                elif kind == "phone":
                    self.phones.add(_digits(value))
                elif kind == "email":
                    self.emails.add(value.lower())
                elif kind == "poster_id":
                    self.poster_ids[value] = label or "Known broker"

    def _load_centris(self, path):
        """Optional export of active Centris/Matrix rental listings (needs Nesi's Matrix access).

        Any CSV with an address column works; we match on civic number + street.
        """
        keys = {}
        if not Path(path).exists():
            return None
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = [c for c in (reader.fieldnames or []) if "adress" in fold(c) or "address" in fold(c)]
            for row in reader:
                addr = " ".join(row[c] for c in cols if row.get(c))
                k = address_key(addr)
                if k:
                    keys[k] = row.get("MLS") or row.get("No Centris") or row.get("centris") or "on Centris"
        return keys

    # -- the checks -------------------------------------------------------
    def _nearest(self, lat, lng, catch_all):
        best, best_d = "", None
        for name, a in self.areas.items():
            if bool(a.get("catch_all")) != catch_all or not a.get("center"):
                continue
            d = _km(lat, lng, *a["center"])
            if d <= float(a.get("radius_km", 0)) and (best_d is None or d < best_d):
                best, best_d = name, d
        return best

    def area_of(self, address, lat=None, lng=None):
        """Postal code first, then city name, then map position; catch-all areas ('Montreal (other)') last."""
        fsa = postal_fsa(address)
        if fsa:
            return self.fsa_to_area.get(fsa) or self.prefix_to_area.get(fsa[:2], "")
        city = city_of(address)
        if city in self.city_to_area:
            return self.city_to_area[city]
        for c, name in self.city_to_area.items():
            if len(c) > 4 and c in city:
                return name
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            lat = lng = None
        # Named places inside a catch-all ('Saint-Léonard', 'Verdun') beat map positions;
        # the bare region name ('Montréal') only applies once the map position says nothing.
        generic = city in GENERIC_CITIES
        if not generic:
            if city in self.catch_all_cities:
                return self.catch_all_cities[city]
            for c, name in self.catch_all_cities.items():
                if len(c) > 4 and c not in GENERIC_CITIES and c in city:
                    return name
        if lat is not None:
            near = self._nearest(lat, lng, False)
            if near:
                return near
        if city in self.catch_all_cities:
            return self.catch_all_cities[city]
        if lat is not None:
            return self._nearest(lat, lng, True)
        return ""

    def run(self, lead):
        """Return (area, flags) for a lead dict with title/snippet/address/price/poster_type/poster_id."""
        flags = []
        # check_title / check_text: a scanned ad's title and text before phone numbers were blanked out (never stored).
        text = fold("\n".join([lead.get("check_title") or lead.get("title") or "",
                               lead.get("check_text") or lead.get("snippet") or ""]))

        if TENANT_RE.search(text):
            flags.append({"code": "tenant_ad", "level": "hide", "text": "Tenant ad (lease transfer, sublet or room)"})

        area = self.area_of(lead.get("address", ""), lead.get("lat"), lead.get("lng"))
        if not area and not (lead.get("address") or "").strip() and lead.get("lat") is None:
            # Captured or alert ads often carry no address: Stefan decides, rather than hiding them.
            flags.append({"code": "area_unknown", "level": "check", "text": "No address in the ad: check the area"})
        elif not area:
            flags.append({"code": "out_of_area", "level": "hide", "text": "Outside every target area"})
        elif self.phase.get(area, 1) > 1:
            flags.append({"code": "phase2", "level": "info", "text": "Phase 2 area"})
        if area in self.notes:
            flags.append({"code": "area_note", "level": "info", "text": self.notes[area]})

        hits = [label for rx, label in self.keywords if rx.search(text)]
        phones = {"".join(m.groups()) for m in PHONE_RE.finditer(text + "\n" + (lead.get("phone") or ""))}
        emails = {e.lower() for e in EMAIL_RE.findall((lead.get("snippet") or "") + " " + (lead.get("check_text") or ""))}
        if phones & self.phones:
            hits.append("Phone matches a known broker")
        if emails & self.emails:
            hits.append("Email matches a known broker")
        hits += lead.get("number_hits") or []  # from a check of the original text, kept when rechecking it blanked
        pid = lead.get("poster_id")
        if pid and pid in self.poster_ids:
            hits.append(f"Poster marked before: {self.poster_ids[pid]}")
        if hits:
            label = "Our own listing" if "Our own listing" in hits else "Likely broker"
            flags.append({"code": "broker", "level": "broker", "text": f"{label}: " + ", ".join(dict.fromkeys(hits))})

        if lead.get("poster_type") == "professional" and not hits:
            flags.append({"code": "property_manager", "level": "info",
                          "text": "Professional poster, probably a property manager (keep: one PM can mean 80 units)"})
        elif PM_PATTERNS.search(text) and not hits:
            flags.append({"code": "property_manager", "level": "info", "text": "Mentions management company"})

        price = lead.get("price")
        if SMALL_UNIT_RE.search(text) or (price and self.min_rent and price < self.min_rent):
            flags.append({"code": "small", "level": "info", "text": "Small unit or low rent"})

        key = address_key(lead.get("address", ""))
        if self.centris is None:
            if key:
                flags.append({"code": "centris_unchecked", "level": "info",
                              "text": "Not checked against Centris yet (no Matrix export loaded)"})
            else:
                flags.append({"code": "centris_unchecked", "level": "check",
                              "text": "No civic number in the ad: check Centris by hand"})
        elif key and key in self.centris:
            flags.append({"code": "on_centris", "level": "broker", "text": f"Address is already listed on Centris ({self.centris[key]})"})
        elif not key:
            flags.append({"code": "centris_unchecked", "level": "check", "text": "No civic number in the ad: check Centris by hand"})

        return area, flags


def bucket(flags):
    """Which tab of the review screen a lead goes in."""
    levels = {f["level"] for f in flags}
    if "hide" in levels:
        return "hidden"
    if "broker" in levels:
        return "broker"
    return "review"
