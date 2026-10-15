"""The learning loop: what Stefan approves, what landlords answer, written up as plain markdown.

Inputs are the desk's own records: every lead Stefan decided (Approve / Reject with a reason / Later),
what happened after a message was sent (replied, presentation booked, listing signed, no reply), the
template each message came from and whether Stefan edited it, plus the listing's signals (board, area
and phase, rent, owner or property manager, ad age, language, title words). The events table keeps
the full history; the rates below use each lead's current state, so an undone mis-click doesn't count.

Output goes to rentals-desk/learnings/ (see the README there): one summary with guidance written as
instructions an agent can follow, one file per board, one per signal, plus findings.json. Any finding
from fewer than `learn.min_sample` decisions is marked tentative; below `learn.min_show` it is left out.
"""
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT
from .text import fold

OUT_DIR = ROOT / "learnings"
SOURCE_NAMES = {"kijiji": "Kijiji", "craigslist": "Craigslist", "logisquebec": "LogisQuébec", "livrent": "liv.rent",
                "facebook": "Facebook Marketplace", "group": "Facebook groups", "lespac": "LesPAC", "rentalsca": "Rentals.ca",
                "padmapper": "PadMapper / Zumper", "appartogo": "Appartogo", "louer": "Louer.ca", "duproprio": "DuProprio",
                "other": "Other"}
STOP = set("""a au aux avec ce ces dans de des du en et la le les leur ou par pour sur un une est sont
the and for with near from all in of to on at by is are an or very new""".split())
REPLIED = ("replied", "presentation", "listing")


# ---------------------------------------------------------------- signals
def _dt(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def rent_band(price):
    if not price:
        return "rent not shown"
    for top, label in [(1200, "under $1,200"), (1600, "$1,200–1,599"), (2000, "$1,600–1,999"), (2500, "$2,000–2,499")]:
        if price < top:
            return label
    return "$2,500 and up"


def age_band(posted, decided):
    p, d = _dt(posted), _dt(decided)
    if not p or not d:
        return "age unknown"
    days = (d - p).days
    for top, label in [(1, "posted that day"), (4, "up 1–3 days"), (8, "up 4–7 days"), (15, "up 1–2 weeks"), (31, "up 2–4 weeks")]:
        if days < top:
            return label
    return "up over a month"


def poster_kind(lead, flags):
    if "broker" in flags:
        return "flagged as broker"
    if lead.get("poster_type") == "owner":
        return "owner"
    if lead.get("poster_type") == "professional" or "property_manager" in flags:
        return "property manager"
    return "not stated"


def title_words(title):
    words = [w for w in re.findall(r"[a-z0-9½/]+", fold(title or "")) if len(w) > 2 and w not in STOP and not w.isdigit()]
    return set(words)


def rows(conn):
    """One dict per decided lead: its signals and what happened."""
    out = []
    for r in conn.execute("SELECT * FROM leads WHERE status IN ('approved','sent','rejected','later')"):
        d = dict(r)
        flags = {f["code"] for f in json.loads(d.get("flags") or "[]")}
        out.append({
            "id": d["id"], "source": d["source"] or "other", "area": d.get("area") or "area unknown",
            "phase": f"phase {d['phase']}" if d.get("phase") else "phase unknown",
            "rent": rent_band(d.get("price")), "poster": poster_kind(d, flags),
            "age": age_band(d.get("posted_at"), d.get("decided_at") or d.get("sent_at")),
            "lang": {"fr": "French", "en": "English"}.get(d.get("lang"), "unknown"),
            "words": title_words(d.get("title")),
            "approved": d["status"] in ("approved", "sent"), "rejected": d["status"] == "rejected",
            "later": d["status"] == "later", "reason": d.get("reject_reason") or "",
            "sent": d["status"] == "sent", "replied": d.get("outcome") in REPLIED,
            "presentation": d.get("outcome") in ("presentation", "listing"), "signed": d.get("outcome") == "listing",
            "outcome_known": bool(d.get("outcome")), "template": d.get("template") or "",
            "decided_at": d.get("decided_at") or d.get("sent_at") or "",
        })
    return out


def edited_by_lead(conn):
    """Whether Stefan edited the message he sent, from the event log (last approve/sent event that says)."""
    out = {}
    for r in conn.execute("SELECT lead_id, edited FROM events WHERE action IN ('approve','sent') AND edited IS NOT NULL ORDER BY id"):
        out[r["lead_id"]] = bool(r["edited"])
    return out


# ---------------------------------------------------------------- statistics
def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(x):
    return f"{round(100 * x)}%"


class Stat:
    def __init__(self, label, k, n, base, min_sample):
        self.label, self.k, self.n, self.base = label, k, n, base
        self.rate = k / n if n else 0.0
        self.lo, self.hi = wilson(k, n)
        self.tentative = n < min_sample
        self.clear = not self.tentative and (self.lo > base or self.hi < base)

    @property
    def tag(self):
        if self.tentative:
            return "tentative"
        return ("higher" if self.lo > self.base else "lower") if self.clear else "no clear difference"

    def as_dict(self):
        return {"label": self.label, "k": self.k, "n": self.n, "rate": round(self.rate, 3),
                "interval": [round(self.lo, 3), round(self.hi, 3)], "baseline": round(self.base, 3), "tag": self.tag}


def breakdown(items, key, hit, min_show, min_sample):
    """Rate of `hit` per value of `key` among items, against the overall rate."""
    n_all = len(items)
    base = sum(1 for i in items if hit(i)) / n_all if n_all else 0.0
    groups = defaultdict(list)
    for i in items:
        for v in (key(i) if isinstance(key(i), (set, list)) else [key(i)]):
            groups[v].append(i)
    stats = [Stat(v, sum(1 for i in g if hit(i)), len(g), base, min_sample) for v, g in groups.items() if len(g) >= min_show]
    return base, sorted(stats, key=lambda s: (-s.n, s.label))


# ---------------------------------------------------------------- writing
SIGNALS = [  # (file, heading, key)
    ("area", "Area", lambda r: r["area"]), ("phase", "Phase", lambda r: r["phase"]),
    ("rent", "Rent", lambda r: r["rent"]), ("poster", "Owner or property manager", lambda r: r["poster"]),
    ("ad-age", "How long the ad had been up", lambda r: r["age"]), ("language", "Ad language", lambda r: r["lang"]),
    ("board", "Board", lambda r: r["source"]),
]


def table(stats, what):
    if not stats:
        return "_Not enough decisions yet._\n"
    lines = [f"| | {what} | Decisions | Range (95%) | Reading |", "|---|---|---|---|---|"]
    for s in stats:
        lines.append(f"| {s.label} | {pct(s.rate)} ({s.k} of {s.n}) | {s.n} | {pct(s.lo)}–{pct(s.hi)} | {s.tag} |")
    return "\n".join(lines) + "\n"


def section(items, key, title, cfg):
    mn, ms = cfg["min_show"], cfg["min_sample"]
    out = []
    base, st = breakdown(items, key, lambda r: r["approved"], mn, ms)
    out.append(f"### Approval rate by {title.lower()}\n\nOverall: {pct(base)} of {len(items)} decisions.\n\n" + table(st, "Approved"))
    sent = [i for i in items if i["sent"] and i["outcome_known"]]
    base, st = breakdown(sent, key, lambda r: r["replied"], mn, ms)
    out.append(f"### Reply rate by {title.lower()}\n\nOverall: {pct(base)} of {len(sent)} sent messages with a recorded outcome.\n\n"
               + table(st, "Replied"))
    return "\n".join(out), {"approval": [s.as_dict() for s in breakdown(items, key, lambda r: r["approved"], mn, ms)[1]],
                            "reply": [s.as_dict() for s in breakdown(sent, key, lambda r: r["replied"], mn, ms)[1]]}


HEADER = """<!-- Written by the rentals desk (python3 -m desk learn) on {at}. Regenerated from the desk's records: don't edit by hand. -->
"""


def guidance(items, sent, cfg, edited):
    """Plain rules an agent (or a new team member) can follow, each with its evidence and confidence."""
    rules = []
    mn, ms = cfg["min_show"], cfg["min_sample"]
    for _f, title, key in SIGNALS:
        for what, pool, hit, verb in [("approved", items, lambda r: r["approved"], "approves"),
                                      ("replied", sent, lambda r: r["replied"], "landlords reply")]:
            _base, st = breakdown(pool, key, hit, mn, ms)
            for s in st:
                if s.clear or (s.tentative and s.n >= mn and abs(s.rate - s.base) >= 0.2):
                    direction = "more" if s.rate > s.base else "less"
                    conf = "tentative" if s.tentative else "supported"
                    act = "Prioritise" if direction == "more" else "Deprioritise"
                    rules.append((not s.tentative, f"- **{act} {title.lower()}: {s.label}.** Stefan {verb} {direction} often here "
                                  f"({pct(s.rate)} of {s.n} vs {pct(s.base)} overall). _{conf}_" if what == "approved" else
                                  f"- **{act} {title.lower()}: {s.label}.** Landlords reply {direction} often here "
                                  f"({pct(s.rate)} of {s.n} vs {pct(s.base)} overall). _{conf}_"))
    reasons = Counter(i["reason"] for i in items if i["rejected"] and i["reason"])
    for reason, n in reasons.most_common(3):
        if n >= mn:
            rules.append((n >= ms, f"- **Filter earlier for \"{reason}\".** It is the reason for {n} of "
                                   f"{sum(reasons.values())} rejections. _{'supported' if n >= ms else 'tentative'}_"))
    e = [i for i in sent if i["outcome_known"] and i.get("edited") is not None]
    if len(e) >= mn:
        _b, st = breakdown(e, lambda r: "edited by Stefan" if r["edited"] else "template as is", lambda r: r["replied"], mn, ms)
        for s in st:
            if s.clear:
                rules.append((True, f"- **Messages {s.label} get {'more' if s.rate > s.base else 'fewer'} replies** "
                                    f"({pct(s.rate)} of {s.n}). _supported_"))
    rules.sort(key=lambda x: not x[0])
    return [r for _ok, r in rules]


STANDING = """## Standing rules (always apply, whatever the numbers say)

- Never send a message to a landlord automatically. Prepare it; a person reads it and sends it.
- Keep the broker disclaimer in every first message (plan item 1.6).
- Don't contact ads flagged as brokers, our own listings, or addresses already on Centris.
- One message per poster: a property manager with many ads gets one message.
- Never read boards in ways their robots.txt or terms don't allow, and never get around a bot check or login wall.
"""


def write(conn, settings=None, out_dir=None, now=None):
    cfg = {"min_sample": 30, "min_show": 5}
    cfg.update((settings or {}).get("learn", {}))
    out = Path(out_dir or OUT_DIR)
    (out / "platforms").mkdir(parents=True, exist_ok=True)
    (out / "signals").mkdir(parents=True, exist_ok=True)
    at = (now or datetime.now()).isoformat(timespec="minutes")
    items = rows(conn)
    edited = edited_by_lead(conn)
    for i in items:
        i["edited"] = edited.get(i["id"])
    sent = [i for i in items if i["sent"] and i["outcome_known"]]
    findings = {"written_at": at, "decisions": len(items), "sent_with_outcome": len(sent), "min_sample": cfg["min_sample"],
                "signals": {}, "platforms": {}, "hooks": {}, "templates": {}}
    written = []

    # Signals
    for fname, title, key in SIGNALS:
        text, data = section(items, key, title, cfg)
        findings["signals"][fname] = data
        (out / "signals" / f"{fname}.md").write_text(HEADER.format(at=at) + f"# {title}\n\n{text}", encoding="utf-8")
        written.append(f"signals/{fname}.md")

    # Platforms
    by_src = defaultdict(list)
    for i in items:
        by_src[i["source"]].append(i)
    plat_lines = []
    for src, its in sorted(by_src.items(), key=lambda kv: -len(kv[1])):
        name = SOURCE_NAMES.get(src, src)
        s_sent = [i for i in its if i["sent"] and i["outcome_known"]]
        appr = sum(i["approved"] for i in its)
        rep = sum(i["replied"] for i in s_sent)
        tent = " _(tentative: small sample)_" if len(its) < cfg["min_sample"] else ""
        reasons = Counter(i["reason"] for i in its if i["rejected"] and i["reason"])
        body = [f"# {name}\n", f"{len(its)} decisions: {appr} approved ({pct(appr / len(its))}), "
                f"{sum(i['rejected'] for i in its)} rejected, {sum(i['later'] for i in its)} for later.{tent}\n",
                f"Sent with a recorded outcome: {len(s_sent)}; replied {rep}"
                + (f" ({pct(rep / len(s_sent))})" if s_sent else "") + f"; presentations {sum(i['presentation'] for i in s_sent)}; "
                f"signed {sum(i['signed'] for i in s_sent)}.\n"]
        if reasons:
            body.append("## Why ads were rejected\n\n" + "\n".join(f"- {r}: {n}" for r, n in reasons.most_common()) + "\n")
        for _f, title, key in SIGNALS:
            if _f == "board":
                continue
            text, _d = section(its, key, title, cfg)
            body.append(f"## {title}\n\n{text}")
        hooks_text, _h = hooks_section(its, cfg)
        body.append("## Title words\n\n" + hooks_text)
        (out / "platforms" / f"{src}.md").write_text(HEADER.format(at=at) + "\n".join(body), encoding="utf-8")
        findings["platforms"][src] = {"decisions": len(its), "approved": appr, "sent_with_outcome": len(s_sent), "replied": rep,
                                      "reject_reasons": dict(reasons)}
        plat_lines.append(f"| [{name}](platforms/{src}.md) | {len(its)} | {pct(appr / len(its))} | "
                          f"{(pct(rep / len(s_sent)) + f' of {len(s_sent)}') if s_sent else '—'} |{' tentative' if tent else ''}")
        written.append(f"platforms/{src}.md")

    # Hooks (title words) and templates
    hooks_text, hooks = hooks_section(items, cfg)
    findings["hooks"] = hooks
    (out / "hooks.md").write_text(HEADER.format(at=at) + "# Title words and hooks\n\nWords in the ad's title, and how often ads "
                                  "with them were approved by Stefan and answered by the landlord. A word is listed once it "
                                  f"appears in {cfg['min_show']} decided ads.\n\n" + hooks_text, encoding="utf-8")
    tmpl_text, tmpl = templates_section(items, cfg)
    findings["templates"] = tmpl
    (out / "messages.md").write_text(HEADER.format(at=at) + "# Messages\n\nWhich template (and whether Stefan edited it) was "
                                     "sent, and how often the landlord replied. The id after @ changes whenever the template "
                                     "text changes, so each wording is its own row.\n\n" + tmpl_text, encoding="utf-8")
    written += ["hooks.md", "messages.md"]

    rules = guidance(items, sent, cfg, edited)
    summary = [HEADER.format(at=at), "# What the desk has learned so far\n",
               f"From {len(items)} decisions and {len(sent)} sent messages with a recorded outcome. Findings from fewer than "
               f"{cfg['min_sample']} decisions are marked _tentative_: read them as hunches to test, not rules.\n",
               "## Guidance\n", "\n".join(rules) if rules else "_Nothing stands out yet. Keep deciding and recording outcomes._", "",
               STANDING, "## Boards\n", "| Board | Decisions | Approved | Replied |", "|---|---|---|---|", *plat_lines, "",
               "## More detail\n", *[f"- [{w}]({w})" for w in written], "- [findings.json](findings.json) (the same numbers, for scripts and agents)"]
    (out / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    (out / "findings.json").write_text(json.dumps(findings, indent=1, ensure_ascii=False), encoding="utf-8")
    return out / "summary.md"


def hooks_section(items, cfg):
    mn, ms = cfg["min_show"], cfg["min_sample"]
    base, st = breakdown(items, lambda r: r["words"], lambda r: r["approved"], mn, ms)
    st = sorted(st, key=lambda s: -(s.rate - s.base) * math.sqrt(s.n))
    top = st[:12] + [s for s in st[-8:] if s not in st[:12]]
    sent = [i for i in items if i["sent"] and i["outcome_known"]]
    rbase, rst = breakdown(sent, lambda r: r["words"], lambda r: r["replied"], mn, ms)
    rst = sorted(rst, key=lambda s: -(s.rate - s.base) * math.sqrt(s.n))[:12]
    text = (f"### Approved by Stefan (overall {pct(base)})\n\n" + table(top, "Approved") +
            f"\n### Landlord replied (overall {pct(rbase)} of {len(sent)})\n\n" + table(rst, "Replied"))
    return text, {"approval": [s.as_dict() for s in top], "reply": [s.as_dict() for s in rst]}


def templates_section(items, cfg):
    mn, ms = cfg["min_show"], cfg["min_sample"]
    sent = [i for i in items if i["sent"] and i["outcome_known"]]
    key = lambda r: (r["template"] or "unknown template") + (" (edited)" if r.get("edited") else "")
    base, st = breakdown(sent, key, lambda r: r["replied"], 1, ms)
    _b2, st2 = breakdown(sent, key, lambda r: r["presentation"], 1, ms)
    text = (f"### Reply rate by template (overall {pct(base)} of {len(sent)})\n\n" + table(st, "Replied") +
            "\n### Presentation booked by template\n\n" + table(st2, "Presentation"))
    return text, {"reply": [s.as_dict() for s in st], "presentation": [s.as_dict() for s in st2]}


def maybe_write(conn, settings=None, max_age_hours=24):
    """Rewrite the learnings when they are missing or older than a day (called after background reads)."""
    f = OUT_DIR / "summary.md"
    if f.exists() and datetime.fromtimestamp(f.stat().st_mtime) > datetime.now() - timedelta(hours=max_age_hours):
        return None
    return write(conn, settings)
