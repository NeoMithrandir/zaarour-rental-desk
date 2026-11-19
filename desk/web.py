"""Local web app: Stefan's morning review screen, the lease renewal tracker, the lease sheet and the scorecard.

Runs on http://127.0.0.1:8765 (this computer only). Standard library only.
"""
import csv
import errno
import html
import io
import json
import mimetypes
import sys
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import approaches, capture, db, drafts, feed, learn, lease_import, lease_scan, leases, outreach, profile, scan, send, tal
from .config import UI_DIR, load_areas, load_settings

BROKER_LABEL = "Rejected as broker by Stefan"
REJECT_REASONS = ["Broker", "Already listed", "Out of area", "Too small / low rent", "Bad property", "Tenant ad", "Other"]


def esc(s):
    return html.escape(str(s if s is not None else ""))


def page(title, body, active=""):
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in [("leads", "/", "Leads"), ("lease", "/leases/new", "New lease"),
                                 ("leases", "/leases", "Renewals"), ("stats", "/stats", "Scorecard"), ("me", "/me", "My details")])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Zaarour Rentals</title>
<link rel="icon" type="image/png" href="/ui/zaarour-favicon.png"><link rel="apple-touch-icon" href="/ui/zaarour-favicon.png">
<link rel="stylesheet" href="/ui/style.css"></head><body>
<header><a class="brand" href="/"><img class="logo" src="/ui/zaarour-logo.png" width="158" height="44" alt="Zaarour &amp; Associates"><span>Rentals desk</span></a><nav>{nav}</nav></header>
<main class="{active}">{body}</main></body></html>"""


# ---------------------------------------------------------------- pages
def phase_tip(n):
    """'Covers Saint-Laurent, Lachine, …': the areas in config/areas.json for that phase."""
    return "Covers " + ", ".join(name for name, a in load_areas().items() if a.get("phase") == n) + "."


def leads_page():
    body = open(UI_DIR / "leads.html", encoding="utf-8").read()
    for n in (1, 2):
        body = body.replace(f"{{phase{n}_areas}}", esc(phase_tip(n)))
    return page("Leads", body, "leads")


WIDE = {"address", "landlord_address", "tenant_current_address", "appliances", "notes_for_office",
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


def lease_form(values=None, errors=None):
    values = values or {}
    sections = {}
    for key, label, kind, req, section in leases.FIELDS:
        sections.setdefault(section, []).append(field_html(key, label, kind, req, values.get(key, "")))
    err = "".join(f"<li>{esc(e)}</li>" for e in (errors or []))
    months = int(load_settings().get("followup_months_before_end", 6))
    sid = lambda s: "s-" + s.lower()
    toc = "".join(f'<a href="#{sid(s)}" data-sec="{sid(s)}"><i></i>{esc(s)}</a>' for s in sections)
    body = f"""<div class="formpage">
<aside class="toc noprint"><div class="toc-in"><p class="eyebrow">Sections</p>{toc}</div></aside>
<div class="formmain">
<h1>New lease</h1>
<p class="lede">Fill this in when a lease is signed. Saving adds it to the renewal tracker with a renewal call
{months} months before the lease ends, writes the email to the office and prepares the lease sheet. Nothing is sent until you send the email.</p>
{f'<div class="banner bad"><strong>Almost there.</strong><ul>{err}</ul></div>' if err else ''}
<section class="card scanbox noprint" id="scanbox">
<h2>Scan the lease</h2>
<p class="muted">Take photos of each page with your phone or drop in scans or a PDF. The form fills itself as the pages
are read; highlighted fields came from the photos, and orange ones need a closer look. Nothing saves until you click Save.
The pages are sent to the Claude API to be read and kept on this computer in <code>data/scans/</code>.</p>
<label class="dropzone" id="dropzone"><input type="file" id="scanfiles" accept="image/*,application/pdf" capture="environment" multiple>
<span><b>Take photos or choose files</b> · or drop them here</span></label>
<ul class="scanpages" id="scanpages"></ul>
<div class="actions-row"><button type="button" class="primary" id="scanbtn" disabled>Read the pages</button>
<span id="scanstatus" class="muted" role="status" aria-live="polite"></span></div>
</section>
<form method="post" action="/leases/new" class="form" id="leaseform" data-months="{months}">
<input type="hidden" name="scan_meta" id="scan_meta" value="">
{''.join(f'<section class="card fsec" id="{sid(s)}"><h2>{esc(s)}</h2><div class="grid">{"".join(f)}</div></section>' for s, f in sections.items())}
<div class="savebar"><span id="left" class="muted"></span><span id="renewal" class="muted"></span>
<button class="primary big" type="submit">Save and prepare email</button></div>
</form></div></div>
<script src="/ui/form.js"></script>
<script src="/ui/scan.js"></script>"""
    return page("New lease", body, "lease")


def lease_done(r, settings):
    mail = leases.office_email(r, settings)
    mailto = "mailto:" + urllib.parse.quote(mail["to"]) + "?" + urllib.parse.urlencode(
        {"subject": mail["subject"], "body": mail["body"]}, quote_via=urllib.parse.quote)
    body = f"""<h1>Saved: {esc(r['address'])}</h1>
<ol class="steps">
<li><strong>Email the office the lease details.</strong> <a class="btn primary" href="{esc(mailto)}">Open in my mail app</a>
 <button class="btn" data-copy="#mailbody">Copy text</button>
 <div class="muted">To: {esc(mail['to'])} · Subject: {esc(mail['subject'])}</div>
 <pre id="mailbody">{esc(mail['body'])}</pre></li>
<li><strong>Add the renewal call to your calendar</strong> ({esc(r['followup_date'])}). It's also on the
 <a href="/leases">renewal tracker</a> with the message for the landlord.
 <a class="btn primary" href="/leases/{r['id']}/followup.ics">Download calendar event</a></li>
<li><strong>Lease.</strong> <a class="btn" href="/leases/{r['id']}/lease" target="_blank">Open lease prep sheet</a></li>
</ol>
<p><a href="/leases/new">Enter another lease</a></p>
<script src="/ui/copy.js"></script>"""
    return page("Lease saved", body, "lease")


STATUS_LABEL = {"due": "Call now", "check": "Check data", "upcoming": "Upcoming", "ended": "Lease ended"}


def tracker_page(conn):
    rows = leases.export_rows(conn)
    settings = load_settings()
    for r in rows:
        r["_status"] = leases.renewal_status(r, settings=settings)
        r["_call"] = leases.next_call(r, settings).isoformat()
        _s, end, renewed = leases.current_term(r)
        r["_end"] = end.isoformat() + (" (renewed)" if renewed else "")
    order = {"due": 0, "check": 1, "upcoming": 2, "ended": 3}
    rows.sort(key=lambda r: (order[r["_status"]], r["_call"]))
    due = sum(1 for r in rows if r["_status"] == "due")
    check = sum(1 for r in rows if r["_status"] == "check")
    trs = "".join(
        f"<tr class='st-{r['_status']}'><td><span class='rstat {r['_status']}'>{STATUS_LABEL[r['_status']]}</span></td>"
        f"<td>{esc(r['_call'])}</td><td>{esc(r['address'])}, {esc(r['city'])}</td><td>{esc(r['landlord_name'])}</td>"
        f"<td>{esc(r['_end'])}</td><td><a class='btn' href='/leases/{r['id']}/renewal'>Landlord message</a> "
        f"<a class='btn' href='/leases/{r['id']}/tal'>TAL calculator</a> "
        f"<a href='/leases/{r['id']}'>Details</a> · <a href='/leases/{r['id']}/lease' target='_blank'>Lease sheet</a></td></tr>"
        for r in rows)
    months = int(load_settings().get("followup_months_before_end", 6))
    body = f"""<h1>Lease renewal tracker</h1>
<p class="lede">{len(rows)} lease{'s' if len(rows) != 1 else ''} tracked, {due} to call now{f", {check} imported with data to check" if check else ""}. Each lease gets a renewal call
{months} months before it ends, when the landlord's notice window opens. <b>Landlord message</b> has the message ready
to send. <a href="/leases.csv">Download CSV</a></p>
<table><tr><th></th><th>Renewal call</th><th>Property</th><th>Landlord</th><th>Lease ends</th><th></th></tr>{trs}</table>
<p class="muted">Leases come from <a href="/leases/new">New lease</a> and from imports (docs/lease-import-spec.md).
"Check data" means an imported lease whose end date was guessed or low-confidence: confirm it before calling.</p>"""
    return page("Lease renewal tracker", body, "leases")


def tal_page(r, saved=False):
    inputs = r.get("tal_inputs") or {}
    calc = tal.for_lease(r)
    fields = "".join(
        f'<label class="field wide"><span class="lbl">{esc(label)}</span>'
        f'<span class="money"><input type="number" step="0.01" min="0" name="{key}" value="{esc(inputs.get(key, ""))}"></span>'
        f'<small class="muted">{esc(help_)}</small></label>'
        for key, label, help_ in [tal.CURRENT_RENT] + tal.INPUTS)
    if calc:
        rows = "".join(f"<tr><td>{esc(l['label'])}</td><td>${l['amount']:,.2f}</td></tr>" for l in calc["lines"])
        prov = (f"<p class='banner ask'>The rates for leases renewing on {calc['renewal_start']} aren't published yet "
                f"(the TAL publishes them in January). This uses the {calc['period']} rates; update "
                f"<code>config/tal_rates.json</code> when the new ones are out.</p>" if calc["provisional"] else "")
        result = f"""{prov}<table><tr><th>Renewal starts</th><td>{calc['renewal_start']}</td></tr>
<tr><th>Current rent</th><td>${calc['rent']:,.2f}</td></tr>{rows}
<tr><th>Increase</th><td><b>${calc['increase']:,.2f} a month ({calc['increase_pct']:.2f} %)</b></td></tr>
<tr><th>New rent</th><td><b>${calc['new_rent']:,}</b> (rounded to the dollar)</td></tr>
<tr><th>This unit's share of the building</th><td>{calc['share_pct']:g} %</td></tr></table>"""
    else:
        result = "<p class='banner ask'>Add the current rent below to calculate.</p>"
    body = f"""<h1>TAL calculator: {esc(r['address'])}</h1>
<p class="lede">Follows the Tribunal administratif du logement's method for notices given since 1 January 2026
(base percentage, municipal and school taxes, insurance, 5 % of major work). With only the rent, it applies the base
percentage; add the building's figures for the full calculation. These numbers go straight into the
<a href="/leases/{r['id']}/renewal">landlord message</a>.</p>
{'<p class="banner">Saved.</p>' if saved else ''}{result}
<p class="muted">The TAL's percentages are a guide, not a cap. Check a final figure with the
<a href="{esc(tal.official_tool_url())}" target="_blank" rel="noopener">TAL's own tool</a> before the notice goes out.</p>
<form method="post" action="/leases/{r['id']}/tal" class="form card"><h2>Building figures (optional)</h2>
<div class="grid">{fields}</div><div class="actions-row"><button class="primary" type="submit">Calculate and save</button></div></form>"""
    return page("TAL calculator", body, "leases")


def renewal_page(r, settings, lang="fr"):
    msg = leases.renewal_message(r, settings, lang)
    mailto = "mailto:" + urllib.parse.quote(msg["to"]) + "?" + urllib.parse.urlencode(
        {"subject": msg["subject"], "body": msg["body"]}, quote_via=urllib.parse.quote)
    other = "en" if lang == "fr" else "fr"
    start, end, renewed = leases.current_term(r)
    opens, closes = leases.renewal_window(start, end)
    send = (f'<a class="btn primary" href="{esc(mailto)}">Open in my mail app</a>' if msg["to"]
            else '<span class="muted">No landlord email on file: copy the text, or call.</span>')
    imp = r.get("_import") or {}
    check = ""
    if imp:
        ev = "".join(f"<li><b>{esc(k)}</b>: {esc(v)}</li>" for k, v in (imp.get("evidence") or {}).items())
        verify = (f'<form method="post" action="/leases/{r["id"]}/verify"><button class="btn primary">The dates are right</button></form>'
                  if imp.get("needs_review") else f'<span class="muted">Checked {esc(imp.get("verified_at", "")[:10])}</span>' if imp.get("verified_at") else "")
        check = f"""<div class="banner{' ask' if imp.get('needs_review') else ''}"><p>Imported from {esc(imp.get('source'))}
{esc(imp.get('source_ref', ''))} · confidence {imp.get('confidence')} · end date {esc(imp.get('lease_end_basis'))}.
{'Check the lease dates against the source before sending.' if imp.get('needs_review') else ''}</p>{f'<ul>{ev}</ul>' if ev else ''}{verify}</div>"""
    body = f"""<h1>Renewal: {esc(r['address'])}</h1>{check}
<p class="lede">Landlord {esc(r['landlord_name'])} {esc(r.get('landlord_phone', ''))} · current term ends {end}{' (renewed by itself from ' + esc(r['lease_end']) + ')' if renewed else ''} ·
notice window {opens} to {closes} · {STATUS_LABEL[leases.renewal_status(r)]}</p>
<p>{send} <button class="btn" data-copy="#renewalmsg">Copy text</button>
 <a class="btn" href="/leases/{r['id']}/tal">TAL calculator</a>
 <a class="btn" href="/leases/{r['id']}/renewal?lang={other}">{'English' if other == 'en' else 'Français'}</a></p>
<div class="muted">To: {esc(msg['to']) or '—'} · Subject: {esc(msg['subject'])}</div>
<pre id="renewalmsg">{esc(msg['body'])}</pre>
<p class="muted">This message goes to the landlord only. The notice to the tenant is a separate step whose process
still needs settling (format, who sends it, tacit renewal): see docs/next-steps.md.</p>
<p><a href="/leases">Back to the tracker</a></p>
<script src="/ui/copy.js"></script>"""
    return page("Renewal message", body, "leases")


def lease_page(r):
    secs = "".join(
        f"<section><h2>{esc(t)}</h2><table>" +
        "".join(f"<tr><th>{esc(l)}</th><td>{esc(v) or '<span class=blank>________________</span>'}</td></tr>" for l, v in rows) +
        "</table></section>" for t, rows in leases.lease_sheet(r))
    body = f"""<div class="print"><h1>Lease prep sheet</h1>
<p class="muted">{esc(r['address'])}, {esc(r['city'])} · Prepared {esc(r['created_at'][:10])}.
Copy each value into the matching section of the official TAL lease form (the form is mandatory in Québec;
check the section letters against the edition you use). Blank lines still need an answer.</p>
{secs}<button class="btn noprint" onclick="print()">Print</button></div>"""
    return page("Lease sheet", body, "leases")


READING = {"unused": "Not used yet", "started": "Just started", "early": "Too early to tell", "alone": "Nothing to compare with",
           "ahead": "Ahead", "behind": "Behind", "same": "About the same"}


def working_html(conn, settings):
    """The top of the Scorecard: where the desk test stands, and which way of opening a message is working."""
    test, st = approaches.desk_test(conn, settings), approaches.stats(conn, settings)
    on = drafts.status(settings)["on"]
    plural = lambda n, one, many: f"{n} {one if n == 1 else many}"
    tiles = "".join(f'<div class="stat"><b>{n}</b><span>{esc(label)}</span></div>' for n, label in [
        (test["sent"], f"Sent, of {test['messages']}"), (test["replied"], "Replied"),
        (test["presentation"], "Presentations booked"), (test["signed"], f"Listings signed, goal {test['success_signed']}")])
    done = min(100, round(100 * test["sent"] / test["messages"])) if test["messages"] else 0
    test_html = f"""<section class="card working"><p class="eyebrow">The desk test</p>
<div class="kpis">{tiles}</div>
<div class="meter" role="img" aria-label="{test['sent']} of {test['messages']} messages sent"><i style="width:{done}%"></i></div>
<p class="verdict {test['state']}">{esc(approaches.verdict(test))}</p>
<p class="muted">The test is {test['messages']} first messages. {test['success_signed']} or more signed listings is a success;
{test['stop_signed']} or fewer is the sign to stop and rethink. A reply is the early sign, a signed listing is the goal.
Mark what happened to each message on the <a href="/#sent">Sent tab</a>.</p></section>"""

    if not on:
        note = ("Custom messages are off, so every message uses your standard wording and there is nothing to compare yet. "
                "The approaches below start being tested when custom messages are switched on.")
    elif st["phase"] == "learning":
        note = (f"Still testing evenly. The desk waits for about {st['min_sample']} messages with a known result per approach "
                f"before it leans on the numbers (the one with the fewest has {st['least']}). Until then it leans only "
                "lightly, so every approach writes close to the same share of new messages.")
    elif st["phase"] == "leaning":
        note = ("The desk now leans toward what works: the approaches landlords answer most write more of the new messages. "
                "Every approach keeps a share, so the testing goes on.")
    else:
        note = "No approach is switched on (config/approaches.json), so custom messages are written without one."
    trs = ""
    for r in st["rows"]:
        if r["share"] is not None and on:
            share = approaches.pct(r["share"])
        else:
            share = ("When you choose it" if r["id"] == approaches.TEMPLATE else "Off" if r["active"]
                     else "Stopped" if r["id"] not in approaches.BUILT_IN else "—")
        replied = f"{r['replied']} of {r['n']} · {approaches.pct(r['rate'])}" if r["n"] else "—"
        waiting = f'<small class="muted">{r["waiting"]} too recent to count</small>' if r["waiting"] else ""
        trs += (f"<tr class='rd-{r['reading']}'><td><b>{esc(r['name'])}</b><small class='muted'>{esc(r['what'])}</small></td>"
                f"<td class='num'>{r['sent']}{waiting}</td><td class='num'>{replied}</td><td class='num'>{r['declined'] if r['n'] else '—'}</td>"
                f"<td class='num'>{r['presentation'] if r['n'] else '—'}</td><td class='num'>{r['listing'] if r['n'] else '—'}</td>"
                f"<td><span class='reading {r['reading']}'>{READING[r['reading']]}</span></td><td class='num'>{share}</td></tr>")
    return test_html + f"""<h2 id="approaches">Which approach is working</h2>
<p class="lede"><b>{esc(st['headline'])}</b></p>
<p class="banner ask"><span>{esc(note)}</span></p>
<div class="tablewrap"><table class="approaches"><tr><th>Way of opening the message</th><th>Sent</th><th>Replied</th><th>Said no</th><th>Presentations</th>
<th>Signed</th><th>Reading</th><th>Share of new messages</th></tr>{trs}</table></div>
<p class="muted">Replied counts interested replies, including those that went on to a presentation or a listing; a landlord
who answers "no thanks" is under Said no. A message with nothing marked after {plural(st['no_reply_days'], 'day', 'days')}
counts as no reply. Ahead or Behind appears only once an approach has {st['min_sample']} results and its gap from the others
is bigger than chance alone would give.</p>"""


def stats_page(conn, settings=None):
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
                             ("Said no", "outcome='declined' AND outcome_at>=? AND outcome_at<?"),
                             ("Listing presentation", "outcome IN ('presentation','listing') AND outcome_at>=? AND outcome_at<?"),
                             ("Signed listing", "outcome='listing' AND outcome_at>=? AND outcome_at<?")]:
            for r in q(where, s, e):
                rows.setdefault(r["source"], {})[label] = r["n"]
        weeks.append((start, rows))
    cols = ["Surfaced", "Sent", "Rejected", "Replied", "Said no", "Listing presentation", "Signed listing"]
    html_rows = ""
    for start, rows in weeks:
        for src, vals in sorted(rows.items()) or [("—", {})]:
            html_rows += f"<tr><td>Week of {start}</td><td>{esc(src)}</td>" + "".join(f"<td>{vals.get(c, 0)}</td>" for c in cols) + "</tr>"
    body = f"""<h1>Scorecard</h1>{working_html(conn, settings or load_settings())}
<h2>Week by week</h2><p class="muted">Ads surfaced, messages sent, replies and listings, by source and week
(plan section 7). Replies and listings come from the buttons on each sent lead.</p>
<div class="tablewrap"><table><tr><th>Week</th><th>Source</th>{''.join(f'<th>{c}</th>' for c in cols)}</tr>{html_rows}</table></div>"""
    return page("Scorecard", body, "stats")


SAVED = {"details": "Your details are saved. New messages use them now.",
         "samples": "Your past messages are saved.",
         "samples-off": "Your past messages are saved. They'll shape your French and English wording once custom "
                        "messages are switched on (drafts.provider in config/settings.json).",
         "suggested": "Your past messages are saved, and the desk rewrote your French and English first messages "
                      "in your style. Read them below, then Save each one you want to use."}


UNSAVED_GUARD = """<script>/* Each box saves on its own: warn before leaving with another box's changes unsaved. */
let dirty = new Set(), sending = null;
document.querySelectorAll("form").forEach(f => {
  f.addEventListener("input", () => dirty.add(f));
  f.addEventListener("submit", () => { sending = f; });
});
addEventListener("beforeunload", e => { if ([...dirty].some(f => f !== sending)) e.preventDefault(); });</script>"""


def my_details_page(settings, saved="", errors=None, typed=None, note=""):
    """Stefan's contact details, message wording and style samples, each saved by its own button.
    errors/typed: a template that didn't save, with what Stefan typed, so nothing he wrote is lost."""
    errors, typed = errors or {}, typed or {}
    agent = settings.get("agent", {})
    details = "".join(field_html(k, label, "url" if k == "rental_page_url" else "text", False, agent.get(k, ""))
                      for k, label in profile.DETAILS)
    details += field_html("office_email", "Office email (where new rentals are sent)", "text", False,
                          settings.get("office_email", ""))
    sample_lead = {"address": "", "area": "Laval"}
    templates = ""
    sugg = profile.suggestions()
    for name, (label, allowed) in profile.TEMPLATES.items():
        suggested = name in sugg and name not in typed
        text = typed.get(name) or sugg.get(name) or profile.read_template(name)
        problem = "".join(f"<li>{esc(p)}</li>" for p in errors.get(name, []))
        kind, lang = name.rsplit("_", 1)
        preview = ""
        if kind in ("outreach", "followup") and not problem and not suggested:
            preview = (f'<details><summary>How it reads (an ad in Laval)</summary>'
                       f'<pre>{esc(outreach.render(sample_lead, settings, lang, kind))}</pre></details>')
        templates += f"""<form method="post" action="/me/template/{name}" class="form card" id="t-{name}">
<h3>{esc(label)}</h3>{f'<div class="banner bad">Not saved:<ul>{problem}</ul></div>' if problem else ''}
{'<p class="banner ask"><span>Saved. New messages use this wording now.</span></p>' if saved == name else ''}
{f'<p class="banner ask"><span>Suggested from your past messages, not used yet. Save to use it.</span><button class="btn" type="submit" formaction="/me/suggestion/{name}/discard">Keep my current wording</button></p>' if suggested else ''}
<label class="field wide"><textarea name="text" rows="12">{esc(text)}</textarea></label>
<p class="muted">Fill-ins the desk replaces: {esc(', '.join('{' + a + '}' for a in sorted(allowed)))}</p>
{preview}<div class="actions-row"><button class="primary" type="submit">Save</button></div></form>"""
    banner = f'<p class="banner ask"><span>{esc(SAVED[saved])}</span></p>' if saved in SAVED else ""
    if note:
        banner += f'<div class="banner bad">{esc(note)}</div>'
    body = f"""<h1>My details</h1>
<p class="lede">Your name, phone and the wording of your messages. Everything you save here is used by the next
message the desk shows you. The line about brokers is always added to first messages, even if you remove it.</p>
{banner}<form method="post" action="/me/details" class="form card"><h2>Contact details</h2>
<div class="grid">{details}</div><div class="actions-row"><button class="primary" type="submit">Save details</button></div></form>
<h2>Message wording</h2>
<p class="muted">Words in curly brackets, like {{agent_phone}}, are filled in by the desk. Keep them as they are.</p>
{templates}
<form method="post" action="/me/samples" class="form card" id="samples"><h2>Your past messages (style samples)</h2>
<p class="muted">Paste a few messages you've sent landlords before. Put a line of three dashes (---) between two
messages. They can be in French, English or both. When you save them, the desk rewrites your French and English
first messages above in your style for you to review, and writes each ad's custom message in your style.</p>
<label class="field wide"><textarea name="text" rows="14">{esc(profile.read_samples(settings))}</textarea></label>
<div class="actions-row"><button class="primary" type="submit">Save samples</button></div></form>
""" + UNSAVED_GUARD
    return page("My details", body, "me")


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
        d["drafts"] = drafts.saved(conn, d["id"])
        d["send"] = send.route(d["source"])
        out.append(d)
    agent = settings.get("agent", {})
    return {"leads": out, "counts": lead_counts(conn), "today": today_counts(conn),
            "limit": int(settings.get("message_char_limit", 1000)), "reasons": REJECT_REASONS,
            "drafting": drafts.status(settings), "learning": approaches.brief(conn, settings),
            "agent": {"n": agent.get("name", ""), "e": agent.get("email", ""), "p": agent.get("phone", "")}}


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


def template_used(lead, settings, message, saved_drafts=None, shown=None):
    """Which template an outgoing message came from, whether Stefan edited it, and the approach it counts for.

    The id carries a short hash of the template text, so a reworded template counts as a new variant
    in the learnings. A message written for the ad counts as its draft's id (model and samples) and its
    draft's approach; the standard message counts for the "template" approach.
    shown: what the review screen had in the box when Stefan started ({"custom": bool, "lang": "fr"}), which
    settles it when he edited the text; without it, the text itself is compared."""
    saved_drafts = saved_drafts or {}
    text = (message or "").strip()
    draft = lambda d, edited: (d["template"], edited, d.get("approach") or approaches.UNSET)
    standard = lambda lang, edited: (outreach.template_id("outreach", lang), edited, approaches.TEMPLATE)
    for d in saved_drafts.values():
        if text == d["text"].strip():
            return draft(d, False)
    for lang in ([lead.get("lang")] if lead.get("lang") else []) + ["fr", "en"]:
        if text == outreach.render(lead, settings, lang).strip():
            return standard(lang, False)
    if shown and shown.get("lang") in ("fr", "en") and "custom" in shown:
        if shown["custom"] and shown["lang"] in saved_drafts:
            return draft(saved_drafts[shown["lang"]], bool(message))
        if not shown["custom"]:
            return standard(shown["lang"], bool(message))
    lang = outreach.detect_lang(message or "") if message else (lead.get("lang") or "fr")
    if lang in saved_drafts:  # Stefan reworked the custom message
        return draft(saved_drafts[lang], bool(message))
    return standard(lang, bool(message))


OUTCOMES = ("replied", "declined", "presentation", "listing", "no_reply")  # what happened after a message was sent
LOGGED = {"approve", "sent", "reject", "later", "undo", "unsend", "set_outcome", *OUTCOMES}


def api_decide(conn, lead_id, body, settings=None):
    action = body.get("action")
    r = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not r:
        return {"error": "not found"}, 404
    t = db.now()
    _group(conn, r, action, t)
    template = edited = approach = None
    if action in ("approve", "sent") and (body.get("message") or action == "approve"):
        template, edited, approach = template_used(db.lead_dict(r), settings or load_settings(), body.get("message", ""),
                                                   drafts.saved(conn, lead_id), body.get("shown"))
    if action in LOGGED:  # the learning log: every decision and outcome, with the ad's signals
        db.log_event(conn, db.lead_dict(r), action, body.get("reason") or body.get("outcome"),
                     template or r["template"], edited, approach or r["approach"])
    if action == "approve":
        conn.execute("UPDATE leads SET status='approved', decided_at=?, message=?, template=?, approach=? WHERE id=?",
                     (t, body.get("message", ""), template, approach, lead_id))
    elif action == "sent":
        conn.execute("UPDATE leads SET status='sent', sent_at=?, message=COALESCE(?, message), template=COALESCE(?, template), "
                     "approach=COALESCE(?, approach) WHERE id=?", (t, body.get("message"), template, approach, lead_id))
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
    elif action in OUTCOMES:
        conn.execute("UPDATE leads SET outcome=?, outcome_at=? WHERE id=?", (action, t, lead_id))
    elif action == "set_outcome":  # undo of an outcome button: put the previous one back (or none)
        prev = body.get("outcome") or None
        if prev and prev not in OUTCOMES:
            return {"error": "bad outcome"}, 400
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


def api_draft(conn, lead_id, body, settings):
    """Write (or fetch the saved) custom message for one ad. Slow the first time: it waits for the model."""
    r = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not r:
        return {"error": "not found"}, 404
    lang = "en" if body.get("lang") == "en" else "fr"
    try:
        d = drafts.get_or_write(conn, db.lead_dict(r), lang, settings, fresh=bool(body.get("fresh")),
                                other=bool(body.get("other")))
    except drafts.DraftError as e:
        return {"error": str(e)}, 200
    return {"lang": lang, **d}, 200


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
            if path == "/api/scan":
                return self._json({**SCANNER.status, "boards": scan.board_status(conn, settings)})
            if path == "/capture":
                return self._send(200, capture.capture_page(page))
            if path == "/capture/install":
                return self._send(200, capture.install_page(page))
            if path == "/api/counts":
                return self._json({"counts": lead_counts(conn), "today": today_counts(conn)})
            if path == "/api/leads":
                return self._json(api_leads(conn, settings, (qs.get("tab") or ["review"])[0], (qs.get("phase") or [""])[0]))
            if path == "/rentals" or path.startswith("/rentals/") or path == "/rentals.csv":
                # Old links (bookmarks, the boot page, the launcher) keep working after the rename.
                new = "/leases" + path[len("/rentals"):]
                return self._send(301, "", headers={"Location": new + (("?" + url.query) if url.query else "")})
            if path == "/leases/new":
                return self._send(200, lease_form())
            if path == "/leases":
                return self._send(200, tracker_page(conn))
            if path == "/leases.csv":
                rows = leases.export_rows(conn)
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=["id", "created_at", "followup_date"] + leases.FIELD_KEYS, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
                return self._send(200, buf.getvalue(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=leases.csv"})
            if path == "/stats":
                return self._send(200, stats_page(conn))
            if path == "/me":
                return self._send(200, my_details_page(settings, (qs.get("saved") or [""])[0]))
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "leases" and parts[1].isdigit():
                r = leases.get(conn, int(parts[1]))
                if not r:
                    return self._send(404, page("Not found", "<h1>Not found</h1>"))
                if len(parts) == 2:
                    return self._send(200, lease_done(r, settings))
                if parts[2] == "followup.ics":
                    return self._send(200, leases.followup_ics(r), "text/calendar; charset=utf-8",
                                      {"Content-Disposition": f"attachment; filename=renewal-call-{r['id']}.ics"})
                if parts[2] == "lease":
                    return self._send(200, lease_page(r))
                if parts[2] == "tal":
                    return self._send(200, tal_page(r, saved="saved" in qs))
                if parts[2] == "renewal":
                    lang = (qs.get("lang") or ["fr"])[0]
                    return self._send(200, renewal_page(r, settings, "en" if lang == "en" else "fr"))
            return self._send(404, page("Not found", "<h1>Not found</h1>"))
        finally:
            conn.close()

    def _scan_stream(self, body):
        """Stream the photo scan to the browser as server-sent events, one field at a time."""
        events = lease_scan.extract(body.get("files") or [])
        try:
            first = next(events)
        except lease_scan.ScanError as e:
            return self._json({"error": str(e)}, 400)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def send(ev):
            self.wfile.write(("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        try:
            send(first)
            try:
                for ev in events:
                    send(ev)
            except lease_scan.ScanError as e:
                send({"type": "error", "message": str(e)})
            except Exception as e:  # never leave the form waiting on a silent stream
                send({"type": "error", "message": f"The scan stopped: {e}"})
            send({"type": "end"})
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        conn = db.connect()
        try:
            body = self._form()
            if path.startswith("/api/lead/") and path.endswith("/decide"):
                lead_id = urllib.parse.unquote(path[len("/api/lead/"):-len("/decide")])
                res, code = api_decide(conn, lead_id, body, load_settings())
                return self._json(res, code)
            if path.startswith("/api/lead/") and path.endswith("/draft"):
                lead_id = urllib.parse.unquote(path[len("/api/lead/"):-len("/draft")])
                return self._json(*api_draft(conn, lead_id, body, load_settings()))
            if path == "/api/decide-many":
                done = [api_decide(conn, i, body)[1] == 200 for i in body.get("ids", [])]
                return self._json({"ok": sum(done)})
            if path == "/api/manual":
                lid = feed.add_manual(conn, body)
                r = conn.execute("SELECT bucket FROM leads WHERE id=?", (lid,)).fetchone()
                return self._json({"id": lid, "bucket": r["bucket"] if r else ""})
            if path == "/api/lease-scan":
                return self._scan_stream(body)
            if path == "/api/leases/import":
                records = body.get("records", []) if isinstance(body, dict) else body
                report = lease_import.import_records(conn, records)
                return self._json({k: v for k, v in report.items()})
            if path.startswith("/leases/") and path.endswith("/tal"):
                rid = path.split("/")[2]
                keys = [tal.CURRENT_RENT[0]] + tal.INPUT_KEYS
                if rid.isdigit() and leases.save_tal_inputs(conn, int(rid), {k: body.get(k, "") for k in keys}):
                    return self._send(303, "", headers={"Location": f"/leases/{rid}/tal?saved=1"})
                return self._json({"error": "not found"}, 404)
            if path.startswith("/leases/") and path.endswith("/verify"):
                rid = path.split("/")[2]
                if rid.isdigit() and lease_import.mark_verified(conn, int(rid)):
                    return self._send(303, "", headers={"Location": f"/leases/{rid}/renewal"})
                return self._json({"error": "not found"}, 404)
            if path == "/api/scan":
                started = SCANNER.start(force=bool(body.get("force")), after=after_scan)
                return self._json({"started": started, **SCANNER.status})
            if path == "/api/recheck":
                return self._json(dict(feed.recheck_all(conn)))
            if path == "/me/details":
                profile.save_details(body)
                return self._send(303, "", headers={"Location": "/me?saved=details"})
            if path.startswith("/me/template/") and path[len("/me/template/"):] in profile.TEMPLATES:
                name = path[len("/me/template/"):]
                errors = profile.save_template(name, body.get("text", ""))
                if errors:
                    return self._send(400, my_details_page(load_settings(), errors={name: errors}, typed={name: body.get("text", "")}))
                return self._send(303, "", headers={"Location": f"/me?saved={name}#t-{name}"})
            if path == "/me/samples":
                settings = load_settings()
                profile.save_samples(settings, body.get("text", ""))
                if not drafts.status(settings)["on"]:
                    return self._send(303, "", headers={"Location": "/me?saved=samples-off#samples"})
                if not drafts.load_samples(settings):
                    return self._send(303, "", headers={"Location": "/me?saved=samples#samples"})
                failed = profile.suggest_templates(settings)
                if failed:
                    note = "The desk couldn't rewrite your wording this time: " + " ".join(sorted(set(failed.values())))
                    return self._send(200, my_details_page(load_settings(), "samples", note=note))
                return self._send(303, "", headers={"Location": "/me?saved=suggested#t-outreach_fr"})
            if path.startswith("/me/suggestion/") and path.endswith("/discard"):
                profile.drop_suggestion(path.split("/")[3])
                return self._send(303, "", headers={"Location": "/me"})
            if path in ("/leases/new", "/rentals/new"):
                errors = leases.validate(body)
                if errors:
                    return self._send(400, lease_form(body, errors))
                rid = leases.save(conn, body)
                return self._send(303, "", headers={"Location": f"/leases/{rid}"})
            return self._json({"error": "not found"}, 404)
        finally:
            conn.close()


def after_scan(conn, settings):
    """After each background read: refresh the learnings if they are a day old."""
    learn.maybe_write(conn, settings)


SCANNER = scan.Scanner(load_settings)


def serve(port=8765, open_browser=True, scan_on_start=False):
    """Open the review screen at once on what the desk already has. With scan_on_start, boards whose last
    read is stale are read in the background while Stefan works; the screen shows progress and new ads."""
    url = f"http://127.0.0.1:{port}/"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        return _port_taken(port, url, open_browser)
    print(f"Rentals desk running at {url}  (Ctrl+C to stop)")
    conn = db.connect()
    try:
        db.sync_phases(conn, load_areas())
    finally:
        conn.close()
    settings = load_settings()
    if scan_on_start:
        if not SCANNER.start(after=after_scan):
            print("Every board was read recently: showing what the desk already has.")
    SCANNER.loop(int(settings.get("scan", {}).get("auto_refresh_minutes", 60)) if scan_on_start else 0)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def _port_taken(port, url, open_browser):
    """Something already listens on the desk's port. If it is the desk, just open it; otherwise say what holds it."""
    import subprocess
    import urllib.request
    try:
        with urllib.request.urlopen(url + "api/counts", timeout=2) as r:
            json.load(r)
        print(f"The desk is already running at {url}  Opening it.")
        if open_browser:
            import webbrowser
            webbrowser.open(url)
        return
    except Exception:
        pass
    pids = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.split()
    what = ""
    if pids:
        what = subprocess.run(["ps", "-o", "command=", "-p", pids[0]], capture_output=True, text=True).stdout.strip()
    print(f"\nThe desk can't start: another program is using port {port}, which the desk needs.")
    if what:
        print(f"  That program is: {what}  (process {pids[0]})")
        print(f"  To stop it, close the window that started it, or run:  kill {pids[0]}")
    print("  Then open the desk again.\n")
    sys.exit(1)
