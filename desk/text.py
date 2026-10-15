"""Small text helpers shared by the checks: accent folding, address normalisation."""
import re
import unicodedata

_STREET_WORDS = {
    "rue": "", "street": "st", "st": "st", "avenue": "", "av": "", "ave": "",
    "boulevard": "", "boul": "", "blvd": "", "bd": "", "chemin": "", "ch": "",
    "place": "", "pl": "", "croissant": "", "crescent": "", "cres": "",
    "montee": "", "rang": "", "drive": "", "dr": "", "road": "", "rd": "",
    "de": "", "du": "", "des": "", "la": "", "le": "", "l": "", "d": "",
    "o": "w", "ouest": "w", "west": "w", "w": "w",
    "e": "e", "est": "e", "east": "e",
    "saint": "st", "sainte": "ste",
}

POSTAL_RE = re.compile(r"\b([A-Za-z]\d[A-Za-z])\s?(\d[A-Za-z]\d)?\b")


def fold(s):
    """Lower-case and strip accents: 'Île-Bizard' -> 'ile-bizard'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace("’", "'")


def postal_fsa(address):
    """First three characters of a Canadian postal code in the address, upper-case, or ''."""
    for m in POSTAL_RE.finditer(address or ""):
        fsa = m.group(1).upper()
        if fsa[0] in "HJGK":  # Quebec FSAs
            return fsa
    return ""


def city_of(address):
    """City segment of a Kijiji-style address: '123 Rue X, Pierrefonds, QC H8Z 1V2' -> 'pierrefonds'."""
    parts = [p.strip() for p in fold(address).split(",")]
    for i, p in enumerate(parts):
        if p.startswith("qc") or p.startswith("quebec"):
            return parts[i - 1] if i > 0 else ""
    if len(parts) >= 2:
        return parts[-1]
    # A bare 'Pierrefonds' is a city; '123 Rue X' is not.
    return parts[0] if parts and not re.match(r"\s*\d", parts[0]) else ""


def address_key(address):
    """Civic number + street words, for matching the same building across sources.

    '4005, boul. des Sources O., Dollard-des-Ormeaux' -> '4005 sources w'
    Returns '' when there is no civic number (can't match reliably).
    """
    a = fold(address).split(",")
    if not a:
        return ""
    head = a[0]
    # '4005 Sources' may be split as '4005, boul. des Sources'
    if re.fullmatch(r"\s*\d+[a-z]?\s*", head) and len(a) > 1:
        head = head + " " + a[1]
    head = re.sub(r"^(app|apt|unit|suite|#)\s*\S+\s+", "", head.strip())
    head = re.sub(r"^\d+\s*-\s*(?=\d)", "", head)  # '12-4005 x' -> '4005 x' (unit-civic)
    m = re.match(r"\s*(\d+)[a-z]?\b(.*)", head)
    if not m:
        return ""
    number, rest = m.group(1), m.group(2)
    words = re.findall(r"[a-z0-9]+", rest)
    out = []
    for w in words:
        w2 = _STREET_WORDS.get(w, w)
        if w2:
            out.append(w2)
    if len(out) > 1 and out[-1] == "st":  # 'Main St' / 'Main Street' -> 'main'
        out.pop()
    return " ".join([number] + out)
