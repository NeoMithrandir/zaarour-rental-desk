// The desk's pages (port of desk/web.py's page functions): the review screen's shell, New lease, the renewal tracker,
// the TAL calculator, the landlord message, the lease sheet, the Scorecard and My details.
import * as approaches from "./approaches.js";
import * as drafts from "./drafts.js";
import * as leases from "./leases.js";
import * as profile from "./profile.js";
import * as tal from "./tal.js";
import { ROUTES } from "./capture.js";
import { Outreach } from "./outreach.js";
import { addDays, esc, g, iso, money0, money2, todayDate } from "./util.js";

export function page(title, body, active = "", who = "") {
  const nav = [["leads", "/", "Leads"], ["lease", "/leases/new", "New lease"], ["leases", "/leases", "Renewals"],
    ["stats", "/stats", "Scorecard"], ["me", "/me", "My details"]]
    .map(([key, href, label]) => `<a href="${href}" class="${key === active ? "on" : ""}">${label}</a>`).join("");
  const signed = who ? `<span class="who" title="Signed in with Cloudflare Access">${esc(who)}</span>` : "";
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)} · Zaarour Rentals</title>
<link rel="icon" type="image/png" href="/ui/zaarour-favicon.png"><link rel="apple-touch-icon" href="/ui/zaarour-favicon.png">
<link rel="stylesheet" href="/ui/style.css"></head><body>
<header><a class="brand" href="/"><img class="logo" src="/ui/zaarour-logo.png" width="158" height="44" alt="Zaarour &amp; Associates"><span>Rentals desk</span></a><nav>${nav}</nav>${signed}</header>
<main class="${active}">${body}</main></body></html>`;
}

const notFound = (who) => page("Not found", "<h1>Not found</h1>", "", who);
export { notFound };

// ---------------------------------------------------------------- leads
export function leadsPage(template, areas, who) {
  let body = template;
  for (const n of [1, 2]) {
    const tip = "Covers " + Object.entries(areas).filter(([, a]) => parseInt(a.phase ?? 1, 10) === n).map(([name]) => name).join(", ") + ".";
    body = body.replaceAll(`{phase${n}_areas}`, esc(tip));
  }
  return page("Leads", body, "leads", who);
}

export function capturePage(template, who) {
  return page("Send to desk", template + '<script src="/ui/capture.js"></script>', "leads", who);
}

// ---------------------------------------------------------------- forms
const WIDE = new Set(["address", "landlord_address", "tenant_current_address", "appliances", "notes_for_office",
  "lowest_rent_12m", "new_building", "building_rules"]);

export function fieldHtml(key, label, kind, required, value = "") {
  const req = required ? " required" : "";
  const wide = WIDE.has(key) || kind === "textarea" ? " wide" : "";
  let inp;
  if (kind === "textarea") inp = `<textarea name="${key}" id="f-${key}" rows="3">${esc(value)}</textarea>`;
  else if (kind === "check") {
    inp = `<input type="checkbox" name="${key}" id="f-${key}"${value ? " checked" : ""}>`;
    return `<label class="field check${wide}">${inp}<span>${esc(label)}</span></label>`;
  } else if (kind.startsWith("select:")) {
    const opts = ["", ...kind.slice(7).split("|")].map((o) => `<option${o === value ? " selected" : ""}>${esc(o)}</option>`).join("");
    inp = `<select name="${key}" id="f-${key}"${req}>${opts}</select>`;
  } else {
    const t = { money: "number", number: "number" }[kind] || kind;
    const step = kind === "money" ? ' step="0.01" min="0" inputmode="decimal"' : "";
    inp = `<input type="${t}" name="${key}" id="f-${key}" value="${esc(value)}"${step}${req}>`;
    if (kind === "money") inp = `<span class="money">${inp}</span>`;
  }
  const tag = required ? '<em class="req">Required</em>' : "";
  return `<label class="field${wide}"><span class="lbl">${esc(label)}${tag}</span>${inp}</label>`;
}

export function leaseForm(settings, { values = {}, errors = [], keepsScans = false, who = "" } = {}) {
  const sections = {};
  for (const [key, label, kind, req, section] of leases.FIELDS) (sections[section] ||= []).push(fieldHtml(key, label, kind, req, values[key] ?? ""));
  const err = errors.map((e) => `<li>${esc(e)}</li>`).join("");
  const months = parseInt(settings.followup_months_before_end ?? 6, 10);
  const sid = (s) => "s-" + s.toLowerCase();
  const toc = Object.keys(sections).map((s) => `<a href="#${sid(s)}" data-sec="${sid(s)}"><i></i>${esc(s)}</a>`).join("");
  const kept = keepsScans ? "kept in the desk's private storage" : "not kept once they are read";
  const body = `<div class="formpage">
<aside class="toc noprint"><div class="toc-in"><p class="eyebrow">Sections</p>${toc}</div></aside>
<div class="formmain">
<h1>New lease</h1>
<p class="lede">Fill this in when a lease is signed. Saving adds it to the renewal tracker with a renewal call
${months} months before the lease ends, writes the email to the office and prepares the lease sheet. Nothing is sent until you send the email.</p>
${err ? `<div class="banner bad"><strong>Almost there.</strong><ul>${err}</ul></div>` : ""}
<section class="card scanbox noprint" id="scanbox">
<h2>Scan the lease</h2>
<p class="muted">Take photos of each page with your phone or drop in scans or a PDF. The form fills itself as the pages
are read; highlighted fields came from the photos, and orange ones need a closer look. Nothing saves until you click Save.
The pages are sent to the Claude API to be read, and ${kept}.</p>
<label class="dropzone" id="dropzone"><input type="file" id="scanfiles" accept="image/*,application/pdf" capture="environment" multiple>
<span><b>Take photos or choose files</b> · or drop them here</span></label>
<ul class="scanpages" id="scanpages"></ul>
<div class="actions-row"><button type="button" class="primary" id="scanbtn" disabled>Read the pages</button>
<span id="scanstatus" class="muted" role="status" aria-live="polite"></span></div>
</section>
<form method="post" action="/leases/new" class="form" id="leaseform" data-months="${months}">
<input type="hidden" name="scan_meta" id="scan_meta" value="">
${Object.entries(sections).map(([s, f]) => `<section class="card fsec" id="${sid(s)}"><h2>${esc(s)}</h2><div class="grid">${f.join("")}</div></section>`).join("")}
<div class="savebar"><span id="left" class="muted"></span><span id="renewal" class="muted"></span>
<button class="primary big" type="submit">Save and prepare email</button></div>
</form></div></div>
<script src="/ui/form.js"></script>
<script src="/ui/scan.js"></script>`;
  return page("New lease", body, "lease", who);
}

const q = encodeURIComponent;
const mailto = (m) => `mailto:${q(m.to)}?subject=${q(m.subject)}&body=${q(m.body)}`;

export function leaseDone(r, settings, who) {
  const mail = leases.officeEmail(r, settings);
  const body = `<h1>Saved: ${esc(r.address)}</h1>
<ol class="steps">
<li><strong>Email the office the lease details.</strong> <a class="btn primary" href="${esc(mailto(mail))}">Open in my mail app</a>
 <button class="btn" data-copy="#mailbody">Copy text</button>
 <div class="muted">To: ${esc(mail.to)} · Subject: ${esc(mail.subject)}</div>
 <pre id="mailbody">${esc(mail.body)}</pre></li>
<li><strong>Add the renewal call to your calendar</strong> (${esc(r.followup_date)}). It's also on the
 <a href="/leases">renewal tracker</a> with the message for the landlord.
 <a class="btn primary" href="/leases/${r.id}/followup.ics">Download calendar event</a></li>
<li><strong>Lease.</strong> <a class="btn" href="/leases/${r.id}/lease" target="_blank">Open lease prep sheet</a></li>
</ol>
<p><a href="/leases/new">Enter another lease</a></p>
<script src="/ui/copy.js"></script>`;
  return page("Lease saved", body, "lease", who);
}

// ---------------------------------------------------------------- renewals
const STATUS_LABEL = { due: "Call now", check: "Check data", upcoming: "Upcoming", ended: "Lease ended" };

export function trackerPage(rows, settings, who) {
  for (const r of rows) {
    r._status = leases.renewalStatus(r, settings);
    r._call = iso(leases.nextCall(r, settings));
    const [, end, renewed] = leases.currentTerm(r);
    r._end = iso(end) + (renewed ? " (renewed)" : "");
  }
  const order = { due: 0, check: 1, upcoming: 2, ended: 3 };
  rows.sort((a, b) => order[a._status] - order[b._status] || (a._call < b._call ? -1 : a._call > b._call ? 1 : 0));
  const due = rows.filter((r) => r._status === "due").length, check = rows.filter((r) => r._status === "check").length;
  const trs = rows.map((r) => `<tr class='st-${r._status}'><td><span class='rstat ${r._status}'>${STATUS_LABEL[r._status]}</span></td>`
    + `<td>${esc(r._call)}</td><td>${esc(r.address)}, ${esc(r.city)}</td><td>${esc(r.landlord_name)}</td>`
    + `<td>${esc(r._end)}</td><td><a class='btn' href='/leases/${r.id}/renewal'>Landlord message</a> `
    + `<a class='btn' href='/leases/${r.id}/tal'>TAL calculator</a> `
    + `<a href='/leases/${r.id}'>Details</a> · <a href='/leases/${r.id}/lease' target='_blank'>Lease sheet</a></td></tr>`).join("");
  const months = parseInt(settings.followup_months_before_end ?? 6, 10);
  const body = `<h1>Lease renewal tracker</h1>
<p class="lede">${rows.length} lease${rows.length !== 1 ? "s" : ""} tracked, ${due} to call now${check ? `, ${check} imported with data to check` : ""}. Each lease gets a renewal call
${months} months before it ends, when the landlord's notice window opens. <b>Landlord message</b> has the message ready
to send. <a href="/leases.csv">Download CSV</a></p>
<table><tr><th></th><th>Renewal call</th><th>Property</th><th>Landlord</th><th>Lease ends</th><th></th></tr>${trs}</table>
<p class="muted">Leases come from <a href="/leases/new">New lease</a> and from imports (docs/lease-import-spec.md).
"Check data" means an imported lease whose end date was guessed or low-confidence: confirm it before calling.</p>`;
  return page("Lease renewal tracker", body, "leases", who);
}

export function talPage(r, rates, saved, who) {
  const inputs = r.tal_inputs || {};
  const calc = tal.forLease(rates, r, leases.currentTerm);
  const fields = [tal.CURRENT_RENT, ...tal.INPUTS].map(([key, label, help]) => `<label class="field wide"><span class="lbl">${esc(label)}</span>`
    + `<span class="money"><input type="number" step="0.01" min="0" name="${key}" value="${esc(inputs[key] ?? "")}"></span>`
    + `<small class="muted">${esc(help)}</small></label>`).join("");
  let result;
  if (calc) {
    const rows = calc.lines.map((l) => `<tr><td>${esc(l.label)}</td><td>$${money2(l.amount)}</td></tr>`).join("");
    const prov = calc.provisional ? `<p class='banner ask'>The rates for leases renewing on ${calc.renewal_start} aren't published yet `
      + `(the TAL publishes them in January). This uses the ${calc.period} rates; update `
      + "<code>config/tal_rates.json</code> on the Mac when the new ones are out (it syncs to the desk).</p>" : "";
    result = `${prov}<table><tr><th>Renewal starts</th><td>${calc.renewal_start}</td></tr>
<tr><th>Current rent</th><td>$${money2(calc.rent)}</td></tr>${rows}
<tr><th>Increase</th><td><b>$${money2(calc.increase)} a month (${calc.increase_pct.toFixed(2)} %)</b></td></tr>
<tr><th>New rent</th><td><b>$${money0(calc.new_rent)}</b> (rounded to the dollar)</td></tr>
<tr><th>This unit's share of the building</th><td>${g(calc.share_pct)} %</td></tr></table>`;
  } else {
    result = "<p class='banner ask'>Add the current rent below to calculate.</p>";
  }
  const body = `<h1>TAL calculator: ${esc(r.address)}</h1>
<p class="lede">Follows the Tribunal administratif du logement's method for notices given since 1 January 2026
(base percentage, municipal and school taxes, insurance, 5 % of major work). With only the rent, it applies the base
percentage; add the building's figures for the full calculation. These numbers go straight into the
<a href="/leases/${r.id}/renewal">landlord message</a>.</p>
${saved ? '<p class="banner">Saved.</p>' : ""}${result}
<p class="muted">The TAL's percentages are a guide, not a cap. Check a final figure with the
<a href="${esc(tal.officialToolUrl(rates))}" target="_blank" rel="noopener">TAL's own tool</a> before the notice goes out.</p>
<form method="post" action="/leases/${r.id}/tal" class="form card"><h2>Building figures (optional)</h2>
<div class="grid">${fields}</div><div class="actions-row"><button class="primary" type="submit">Calculate and save</button></div></form>`;
  return page("TAL calculator", body, "leases", who);
}

export function renewalPage(r, settings, rates, template, lang, who) {
  const msg = leases.renewalMessage(rates, r, settings, lang, template);
  const other = lang === "fr" ? "en" : "fr";
  const [start, end, renewed] = leases.currentTerm(r);
  const [opens, closes] = leases.renewalWindow(start, end);
  const send = msg.to ? `<a class="btn primary" href="${esc(mailto(msg))}">Open in my mail app</a>`
    : '<span class="muted">No landlord email on file: copy the text, or call.</span>';
  const imp = r._import || {};
  let check = "";
  if (Object.keys(imp).length) {
    const ev = Object.entries(imp.evidence || {}).map(([k, v]) => `<li><b>${esc(k)}</b>: ${esc(v)}</li>`).join("");
    const verify = imp.needs_review ? `<form method="post" action="/leases/${r.id}/verify"><button class="btn primary">The dates are right</button></form>`
      : imp.verified_at ? `<span class="muted">Checked ${esc(String(imp.verified_at).slice(0, 10))}</span>` : "";
    check = `<div class="banner${imp.needs_review ? " ask" : ""}"><p>Imported from ${esc(imp.source)}
${esc(imp.source_ref || "")} · confidence ${esc(imp.confidence)} · end date ${esc(imp.lease_end_basis)}.
${imp.needs_review ? "Check the lease dates against the source before sending." : ""}</p>${ev ? `<ul>${ev}</ul>` : ""}${verify}</div>`;
  }
  const body = `<h1>Renewal: ${esc(r.address)}</h1>${check}
<p class="lede">Landlord ${esc(r.landlord_name)} ${esc(r.landlord_phone || "")} · current term ends ${iso(end)}${renewed ? " (renewed by itself from " + esc(r.lease_end) + ")" : ""} ·
notice window ${iso(opens)} to ${iso(closes)} · ${STATUS_LABEL[leases.renewalStatus(r, settings)]}</p>
<p>${send} <button class="btn" data-copy="#renewalmsg">Copy text</button>
 <a class="btn" href="/leases/${r.id}/tal">TAL calculator</a>
 <a class="btn" href="/leases/${r.id}/renewal?lang=${other}">${other === "en" ? "English" : "Français"}</a></p>
<div class="muted">To: ${esc(msg.to) || "—"} · Subject: ${esc(msg.subject)}</div>
<pre id="renewalmsg">${esc(msg.body)}</pre>
<p class="muted">This message goes to the landlord only. The notice to the tenant is a separate step whose process
still needs settling (format, who sends it, tacit renewal): see docs/next-steps.md.</p>
<p><a href="/leases">Back to the tracker</a></p>
<script src="/ui/copy.js"></script>`;
  return page("Renewal message", body, "leases", who);
}

export function leasePage(r, who) {
  const secs = leases.leaseSheet(r).map(([t, rows]) => `<section><h2>${esc(t)}</h2><table>`
    + rows.map(([l, v]) => `<tr><th>${esc(l)}</th><td>${esc(v) || "<span class=blank>________________</span>"}</td></tr>`).join("")
    + "</table></section>").join("");
  const body = `<div class="print"><h1>Lease prep sheet</h1>
<p class="muted">${esc(r.address)}, ${esc(r.city)} · Prepared ${esc(String(r.created_at || "").slice(0, 10))}.
Copy each value into the matching section of the official TAL lease form (the form is mandatory in Québec;
check the section letters against the edition you use). Blank lines still need an answer.</p>
${secs}<button class="btn noprint" onclick="print()">Print</button></div>`;
  return page("Lease sheet", body, "leases", who);
}

// ---------------------------------------------------------------- scorecard
const READING = { unused: "Not used yet", started: "Just started", early: "Too early to tell", alone: "Nothing to compare with",
  ahead: "Ahead", behind: "Behind", same: "About the same" };

async function workingHtml(db, settings, defs, draftingOn) {
  const test = await approaches.deskTest(db, settings), st = await approaches.stats(db, settings, defs);
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const tiles = [[test.sent, `Sent, of ${test.messages}`], [test.replied, "Replied"], [test.presentation, "Presentations booked"],
    [test.signed, `Listings signed, goal ${test.success_signed}`]].map(([n, label]) => `<div class="stat"><b>${n}</b><span>${esc(label)}</span></div>`).join("");
  const done = test.messages ? Math.min(100, Math.round((100 * test.sent) / test.messages)) : 0;
  const testHtml = `<section class="card working"><p class="eyebrow">The desk test</p>
<div class="kpis">${tiles}</div>
<div class="meter" role="img" aria-label="${test.sent} of ${test.messages} messages sent"><i style="width:${done}%"></i></div>
<p class="verdict ${test.state}">${esc(approaches.verdict(test))}</p>
<p class="muted">The test is ${test.messages} first messages. ${test.success_signed} or more signed listings is a success;
${test.stop_signed} or fewer is the sign to stop and rethink. A reply is the early sign, a signed listing is the goal.
Mark what happened to each message on the <a href="/#sent">Sent tab</a>.</p></section>`;
  let note;
  if (!draftingOn) note = "Custom messages are off, so every message uses your standard wording and there is nothing to compare yet. "
    + "The approaches below start being tested when custom messages are switched on.";
  else if (st.phase === "learning") note = `Still testing evenly. The desk waits for about ${st.min_sample} messages with a known result per approach `
    + `before it leans on the numbers (the one with the fewest has ${st.least}). Until then it leans only `
    + "lightly, so every approach writes close to the same share of new messages.";
  else if (st.phase === "leaning") note = "The desk now leans toward what works: the approaches landlords answer most write more of the new messages. "
    + "Every approach keeps a share, so the testing goes on.";
  else note = "No approach is switched on (config/approaches.json), so custom messages are written without one.";
  const trs = st.rows.map((r) => {
    const share = r.share !== null && draftingOn ? approaches.pct(r.share)
      : r.id === approaches.TEMPLATE ? "When you choose it" : r.active ? "Off" : !(r.id in approaches.BUILT_IN) ? "Stopped" : "—";
    const replied = r.n ? `${r.replied} of ${r.n} · ${approaches.pct(r.rate)}` : "—";
    const waiting = r.waiting ? `<small class="muted">${r.waiting} too recent to count</small>` : "";
    return `<tr class='rd-${r.reading}'><td><b>${esc(r.name)}</b><small class='muted'>${esc(r.what)}</small></td>`
      + `<td class='num'>${r.sent}${waiting}</td><td class='num'>${replied}</td><td class='num'>${r.n ? r.declined : "—"}</td>`
      + `<td class='num'>${r.n ? r.presentation : "—"}</td><td class='num'>${r.n ? r.listing : "—"}</td>`
      + `<td><span class='reading ${r.reading}'>${READING[r.reading]}</span></td><td class='num'>${share}</td></tr>`;
  }).join("");
  return testHtml + `<h2 id="approaches">Which approach is working</h2>
<p class="lede"><b>${esc(st.headline)}</b></p>
<p class="banner ask"><span>${esc(note)}</span></p>
<div class="tablewrap"><table class="approaches"><tr><th>Way of opening the message</th><th>Sent</th><th>Replied</th><th>Said no</th><th>Presentations</th>
<th>Signed</th><th>Reading</th><th>Share of new messages</th></tr>${trs}</table></div>
<p class="muted">Replied counts interested replies, including those that went on to a presentation or a listing; a landlord
who answers "no thanks" is under Said no. A message with nothing marked after ${plural(st.no_reply_days, "day", "days")}
counts as no reply. Ahead or Behind appears only once an approach has ${st.min_sample} results and its gap from the others
is bigger than chance alone would give.</p>`;
}

export async function statsPage(db, settings, defs, draftingOn, who) {
  const t = todayDate();
  const monday = addDays(t, -((t.getUTCDay() + 6) % 7));
  const cols = ["Surfaced", "Sent", "Rejected", "Replied", "Said no", "Listing presentation", "Signed listing"];
  const wheres = [["Surfaced", "bucket!='hidden' AND first_seen>=? AND first_seen<?"], ["Sent", "sent_at>=? AND sent_at<?"],
    ["Rejected", "status='rejected' AND decided_at>=? AND decided_at<?"],
    ["Replied", "outcome IN ('replied','presentation','listing') AND outcome_at>=? AND outcome_at<?"],
    ["Said no", "outcome='declined' AND outcome_at>=? AND outcome_at<?"],
    ["Listing presentation", "outcome IN ('presentation','listing') AND outcome_at>=? AND outcome_at<?"],
    ["Signed listing", "outcome='listing' AND outcome_at>=? AND outcome_at<?"]];
  let htmlRows = "";
  for (let i = 0; i < 4; i++) {
    const start = addDays(monday, -7 * i), s = iso(start), e = iso(addDays(start, 7));
    const res = await db.batch(wheres.map(([, where]) => db.prepare(`SELECT source, COUNT(*) n FROM leads WHERE ${where} GROUP BY source`).bind(s, e)));
    const rows = {};
    res.forEach((r, j) => { for (const x of r.results) (rows[x.source] ||= {})[wheres[j][0]] = x.n; });
    const entries = Object.entries(rows).sort(([a], [b]) => (String(a) < String(b) ? -1 : 1));
    for (const [src, vals] of entries.length ? entries : [["—", {}]]) {
      htmlRows += `<tr><td>Week of ${s}</td><td>${esc(src)}</td>` + cols.map((c) => `<td>${vals[c] || 0}</td>`).join("") + "</tr>";
    }
  }
  const body = `<h1>Scorecard</h1>${await workingHtml(db, settings, defs, draftingOn)}
<h2>Week by week</h2><p class="muted">Ads surfaced, messages sent, replies and listings, by source and week
(plan section 7). Replies and listings come from the buttons on each sent lead.</p>
<div class="tablewrap"><table><tr><th>Week</th><th>Source</th>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>${htmlRows}</table></div>`;
  return page("Scorecard", body, "stats", who);
}

// ---------------------------------------------------------------- my details
const SAVED = {
  details: "Your details are saved. New messages use them now.",
  samples: "Your past messages are saved.",
  boards: "Saved. The review screen shows the ticked boards' new ads.",
  "samples-off": "Your past messages are saved. They'll shape your French and English wording once custom "
    + "messages are switched on (drafts.provider in config/settings.json on the Mac).",
  suggested: "Your past messages are saved, and the desk rewrote your French and English first messages "
    + "in your style. Read them below, then Save each one you want to use.",
};

const UNSAVED_GUARD = `<script>/* Each box saves on its own: warn before leaving with another box's changes unsaved. */
let dirty = new Set(), sending = null;
document.querySelectorAll("form").forEach(f => {
  f.addEventListener("input", () => dirty.add(f));
  f.addEventListener("submit", () => { sending = f; });
});
addEventListener("beforeunload", e => { if ([...dirty].some(f => f !== sending)) e.preventDefault(); });</script>`;

/** Which boards' new ads the review screen shows. Unticked boards keep being read and stored. */
function boardsForm(settings) {
  const hidden = new Set((settings.hidden_sources || []).map(String));
  const boxes = Object.entries(ROUTES).map(([k, r]) => `<label class="field check"><input type="checkbox" name="show_${k}"${hidden.has(k) ? "" : " checked"}>`
    + `<span>${esc(r.name)}</span></label>`).join("");
  return `<form method="post" action="/me/boards" class="form card" id="boards"><h2>Boards on the desk</h2>
<p class="muted">Untick a board to keep its new ads off the review screen. Its scanner keeps reading it and the ads are kept,
so ticking it again brings them back. Ads already approved, sent or in Later stay where they are. This is for the whole team.</p>
<div class="grid">${boxes}</div><div class="actions-row"><button class="primary" type="submit">Save boards</button></div></form>`;
}

/** The signed-in person's contact details, the team's message wording and style samples, each saved by its own button. */
export async function myDetailsPage(desk, { saved = "", errors = {}, typed = {}, note = "" } = {}) {
  const settings = await desk.settings();
  const agent = settings.agent || {};
  const mine = await desk.profile();
  let details = profile.DETAILS.map(([k, label]) => fieldHtml(k, label, k === "rental_page_url" ? "url" : "text", false, agent[k] || "")).join("");
  details += fieldHtml("office_email", "Office email (where new rentals are sent)", "text", false, settings.office_email || "");
  const msgs = new Outreach(desk, settings);
  const sugg = await profile.suggestions(desk);
  let templates = "";
  for (const [name, [label, allowedFills]] of Object.entries(profile.TEMPLATES)) {
    const suggested = name in sugg && !(name in typed);
    const text = typed[name] || sugg[name] || (await desk.template(name));
    const problem = (errors[name] || []).map((p) => `<li>${esc(p)}</li>`).join("");
    const [kind, lang] = [name.slice(0, name.lastIndexOf("_")), name.slice(name.lastIndexOf("_") + 1)];
    let preview = "";
    if (["outreach", "followup"].includes(kind) && !problem && !suggested) {
      preview = `<details><summary>How it reads (an ad in Laval)</summary><pre>${esc(await msgs.render({ address: "", area: "Laval" }, lang, kind))}</pre></details>`;
    }
    templates += `<form method="post" action="/me/template/${name}" class="form card" id="t-${name}">
<h3>${esc(label)}</h3>${problem ? `<div class="banner bad">Not saved:<ul>${problem}</ul></div>` : ""}
${saved === name ? '<p class="banner ask"><span>Saved. New messages use this wording now.</span></p>' : ""}
${suggested ? `<p class="banner ask"><span>Suggested from your past messages, not used yet. Save to use it.</span><button class="btn" type="submit" formaction="/me/suggestion/${name}/discard">Keep my current wording</button></p>` : ""}
<label class="field wide"><textarea name="text" rows="12">${esc(text)}</textarea></label>
<p class="muted">Fill-ins the desk replaces: ${esc([...allowedFills].sort().map((a) => `{${a}}`).join(", "))}</p>
${preview}<div class="actions-row"><button class="primary" type="submit">Save</button></div></form>`;
  }
  let banner = SAVED[saved] ? `<p class="banner ask"><span>${esc(SAVED[saved])}</span></p>` : "";
  if (note) banner += `<div class="banner bad">${esc(note)}</div>`;
  const whose = mine ? "" : `<p class="banner ask"><span>These are the desk's shared details until you save your own.
Each person signed in to the desk keeps their own name, phone and email; messages you approve use yours.</span></p>`;
  const body = `<h1>My details</h1>
<p class="lede">Your name, phone and the wording of your messages. Everything you save here is used by the next
message the desk shows you. The line about brokers is always added to first messages, even if you remove it.
Your contact details are yours alone (${esc(desk.user.email)}); the message wording and style samples are shared by the team.</p>
${banner}<form method="post" action="/me/details" class="form card"><h2>Contact details</h2>${whose}
<div class="grid">${details}</div><div class="actions-row"><button class="primary" type="submit">Save details</button></div></form>
${boardsForm(settings)}
<h2>Message wording</h2>
<p class="muted">Words in curly brackets, like {agent_phone}, are filled in by the desk. Keep them as they are.</p>
${templates}
<form method="post" action="/me/samples" class="form card" id="samples"><h2>Your past messages (style samples)</h2>
<p class="muted">Paste a few messages you've sent landlords before. Put a line of three dashes (---) between two
messages. They can be in French, English or both. When you save them, the desk rewrites your French and English
first messages above in your style for you to review, and writes each ad's custom message in your style.</p>
<label class="field wide"><textarea name="text" rows="14">${esc(await profile.readSamples(desk))}</textarea></label>
<div class="actions-row"><button class="primary" type="submit">Save samples</button></div></form>
` + UNSAVED_GUARD;
  return page("My details", body, "me", desk.user.email);
}

export { drafts };
