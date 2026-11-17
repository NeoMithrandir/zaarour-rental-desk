"""Opening ads for their full text, on boards whose search pages carry little (Craigslist, LogisQuébec).

After a board's search pages are read, the desk opens the ads it has no full text for yet: new ads first, newest
first, at most `details.max_per_run` per board per read, through the same polite fetcher (robots.txt, spacing,
back-off, and any limit from the board's permission). An ad opened once is never opened again.

Phone numbers and email addresses are blanked out of the text before it is stored: Stefan replies through the
board's own form. The checks see the original text once, in memory, so a known broker's number still flags the ad.
"""
import re

from .http import HostCooling

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DIGIT = r"(?:\d|zero|one|two|three|four|five|six|seven|eight|nine|z[ée]ro|un|deux|trois|quatre|cinq|sept|huit|neuf)"
DETAIL_FIELDS = ("snippet", "posted_at", "bedrooms", "unit_type")

WORDS = {w: str(i) for i, ws in enumerate((("zero", "zéro"), ("one", "un"), ("two", "deux"), ("three", "trois"),
                                           ("four", "quatre"), ("five", "cinq"), ("six",), ("seven", "sept"),
                                           ("eight", "huit"), ("nine", "neuf"))) for w in ws}
_D = _DIGIT
# Runs of digits (or spelled digits, to dodge filters: "514Two66One355") joined by the separators people put in phone numbers, and the groups in them.
RUN_RE = re.compile(rf"(?<![\w]){_D}(?:[\s.\-()]{{0,3}}{_D})*(?![\w])", re.I)
GROUP_RE = re.compile(rf"{_D}+", re.I)
# How the digits of one number are grouped: 514-555-1234, 514 5551234, 5145551234, each with an optional leading 1.
SHAPES = ((3, 3, 4), (3, 7), (10,), (1, 3, 3, 4), (1, 3, 7), (11,))


def nanp(digits):
    """'5145551234' or '15145551234' -> '514-555-1234'; None unless it is a North American number."""
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        return None
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def find_phones(text):
    """(start, end, number) for each North American number in the text.

    A number counts only when its digits are grouped like one, so the '2' of '4 1/2', a year or the rent next to
    it never shifts it. Runs of ten digits or more that fit no such shape come back with number None."""
    out = []
    for run in RUN_RE.finditer(text or ""):
        groups = [(m.start() + run.start(), m.end() + run.start(),
                   "".join(WORDS.get(t.lower(), t) for t in re.findall(_D, m.group(0), re.I)))
                  for m in GROUP_RE.finditer(run.group(0))]
        found, i = [], 0
        while i < len(groups):
            for shape in SHAPES:
                part = groups[i:i + len(shape)]
                n = nanp("".join(g[2] for g in part)) if tuple(len(g[2]) for g in part) == shape else None
                if n:
                    found.append((part[0][0], part[-1][1], n))
                    i += len(shape)
                    break
            else:
                i += 1
        # Groups no number took (an oddly grouped second number next to a first one): blank a stretch of 7+ digits.
        taken = {k for s, e, _n in found for k, g in enumerate(groups) if s <= g[0] < e}
        stretch = []
        for k, g in enumerate(groups + [None]):
            if g is not None and k not in taken:
                stretch.append(g)
                continue
            if sum(len(x[2]) for x in stretch) >= (10 if not found else 7):
                found.append((stretch[0][0], stretch[-1][1], None))
            stretch = []
        out += sorted(found)
    return out


def redact(text):
    text = EMAIL_RE.sub("[email removed]", text or "")
    for start, end, _n in reversed(find_phones(text)):
        start -= text[start - 1:start] == "("
        end += text[end:end + 1] == ")" and text[start:start + 1] == "(" and ")" not in text[start:end]
        text = text[:start] + "[phone removed]" + text[end:]
    # Spellings glued to words ("Call514Two66One355") have no boundary for find_phones to start at.
    def glued(m):
        digits = re.findall(_DIGIT, m.group(0), re.I)
        return "[phone removed]" if len(digits) >= 10 and re.search(r"\d", m.group(0)) else m.group(0)
    return re.sub(r"\b[0-9A-Za-zéÉ]{10,}\b", glued, text)


def enrich(conn, source, leads, parse_ad, settings, fetcher, log=print):
    """Add the full text, posting date and unit details to leads from this board that don't have them yet."""
    cfg = settings.get("details") or {}
    if source not in cfg.get("boards", []):
        return 0
    have = {r["id"]: r for r in conn.execute("SELECT * FROM leads WHERE source=? AND detailed=1", (source,))}
    known = {r[0] for r in conn.execute("SELECT id FROM leads WHERE source=?", (source,))}
    for lead in leads:
        if lead["id"] in have:
            # Keep the full text already stored, and check the ad against it again, not against the short summary.
            lead["summary_only"] = True
            for k in DETAIL_FIELDS:
                lead[k] = have[lead["id"]][k] or lead.get(k)
    todo = [l for l in leads if l["id"] not in have and l.get("url")]
    todo.sort(key=lambda l: l["id"] in known)  # new ads first, otherwise in page order (newest first)
    done = failed = 0
    for lead in todo[:int(cfg.get("max_per_run", 60))]:
        try:
            extra = parse_ad(fetcher(lead["url"], conditional=False))
        except HostCooling as e:
            log(f"  ! {source} ads: {e}")
            break
        except Exception as e:  # a removed ad or a page change: skip it, keep going
            failed += 1
            if failed <= 3:
                log(f"  ! {source} ad {lead['source_id']}: {e}")
            continue
        text = (extra.pop("text", "") or "").strip()
        if text:
            lead["check_text"] = text
            lead["snippet"] = redact(text)[:4000]
        for k, v in extra.items():
            if v and not lead.get(k):
                lead[k] = v
        lead["detailed"] = 1
        done += 1
    left = len(todo) - done - failed
    log(f"  {source}: opened {done} ads for their full text" + (f", {left} left for later reads" if left > 0 else ""))
    return done
