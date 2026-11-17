"""Which way of opening a first message gets landlords to answer, and how new messages lean on it.

Every custom message (drafts.py) is written with one "approach" from config/approaches.json: a named way of
opening. The approach is kept with the draft, with the lead once Stefan approves its message, and with every
logged event. The standard template is its own approach ("template"): the baseline the others are read against.

What counts as success, in the order it happens after a message is sent:
    no reply, or said no  <  replied (interested)  <  presentation booked  <  listing signed
A reply is the early sign and a signed listing is the goal. Signed listings are far too rare to steer by (the
desk test hopes for 6 in 300 messages), so an interested reply carries most of the credit and the later steps
add to it (learn.outcome_worth). A sent message with nothing recorded after learn.no_reply_days counts as no
reply, so the rates don't depend on Stefan clicking "No reply" on every quiet ad.

How drafting leans (shares): each approach's chance of being the best is worked out from its own results,
pulled toward the average of all messages while it has few, so two lucky replies don't crown a winner. That
is blended with an even split: mostly even while any approach has fewer than learn.min_sample outcomes
(learn.lean_light), mostly by results after that (learn.lean_full). Every active approach always keeps a
share, so the testing never stops. pick() gives each new ad the approach furthest below its share lately.

Nothing here sends anything: it decides how a draft is worded, and Stefan reads every one before it goes.
"""
import hashlib
import json
import math
import random
import re
from datetime import datetime, timedelta

from . import outreach
from .config import CONFIG_DIR

TEMPLATE = "template"   # the standard message from templates/, the same for every ad
UNSET = "custom"        # a custom message written with no approach (none active, or before approaches existed)
POSITIVE = ("replied", "presentation", "listing")
DEFAULTS = {"min_sample": 30, "min_show": 5, "no_reply_days": 14, "lean_light": 0.25, "lean_full": 0.75,
            "prior_strength": 10, "prior_rate": 0.08, "window": 40,
            "outcome_worth": {"replied": 0.5, "presentation": 0.8, "listing": 1.0},
            "desk_test": {"messages": 300, "success_signed": 6, "stop_signed": 2}}
BUILT_IN = {
    TEMPLATE: {"id": TEMPLATE, "name": "Standard message", "what": "The desk's standard wording, the same for every ad.",
               "instruction": "", "active": False},
    UNSET: {"id": UNSET, "name": "Custom, no set approach", "what": "Written for the ad without a set way of opening.",
            "instruction": "", "active": False},
}
DRAWS = 3000


def cfg(settings):
    """learn.* from settings over the defaults (nested ones merged key by key)."""
    out = dict(DEFAULTS)
    for k, v in ((settings or {}).get("learn") or {}).items():
        out[k] = {**DEFAULTS[k], **v} if isinstance(DEFAULTS.get(k), dict) and isinstance(v, dict) else v
    return out


def load(path=None):
    """The approaches in config/approaches.json, in file order: [{"id", "name", "what", "instruction", "active"}]."""
    try:
        raw = json.loads((path or CONFIG_DIR / "approaches.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out, seen = [], set(BUILT_IN)
    for a in raw.get("approaches") or []:
        aid = str(a.get("id") or "")
        if not re.fullmatch(r"[a-z0-9_]{1,40}", aid) or aid in seen or not (a.get("instruction") or "").strip():
            continue
        seen.add(aid)
        out.append({"id": aid, "name": a.get("name") or aid, "what": a.get("what") or "",
                    "instruction": a["instruction"].strip(), "active": bool(a.get("active", True))})
    return out


def get(approach_id, defs=None):
    """One approach by id (None when it isn't in the file any more)."""
    return next((a for a in (load() if defs is None else defs) if a["id"] == approach_id), None)


# ---------------------------------------------------------------- what happened to each sent message
def _dt(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def filed_under(approach, template):
    """The approach a message counts for: its own, else the standard message (or 'custom' for an old draft)."""
    return approach or (UNSET if (template or "").startswith("draft:") else TEMPLATE)


def state(outcome, sent_at, no_reply_days=14, now=None):
    """Where a sent message stands: its recorded outcome, 'quiet' once it has gone no_reply_days without one
    (counted as no reply), or 'waiting' while it is too soon to say."""
    if outcome:
        return outcome
    sent = _dt(sent_at)
    return "quiet" if sent and sent <= (now or datetime.now()) - timedelta(days=no_reply_days) else "waiting"


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _tally(conn, c, now):
    """{approach id: counts} over sent messages. n is the messages whose outcome is known; replied counts every
    interested reply, including those that went on to a presentation or a signed listing."""
    out = {}
    for r in conn.execute("SELECT approach, template, outcome, sent_at FROM leads WHERE status='sent'"):
        t = out.setdefault(filed_under(r["approach"], r["template"]), dict.fromkeys(
            ("sent", "waiting", "n", "replied", "presentation", "listing", "declined", "no_reply"), 0) | {"points": 0.0})
        s = state(r["outcome"], r["sent_at"], c["no_reply_days"], now)
        t["sent"] += 1
        if s == "waiting":
            t["waiting"] += 1
            continue
        t["n"] += 1
        t["replied"] += s in POSITIVE
        t["presentation"] += s in ("presentation", "listing")
        t["listing"] += s == "listing"
        t["declined"] += s == "declined"
        t["no_reply"] += s in ("no_reply", "quiet")
        t["points"] += float(c["outcome_worth"].get(s, 0))
    return out


# ---------------------------------------------------------------- how much to lean on the results
def shares(arms, c, prior_mean):
    """{id: share of new messages} for the approaches in rotation. See the module note for the reasoning."""
    k = len(arms)
    if not k:
        return {}
    lean = c["lean_light"] if min(a["n"] for a in arms) < c["min_sample"] else c["lean_full"]
    if len({(a["n"], round(a["points"], 6)) for a in arms}) == 1:  # nothing tells them apart: an even split
        best = {a["id"]: 1 / k for a in arms}
    else:
        mean = min(0.99, max(0.01, prior_mean))
        a0, b0 = c["prior_strength"] * mean, c["prior_strength"] * (1 - mean)
        rng, wins = random.Random(0), dict.fromkeys((a["id"] for a in arms), 0)  # same numbers, same shares
        for _ in range(DRAWS):
            wins[max(arms, key=lambda a: rng.betavariate(a0 + a["points"], b0 + a["n"] - a["points"]))["id"]] += 1
        best = {i: w / DRAWS for i, w in wins.items()}
    return {a["id"]: (1 - lean) / k + lean * best[a["id"]] for a in arms}


def stats(conn, settings=None, defs=None, now=None, with_shares=True):
    """How each approach is doing, best first, and how hard drafting leans on that.

    {"rows": [...], "phase": "learning" | "leaning" | "none", "least": fewest outcomes among the approaches in
    rotation, "min_sample", "no_reply_days", "base": interested-reply rate over all messages, "headline"}"""
    c = cfg(settings)
    defs = load() if defs is None else defs
    tally = _tally(conn, c, now)
    known = {a["id"]: a for a in defs}
    ids = [a["id"] for a in defs] + [i for i in tally if i not in known]
    if TEMPLATE not in ids:
        ids.append(TEMPLATE)
    total_n = sum(t["n"] for t in tally.values())
    total_points = sum(t["points"] for t in tally.values())
    total_replied = sum(t["replied"] for t in tally.values())
    # What an approach with no results of its own is assumed to earn per message: the average so far, itself
    # held near learn.prior_rate until there is something to average.
    prior_mean = (total_points + c["prior_rate"] * c["prior_strength"]) / (total_n + c["prior_strength"])
    rows = []
    for i in ids:
        a = known.get(i) or BUILT_IN.get(i) or {"id": i, "name": i, "what": "No longer in config/approaches.json.",
                                                "instruction": "", "active": False}
        t = tally.get(i) or dict.fromkeys(("sent", "waiting", "n", "replied", "presentation", "listing", "declined", "no_reply"), 0) | {"points": 0.0}
        lo, hi = wilson(t["replied"], t["n"])
        others_n, others_k = total_n - t["n"], total_replied - t["replied"]
        others = others_k / others_n if others_n else None
        if not t["sent"]:
            reading = "unused"
        elif t["n"] < c["min_show"]:
            reading = "started"
        elif t["n"] < c["min_sample"]:
            reading = "early"
        elif others is None:
            reading = "alone"
        else:  # ahead or behind only when its range and the others' range don't even touch
            o_lo, o_hi = wilson(others_k, others_n)
            reading = "ahead" if lo > o_hi else "behind" if hi < o_lo else "same"
        rows.append({**{k: a[k] for k in ("id", "name", "what", "active")}, **t, "rate": t["replied"] / t["n"] if t["n"] else None,
                     "range": (lo, hi), "others": others, "reading": reading, "share": None,
                     "score": (t["points"] + c["prior_strength"] * prior_mean) / (t["n"] + c["prior_strength"])})
    arms = [r for r in rows if r["active"]]
    if with_shares:
        for i, s in shares(arms, c, prior_mean).items():
            next(r for r in arms if r["id"] == i)["share"] = s
    least = min((r["n"] for r in arms), default=0)
    phase = "none" if not arms else "learning" if least < c["min_sample"] else "leaning"
    order = {i: n for n, i in enumerate(ids)}
    rows.sort(key=lambda r: (not r["n"], -r["score"] if r["n"] else 0, order[r["id"]]))
    out = {"rows": rows, "phase": phase, "least": least, "min_sample": c["min_sample"], "no_reply_days": c["no_reply_days"],
           "base": total_replied / total_n if total_n else None, "sent": sum(t["sent"] for t in tally.values()), "known": total_n}
    out["headline"] = headline(out)
    return out


def pct(x):
    return f"{round(100 * x)}%"


def _of(r):
    return f"{r['replied']} {'reply' if r['replied'] == 1 else 'replies'} from {r['n']} message{'' if r['n'] == 1 else 's'}"


def headline(st):
    """One plain sentence on which approach is winning, saying so when it is too early to know."""
    if not st["known"]:
        return "No answers to read yet. This fills in as you mark what happened to each sent message."
    solid = [r for r in st["rows"] if r["reading"] in ("ahead", "behind", "same", "alone")]
    ahead = [r for r in solid if r["reading"] == "ahead"]
    if ahead:
        r = ahead[0]
        return f"{r['name']} is winning: {_of(r)} ({pct(r['rate'])}), against {pct(r['others'])} for the rest."
    early = [r for r in st["rows"] if r["reading"] == "early" and r["replied"]]
    behind = [r for r in solid if r["reading"] == "behind"]
    if behind:
        r = behind[-1]
        return f"No approach is clearly ahead. {r['name']} is behind: {pct(r['rate'])} replied, against {pct(r['others'])} for the rest."
    if len(solid) >= 2:
        return "No approach is clearly ahead: the differences so far are within chance."
    if early:
        r = early[0]
        return (f"Ahead so far: {r['name']}, with {_of(r)}. Too early to rely on: it takes about "
                f"{st['min_sample']} messages with a known result per approach.")
    return f"Too early to tell. It takes about {st['min_sample']} messages with a known result per approach."


def brief(conn, settings=None, defs=None):
    """What the review screen needs: each approach's name, and whether the desk is still testing evenly."""
    defs = load() if defs is None else defs
    st = stats(conn, settings, defs, with_shares=False)
    names = {i: a["name"] for i, a in BUILT_IN.items()} | {a["id"]: a["name"] for a in defs}
    return {"phase": st["phase"], "least": st["least"], "min_sample": st["min_sample"], "no_reply_days": st["no_reply_days"],
            "names": names, "active": sum(1 for a in defs if a["active"])}


# ---------------------------------------------------------------- choosing the approach for a new message
def pick(conn, lead_id, settings=None, exclude=(), defs=None, now=None):
    """The approach for this ad's message: the active one furthest below its share of the last learn.window ads
    that got a custom message (so the split holds even over a few dozen ads). None when no approach is active."""
    c = cfg(settings)
    arms = [r for r in stats(conn, settings, defs, now)["rows"] if r["share"] is not None and r["id"] not in exclude]
    if not arms:
        return None
    recent = [r["approach"] for r in conn.execute(
        "SELECT approach, MAX(created_at) AS at FROM drafts WHERE lead_id!=? AND approach IS NOT NULL "
        "GROUP BY lead_id ORDER BY at DESC LIMIT ?", (lead_id, int(c["window"])))]
    recent = [a for a in recent if any(r["id"] == a for r in arms)]
    whole = sum(r["share"] for r in arms)

    def behind(r):  # ties go to a different approach from one ad to the next, the same one for the same ad
        gap = r["share"] / whole * (len(recent) + 1) - recent.count(r["id"])
        return (round(gap, 6), hashlib.sha1(f"{lead_id}:{r['id']}".encode("utf-8")).hexdigest())
    return max(arms, key=behind)["id"]


def answered(conn, approach_id, lang, settings=None):
    """Messages sent with this approach that a landlord answered, as Stefan sent them: same language first, then
    the ones that went furthest, newest first. [] until there are drafts.proven_min of them, so one lucky
    reply doesn't become the model for every message after it."""
    d = (settings or {}).get("drafts") or {}
    least, most = int(d.get("proven_min", 3)), int(d.get("proven_max", 3))
    if not approach_id or most <= 0:
        return []
    rows = conn.execute("SELECT message, outcome, outcome_at FROM leads WHERE status='sent' AND approach=? "
                        "AND outcome IN ('replied','presentation','listing') AND COALESCE(message,'')!='' "
                        "ORDER BY outcome_at DESC", (approach_id,)).fetchall()
    if len(rows) < max(1, least):
        return []
    worth = cfg(settings)["outcome_worth"]
    rows.sort(key=lambda r: (outreach.detect_lang(r["message"]) != lang, -float(worth.get(r["outcome"], 0))))
    return [r["message"] for r in rows[:most]]


# ---------------------------------------------------------------- the desk test
def desk_test(conn, settings=None):
    """Where the plan's test stands: the first desk_test.messages sent, and how many of them signed a listing.
    success at success_signed or more; once every message is out, stop at stop_signed or fewer."""
    t = cfg(settings)["desk_test"]
    rows = conn.execute("SELECT outcome FROM leads WHERE status='sent' ORDER BY sent_at, id").fetchall()
    first = rows[:int(t["messages"])]
    count = lambda outs: sum(1 for r in first if r["outcome"] in outs)
    signed = count(("listing",))
    if signed >= t["success_signed"]:
        st = "success"
    elif len(rows) < t["messages"]:
        st = "running"
    else:
        st = "stop" if signed <= t["stop_signed"] else "between"
    return {**t, "sent": len(first), "sent_all": len(rows), "replied": count(POSITIVE), "declined": count(("declined",)),
            "presentation": count(("presentation", "listing")), "signed": signed, "state": st}


def verdict(t):
    """The desk test in one plain sentence."""
    n, k, goal, stop, total = t["sent"], t["signed"], t["success_signed"], t["stop_signed"], t["messages"]
    s = "" if k == 1 else "s"
    if t["state"] == "success":
        return f"Goal reached: {k} signed listings from {n} messages. The plan's bar is {goal} from {total}."
    if t["state"] == "stop":
        return (f"{k} signed listing{s} from the first {total} messages. That is the plan's stop line ({stop} or fewer): "
                f"stop and rethink before sending more. Listings from these {total} still count if they sign later.")
    if t["state"] == "between":
        return (f"{k} signed listings from the first {total} messages: between the stop line ({stop}) and the goal ({goal}). "
                f"A judgement call. Listings from these {total} still count if they sign later.")
    if not n:
        return "No messages sent yet. The test starts with the first message you mark sent."
    return (f"{k} signed listing{s} from {n} message{'' if n == 1 else 's'} so far. A listing takes weeks to sign after a "
            f"first message, so read this at {total} messages, not before.")
