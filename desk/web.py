"""Local web app: Stefan's morning review screen, the Tanya form, the lease sheet and the scorecard.

Runs on http://127.0.0.1:8765 (this computer only). Standard library only.
"""
import csv
import html
import io
import json
import mimetypes
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, feed, outreach, rentals
from .config import UI_DIR, load_settings

BROKER_LABEL = "Rejected as broker by Stefan"
REJECT_REASONS = ["Broker", "Already listed", "Out of area", "Too small / low rent", "Bad property", "Tenant ad", "Other"]


def esc(s):
    return html.escape(str(s if s is not None else ""))


def page(title, body, active=""):
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in [("leads", "/", "Leads"), ("rental", "/rentals/new", "New rental"),
                                 ("rentals", "/rentals", "Rentals"), ("stats", "/stats", "Scorecard")])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Zaarour Rentals</title>
<link rel="stylesheet" href="/ui/style.css"></head><body>
<header><a class="brand" href="/"><span class="logo">Z</span><span>Rentals desk</span></a><nav>{nav}</nav></header>
<main class="{active}">{body}</main></body></html>"""


# ---------------------------------------------------------------- pages
def leads_page():
    return page("Leads", open(UI_DIR / "leads.html", encoding="utf-8").read(), "leads")


WIDE = {"address", "landlord_address", "tenant_current_address", "appliances", "notes_for_tanya",
        "lowest_rent_12m", "new_building", "building_rules"}


def field_html(key, label, kind, required, value=""):
    req = " required" if required else ""
    wide = " wide" if key in WIDE or kind == "textarea" else ""
    if kind == "textarea":
        inp = f'<textarea name="{key}" id="f-{key}" rows="3">{esc(value)}</textarea>'
    elif kind == "check":
        inp = f'<input type="checkbox" name="{key}" id="f-{key}"{" checked" if value else ""}>'
        return f'<label class="field check{wide}">{inp}<span>{esc(label)}</span></label>'
    elif kind.startswith("select:"):
        opts = "".join(f'<option{" selected" if o == value else ""}>{esc(o)}</option>' for o in [""] + kind[7:].split("|"))
        inp = f'<select name="{key}" id="f-{key}"{req}>{opts}</select>'
    else:
        t = {"money": "number", "number": "number"}.get(kind, kind)
        step = ' step="0.01" min="0" inputmode="decimal"' if kind == "money" else ""
        inp = f'<input type="{t}" name="{key}" id="f-{key}" value="{esc(value)}"{step}{req}>'
        if kind == "money":
            inp = f'<span class="money">{inp}</span>'
    tag = '<em class="req">Required</em>' if required else ""
    return f'<label class="field{wide}"><span class="lbl">{esc(label)}{tag}</span>{inp}</label>'


def rental_form(values=None, errors=None):
    values = values or {}
    sections = {}
    for key, label, kind, req, section in rentals.FIELDS:
        sections.setdefault(section, []).append(field_html(key, label, kind, req, values.get(key, "")))
    err = "".join(f"<li>{esc(e)}</li>" for e in (errors or []))
    months = int(load_settings().get("followup_months_before_end", 6))
    sid = lambda s: "s-" + s.lower()
    toc = "".join(f'<a href="#{sid(s)}" data-sec="{sid(s)}"><i></i>{esc(s)}</a>' for s in sections)
    body = f"""<div class="formpage">
<aside class="toc noprint"><div class="toc-in"><p class="eyebrow">Sections</p>{toc}</div></aside>
<div class="formmain">
<h1>New rental</h1>
<p class="lede">Fill this in when a lease is signed. Saving writes the email to Tanya, books your renewal call
{months} months before the lease ends, and prepares the lease sheet. Nothing is sent until you send the email.</p>
{f'<div class="banner bad"><strong>Almost there.</strong><ul>{err}</ul></div>' if err else ''}
<form method="post" action="/rentals/new" class="form" id="rentalform" data-months="{months}">
{''.join(f'<section class="card fsec" id="{sid(s)}"><h2>{esc(s)}</h2><div class="grid">{"".join(f)}</div></section>' for s, f in sections.items())}
<div class="savebar"><span id="left" class="muted"></span><span id="renewal" class="muted"></span>
<button class="primary big" type="submit">Save and prepare email</button></div>
</form></div></div>
<script src="/ui/form.js"></script>"""
    return page("New rental", body, "rental")


def rental_done(r, settings):
    mail = rentals.tanya_email(r, settings)
    mailto = "mailto:" + urllib.parse.quote(mail["to"]) + "?" + urllib.parse.urlencode(
        {"subject": mail["subject"], "body": mail["body"]}, quote_via=urllib.parse.quote)
    body = f"""<h1>Saved: {esc(r['address'])}</h1>
<ol class="steps">
<li><strong>Email Tanya.</strong> <a class="btn primary" href="{esc(mailto)}">Open in my mail app</a>
 <button class="btn" data-copy="#mailbody">Copy text</button>
 <div class="muted">To: {esc(mail['to'])} · Subject: {esc(mail['subject'])}</div>
 <pre id="mailbody">{esc(mail['body'])}</pre></li>
<li><strong>Add the renewal call to your calendar</strong> ({esc(r['followup_date'])}).
 <a class="btn primary" href="/rentals/{r['id']}/followup.ics">Download calendar event</a></li>
<li><strong>Lease.</strong> <a class="btn" href="/rentals/{r['id']}/lease" target="_blank">Open lease prep sheet</a></li>
</ol>
<p><a href="/rentals/new">Enter another rental</a></p>
<script src="/ui/copy.js"></script>"""
    return page("Rental saved", body, "rental")


def rentals_list(conn):
    rows = rentals.export_rows(conn)
    trs = "".join(
        f"<tr><td>{esc(r['address'])}, {esc(r['city'])}</td><td>{esc(r['landlord_name'])}</td><td>{esc(r['lease_end'])}</td>"
        f"<td>{esc(r['followup_date'])}</td><td><a href='/rentals/{r['id']}'>Open</a> · "
        f"<a href='/rentals/{r['id']}/lease' target='_blank'>Lease sheet</a></td></tr>" for r in rows)
    body = f"""<h1>Rentals entered</h1>
<p class="muted">{len(rows)} so far. Each one has a lease end date, which is the data engine 3 (renewals) needs.
<a href="/rentals.csv">Download CSV</a></p>
<table><tr><th>Property</th><th>Landlord</th><th>Lease ends</th><th>Renewal call</th><th></th></tr>{trs}</table>"""
    return page("Rentals", body, "rentals")


def lease_page(r):
    secs = "".join(
        f"<section><h2>{esc(t)}</h2><table>" +
        "".join(f"<tr><th>{esc(l)}</th><td>{esc(v) or '<span class=blank>________________</span>'}</td></tr>" for l, v in rows) +
        "</table></section>" for t, rows in rentals.lease_sheet(r))
    body = f"""<div class="print"><h1>Lease prep sheet</h1>
<p class="muted">{esc(r['address'])}, {esc(r['city'])} · Prepared {esc(r['created_at'][:10])}.
Copy each value into the matching section of the official TAL lease form (the form is mandatory in Québec;
check the section letters against the edition you use). Blank lines still need an answer.</p>
{secs}<button class="btn noprint" onclick="print()">Print</button></div>"""
    return page("Lease sheet", body, "rentals")


def stats_page(conn):
    weeks = []
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    for i in range(4):
        start = monday - timedelta(weeks=i)
        end = start + timedelta(days=7)
        s, e = start.isoformat(), end.isoformat()
        q = lambda where, *a: conn.execute(f"SELECT source, COUNT(*) n FROM leads WHERE {where} GROUP BY source", a).fetchall()
        rows = {}
        for label, where in [("Surfaced", "bucket!='hidden' AND first_seen>=? AND first_seen<?"),
                             ("Sent", "sent_at>=? AND sent_at<?"),
                             ("Rejected", "status='rejected' AND decided_at>=? AND decided_at<?"),
                             ("Replied", "outcome IN ('replied','presentation','listing') AND outcome_at>=? AND outcome_at<?"),
                             ("Listing presentation", "outcome IN ('presentation','listing') AND outcome_at>=? AND outcome_at<?"),
                             ("Signed listing", "outcome='listing' AND outcome_at>=? AND outcome_at<?")]:
            for r in q(where, s, e):
                rows.setdefault(r["source"], {})[label] = r["n"]
        weeks.append((start, rows))
    cols = ["Surfaced", "Sent", "Rejected", "Replied", "Listing presentation", "Signed listing"]
    html_rows = ""
    for start, rows in weeks:
        for src, vals in sorted(rows.items()) or [("—", {})]:
            html_rows += f"<tr><td>Week of {start}</td><td>{esc(src)}</td>" + "".join(f"<td>{vals.get(c, 0)}</td>" for c in cols) + "</tr>"
    body = f"""<h1>Scorecard</h1><p class="muted">Ads surfaced, messages sent, replies and listings, by source and week
(plan section 7). Replies and listings come from the buttons on each sent lead.</p>
<table><tr><th>Week</th><th>Source</th>{''.join(f'<th>{c}</th>' for c in cols)}</tr>{html_rows}</table>"""
    return page("Scorecard", body, "stats")


# ---------------------------------------------------------------- API
def api_leads(conn, settings, tab, phase=""):
    wheres = {
        "approved": ("status='approved'", ()),
        "sent": ("status='sent'", ()),
        "later": ("status='later'", ()),
        "rejected": ("status='rejected'", ()),
    }
    bucket = tab if tab in ("broker", "hidden") else "review"
    where, args = wheres.get(tab, ("status='new' AND bucket=?", (bucket,)))
    # Phase 1 areas first, then owners before property managers, newest first within each.
    if phase in ("1", "2"):
        where, args = f"({where}) AND phase=?", tuple(args) + (int(phase),)
    order = "sent_at DESC" if tab == "sent" else "decided_at DESC" if tab in ("approved", "rejected") else "COALESCE(phase, 9), (poster_type='owner') DESC, first_seen DESC, posted_at DESC"
    rows = conn.execute(f"SELECT * FROM leads WHERE {where} ORDER BY {order} LIMIT 3000", args).fetchall()
    out, by_poster = [], {}
    for r in rows:
        d = db.lead_dict(r)
        # One message per poster: a manager with 8 ads gets one card, not 8 messages.
        key = poster_key(d) if tab in ("review", "broker") else None
        if key and key in by_poster:
            by_poster[key]["others"].append({k: d[k] for k in ("title", "url", "address", "price")})
            continue
        d["others"] = []
        if key:
            by_poster[key] = d
        d["message_fr"] = outreach.render(d, settings, "fr")
        d["message_en"] = outreach.render(d, settings, "en")
        d["followup_fr"] = outreach.render(d, settings, "fr", "followup")
        d["followup_en"] = outreach.render(d, settings, "en", "followup")
        out.append(d)
    return {"leads": out, "counts": lead_counts(conn), "today": today_counts(conn),
            "limit": int(settings.get("message_char_limit", 1000)), "reasons": REJECT_REASONS}


def lead_counts(conn):
    """Cards per tab. Waiting ads count once per poster, exactly like the cards (see poster_key)."""
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM leads WHERE status!='new' GROUP BY status")}
    seen = set()
    for r in conn.execute("SELECT id, bucket, source, poster_id, title FROM leads WHERE status='new'"):
        b = r["bucket"]
        key = poster_key(dict(r)) if b in ("review", "broker") else None
        if key and (b, key) in seen:
            continue
        seen.add((b, key or r["id"]))
        counts[b] = counts.get(b, 0) + 1
    return counts


def today_counts(conn):
    t = date.today().isoformat()
    q = lambda where: conn.execute(f"SELECT COUNT(*) FROM leads WHERE {where}", (t,)).fetchone()[0]
    return {"approved": q("status IN ('approved','sent') AND decided_at>=?"), "sent": q("sent_at>=?"),
            "rejected": q("status='rejected' AND decided_at>=?"), "later": q("status='later' AND decided_at>=?")}


def poster_key(d):
    """Who posted it. Boards without poster ids (Craigslist) fall back to the exact title:
    a landlord reposting 'Beautiful 3 1/2 Cote des Neiges' 10 times is one landlord."""
    if d.get("poster_id"):
        return (d["source"], "p", d["poster_id"])
    if d.get("source") in ("craigslist", "logisquebec") and d.get("title"):
        return (d["source"], "t", d["title"].strip().lower())
    return None


def _group(conn, r, action, t):
    """Other ads from the same poster follow the decision on the one card shown for them."""
    key = poster_key(dict(r))
    if not key:
        return
    if key[1] == "p":
        same, args = "source=? AND poster_id=? AND id!=?", (r["source"], r["poster_id"], r["id"])
    else:
        same, args = "source=? AND lower(trim(title))=? AND id!=?", (r["source"], key[2], r["id"])
    if action in ("approve", "sent", "reject", "later"):
        conn.execute(f"UPDATE leads SET status='duplicate', decided_at=?, reject_reason=? WHERE {same} AND status IN ('new','approved')",
                     (t, f"Same poster as {r['id']}") + args)
    elif action == "undo":
        conn.execute(f"UPDATE leads SET status='new', decided_at=NULL, reject_reason=NULL WHERE {same} AND status='duplicate' AND reject_reason=?",
                     args + (f"Same poster as {r['id']}",))


def api_decide(conn, lead_id, body):
    action = body.get("action")
    r = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not r:
        return {"error": "not found"}, 404
    t = db.now()
    _group(conn, r, action, t)
    if action == "approve":
        conn.execute("UPDATE leads SET status='approved', decided_at=?, message=? WHERE id=?", (t, body.get("message", ""), lead_id))
    elif action == "sent":
        conn.execute("UPDATE leads SET status='sent', sent_at=?, message=COALESCE(?, message) WHERE id=?", (t, body.get("message"), lead_id))
    elif action == "reject":
        reason = body.get("reason", "Other")
        conn.execute("UPDATE leads SET status='rejected', decided_at=?, reject_reason=? WHERE id=?", (t, reason, lead_id))
        if reason == "Broker":  # learn: the next ad from this poster is flagged automatically
            db.mark_poster(conn, r["source"], r["poster_id"], BROKER_LABEL)
    elif action == "later":
        conn.execute("UPDATE leads SET status='later', decided_at=? WHERE id=?", (t, lead_id))
    elif action == "undo":
        if r["status"] == "rejected" and r["reject_reason"] == "Broker":  # un-learn a mis-click
            db.unmark_poster(conn, r["source"], r["poster_id"], BROKER_LABEL)
        conn.execute("UPDATE leads SET status='new', decided_at=NULL, reject_reason=NULL, sent_at=NULL WHERE id=?", (lead_id,))
    elif action == "unsend":  # undo of Mark sent: back to the To send queue
        conn.execute("UPDATE leads SET status='approved', sent_at=NULL WHERE id=?", (lead_id,))
    elif action in ("replied", "presentation", "listing", "no_reply"):
        conn.execute("UPDATE leads SET outcome=?, outcome_at=? WHERE id=?", (action, t, lead_id))
    elif action == "set_outcome":  # undo of an outcome button: put the previous one back (or none)
        prev = body.get("outcome") or None
        conn.execute("UPDATE leads SET outcome=?, outcome_at=? WHERE id=?", (prev, t if prev else None, lead_id))
    elif action == "rebucket":  # "Move to review" from Hidden, and its undo
        b = body.get("bucket")
        if b not in ("review", "broker", "hidden"):
            return {"error": "bad bucket"}, 400
        conn.execute("UPDATE leads SET bucket=? WHERE id=?", (b, lead_id))
    elif action == "note":
        conn.execute("UPDATE leads SET notes=? WHERE id=?", (body.get("notes", ""), lead_id))
    else:
        return {"error": "unknown action"}, 400
    conn.commit()
    return {"ok": True}, 200


# ---------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    server_version = "ZaarourDesk/1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _form(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8")
        if "json" in (self.headers.get("Content-Type") or ""):
            return json.loads(raw or "{}")
        return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        conn = db.connect()
        settings = load_settings()
        try:
            if path == "/":
                return self._send(200, leads_page())
            if path.startswith("/ui/"):
                f = UI_DIR / path[4:]
                if f.resolve().parent != UI_DIR.resolve() or not f.exists():
                    return self._send(404, "not found", "text/plain")
                return self._send(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "text/plain")
            if path == "/api/counts":
                return self._json({"counts": lead_counts(conn), "today": today_counts(conn)})
            if path == "/api/leads":
                return self._json(api_leads(conn, settings, (qs.get("tab") or ["review"])[0], (qs.get("phase") or [""])[0]))
            if path == "/rentals/new":
                return self._send(200, rental_form())
            if path == "/rentals":
                return self._send(200, rentals_list(conn))
            if path == "/rentals.csv":
                rows = rentals.export_rows(conn)
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=["id", "created_at", "followup_date"] + rentals.FIELD_KEYS, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
                return self._send(200, buf.getvalue(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=rentals.csv"})
            if path == "/stats":
                return self._send(200, stats_page(conn))
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "rentals" and parts[1].isdigit():
                r = rentals.get(conn, int(parts[1]))
                if not r:
                    return self._send(404, page("Not found", "<h1>Not found</h1>"))
                if len(parts) == 2:
                    return self._send(200, rental_done(r, settings))
                if parts[2] == "followup.ics":
                    return self._send(200, rentals.followup_ics(r), "text/calendar; charset=utf-8",
                                      {"Content-Disposition": f"attachment; filename=renewal-call-{r['id']}.ics"})
                if parts[2] == "lease":
                    return self._send(200, lease_page(r))
            return self._send(404, page("Not found", "<h1>Not found</h1>"))
        finally:
            conn.close()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        conn = db.connect()
        try:
            body = self._form()
            if path.startswith("/api/lead/") and path.endswith("/decide"):
                lead_id = urllib.parse.unquote(path[len("/api/lead/"):-len("/decide")])
                res, code = api_decide(conn, lead_id, body)
                return self._json(res, code)
            if path == "/api/decide-many":
                done = [api_decide(conn, i, body)[1] == 200 for i in body.get("ids", [])]
                return self._json({"ok": sum(done)})
            if path == "/api/manual":
                lid = feed.add_manual(conn, body)
                r = conn.execute("SELECT bucket FROM leads WHERE id=?", (lid,)).fetchone()
                return self._json({"id": lid, "bucket": r["bucket"] if r else ""})
            if path == "/api/recheck":
                return self._json(dict(feed.recheck_all(conn)))
            if path == "/rentals/new":
                errors = rentals.validate(body)
                if errors:
                    return self._send(400, rental_form(body, errors))
                rid = rentals.save(conn, body)
                return self._send(303, "", headers={"Location": f"/rentals/{rid}"})
            return self._json({"error": "not found"}, 404)
        finally:
            conn.close()


def serve(port=8765, open_browser=True):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Rentals desk running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
