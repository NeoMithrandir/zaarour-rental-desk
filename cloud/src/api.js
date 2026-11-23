// The review screen's API (port of desk/web.py's api_* functions and the Handler's /api routes). Every write to a
// lead records who made it (decided_by, the event's by_user) and stamps sync_at so the Mac picks it up.
import * as approaches from "./approaches.js";
import { canonicalId, route, sourceOf } from "./capture.js";
import { Checker, bucket } from "./checks.js";
import * as drafts from "./drafts.js";
import { importRecords } from "./leaseImport.js";
import { Outreach, detectLang } from "./outreach.js";
import { json, now, sha1hex, syncStamp, today } from "./util.js";

export const BROKER_LABEL = "Rejected as broker by Stefan";
export const REJECT_REASONS = ["Broker", "Already with an agency (listed by owner)", "Out of area", "Too small / low rent", "Bad property", "Tenant ad", "Other"];
const OUTCOMES = ["replied", "declined", "presentation", "listing", "no_reply"];
const LOGGED = new Set(["approve", "sent", "reject", "later", "undo", "unsend", "set_outcome", ...OUTCOMES]);

export function leadDict(r) {
  let flags = [];
  try { flags = JSON.parse(r.flags || "[]"); } catch { flags = []; }
  return { ...r, flags };
}

/** Who posted it (see desk/web.py poster_key). */
export function posterKey(d) {
  if (d.poster_id) return `${d.source}\u0000p\u0000${d.poster_id}`;
  if (["craigslist", "logisquebec"].includes(d.source) && d.title) return `${d.source}\u0000t\u0000${d.title.trim().toLowerCase()}`;
  return null;
}

async function all(db, sql, ...args) {
  return (await db.prepare(sql).bind(...args).all()).results;
}

/** Boards whose new ads stay off the review screen (settings hidden_sources, switched on My details). */
export function hiddenSources(settings) {
  return (settings?.hidden_sources || []).map(String);
}
const hiddenSql = (hidden) => (hidden.length ? ` AND source NOT IN (${hidden.map(() => "?").join(",")})` : "");

export async function leadCounts(db, hidden = []) {
  const counts = {};
  for (const r of await all(db, "SELECT status, COUNT(*) n FROM leads WHERE status!='new' GROUP BY status")) counts[r.status] = r.n;
  const seen = new Set();
  for (const r of await all(db, "SELECT id, bucket, source, poster_id, title FROM leads WHERE status='new'" + hiddenSql(hidden), ...hidden)) {
    const b = r.bucket;
    const key = ["review", "broker"].includes(b) ? posterKey(r) : null;
    const k = `${b}\u0001${key || r.id}`;
    if (key && seen.has(k)) continue;
    seen.add(k);
    counts[b] = (counts[b] || 0) + 1;
  }
  return counts;
}

export async function todayCounts(db) {
  const t = today();
  const q = async (where) => (await db.prepare(`SELECT COUNT(*) n FROM leads WHERE ${where}`).bind(t).first()).n;
  return { approved: await q("status IN ('approved','sent') AND decided_at>=?"), sent: await q("sent_at>=?"),
    rejected: await q("status='rejected' AND decided_at>=?"), later: await q("status='later' AND decided_at>=?") };
}

async function approachDefs(desk) {
  return approaches.load(await desk.config("approaches"));
}

async function samplesOf(desk) {
  return drafts.loadSamples((await desk.kv("samples")) || "");
}

/** The areas of the chosen phase (all of them for All areas), in config order, with their card counts (desk/web.py area_chips). */
export function areaChips(areas, phase, counts) {
  const out = Object.entries(areas).map(([name, a]) => ({ name, phase: parseInt(a.phase ?? 1, 10), n: counts[name] || 0 }))
    .filter((a) => !["1", "2"].includes(phase) || a.phase === parseInt(phase, 10));
  if (counts[""]) out.push({ name: "", phase: null, n: counts[""] });
  return out;
}

export async function apiLeads(desk, tab, phase = "", areas = []) {
  const db = desk.db, settings = await desk.settings();
  const wheres = { approved: ["status='approved'", []], sent: ["status='sent'", []], later: ["status='later'", []], rejected: ["status='rejected'", []] };
  const bkt = ["broker", "hidden"].includes(tab) ? tab : "review";
  let [where, args] = wheres[tab] || ["status='new' AND bucket=?", [bkt]];
  const hidden = hiddenSources(settings);
  if (!wheres[tab]) { where += hiddenSql(hidden); args = [...args, ...hidden]; } // switched off: still read, not shown
  if (["1", "2"].includes(phase)) { where = `(${where}) AND phase=?`; args = [...args, parseInt(phase, 10)]; }
  const order = tab === "sent" ? "sent_at DESC" : ["approved", "rejected"].includes(tab) ? "decided_at DESC"
    : "COALESCE(phase, 9), (poster_type='owner') DESC, first_seen DESC, posted_at DESC";
  const rows = await all(db, `SELECT * FROM leads WHERE ${where} ORDER BY ${order} LIMIT 3000`, ...args);
  const cards = [], byPoster = {}, perArea = {};
  const groups = ["review", "broker"].includes(tab); // one card per poster: a manager with 8 ads gets one message
  for (const r of rows) {
    const d = leadDict(r);
    const key = groups ? posterKey(d) : null;
    if (key && byPoster[key]) {
      byPoster[key].others.push({ title: d.title, url: d.url, address: d.address, price: d.price });
      continue;
    }
    d.others = [];
    if (key) byPoster[key] = d;
    cards.push(d);
    perArea[d.area || ""] = (perArea[d.area || ""] || 0) + 1;
  }
  // Ticked areas narrow the list; the counts on the area chips stay those of the whole phase.
  const wanted = new Set(areas);
  const out = wanted.size ? cards.filter((d) => wanted.has(d.area || "")) : cards;
  const msgs = new Outreach(desk, settings);
  const saved = await drafts.savedMany(db, out.map((d) => d.id));
  for (const d of out) {
    d.message_fr = await msgs.render(d, "fr");
    d.message_en = await msgs.render(d, "en");
    d.followup_fr = await msgs.render(d, "fr", "followup");
    d.followup_en = await msgs.render(d, "en", "followup");
    d.drafts = saved[d.id] || {};
    d.send = route(d.source);
  }
  const agent = settings.agent || {};
  const defs = await approachDefs(desk);
  return { leads: out, counts: await leadCounts(db, hidden), today: await todayCounts(db),
    limit: parseInt(settings.message_char_limit ?? 1000, 10), reasons: REJECT_REASONS,
    areas: areaChips(await desk.areas(), phase, perArea),
    drafting: drafts.status(settings, await samplesOf(desk)), learning: await approaches.brief(db, settings, defs),
    agent: { n: agent.name || "", e: agent.email || "", p: agent.phone || "" }, me: desk.user.email };
}

function sameAs(r, key) {
  if (key.split("\u0000")[1] === "p") return ["source=? AND poster_id=? AND id!=?", [r.source, r.poster_id, r.id]];
  return ["source=? AND lower(trim(title))=? AND id!=?", [r.source, key.split("\u0000")[2], r.id]];
}

/** Other ads from the same poster follow the decision on the one card shown for them. */
function groupStmt(db, r, action, t) {
  const key = posterKey(r);
  if (!key) return null;
  const [same, args] = sameAs(r, key);
  const why = `Same poster as ${r.id}`;
  if (["approve", "sent", "reject", "later"].includes(action)) {
    return db.prepare(`UPDATE leads SET status='duplicate', decided_at=?, reject_reason=?, sync_at=? WHERE ${same} AND status IN ('new','approved')`)
      .bind(t, why, syncStamp(), ...args);
  }
  if (action === "undo") {
    return db.prepare(`UPDATE leads SET status='new', decided_at=NULL, reject_reason=NULL, sync_at=? WHERE ${same} AND status='duplicate' AND reject_reason=?`)
      .bind(syncStamp(), ...args, why);
  }
  return null;
}

/** Which template a message came from, whether it was edited, and its approach (see desk/web.py template_used). */
export async function templateUsed(msgs, lead, message, savedDrafts = {}, shown = null) {
  const text = String(message || "").trim();
  const draft = (d, edited) => [d.template, edited, d.approach || approaches.UNSET];
  const standard = async (lang, edited) => [await msgs.templateId("outreach", lang), edited, approaches.TEMPLATE];
  for (const d of Object.values(savedDrafts)) if (text === String(d.text).trim()) return draft(d, false);
  for (const lang of [...(lead.lang ? [lead.lang] : []), "fr", "en"]) {
    if (text === (await msgs.render(lead, lang)).trim()) return standard(lang, false);
  }
  if (shown && ["fr", "en"].includes(shown.lang) && "custom" in shown) {
    if (shown.custom && savedDrafts[shown.lang]) return draft(savedDrafts[shown.lang], Boolean(message));
    if (!shown.custom) return standard(shown.lang, Boolean(message));
  }
  const lang = message ? detectLang(message) : lead.lang || "fr";
  if (savedDrafts[lang]) return draft(savedDrafts[lang], Boolean(message));
  return standard(lang, Boolean(message));
}

function eventStmt(db, lead, action, reason, template, edited, approach, by) {
  const signals = Object.fromEntries(["source", "area", "phase", "price", "bedrooms", "unit_type", "poster_type", "lang", "posted_at", "bucket", "first_seen"]
    .map((k) => [k, lead[k] ?? null]));
  signals.flags = [...new Set((lead.flags || []).map((f) => f.code))].sort();
  return db.prepare("INSERT INTO events (lead_id, at, action, reason, template, edited, signals, approach, by_user) VALUES (?,?,?,?,?,?,?,?,?)")
    .bind(lead.id, now(), action, reason ?? null, template ?? null, edited == null ? null : Number(Boolean(edited)), JSON.stringify(signals), approach ?? null, by || null);
}

/** One decision or outcome on one lead. Returns [body, status]. */
export async function apiDecide(desk, leadId, body) {
  const db = desk.db, by = desk.user.email || null;
  const action = body.action;
  const r = await db.prepare("SELECT * FROM leads WHERE id=?").bind(leadId).first();
  if (!r) return [{ error: "not found" }, 404];
  const lead = leadDict(r);
  const t = now(), s = syncStamp();
  const stmts = [];
  const g = groupStmt(db, r, action, t);
  if (g) stmts.push(g);
  let template = null, edited = null, approach = null;
  if (["approve", "sent"].includes(action) && (body.message || action === "approve")) {
    const msgs = new Outreach(desk, await desk.settings());
    [template, edited, approach] = await templateUsed(msgs, lead, body.message || "", await drafts.saved(db, leadId), body.shown);
  }
  if (LOGGED.has(action)) stmts.push(eventStmt(db, lead, action, body.reason || body.outcome, template || r.template, edited, approach || r.approach, by));
  const upd = (sql, ...args) => stmts.push(db.prepare(sql).bind(...args));
  if (action === "approve") {
    upd("UPDATE leads SET status='approved', decided_at=?, message=?, template=?, approach=?, decided_by=?, sync_at=? WHERE id=?",
      t, body.message || "", template, approach, by, s, leadId);
  } else if (action === "sent") {
    upd("UPDATE leads SET status='sent', sent_at=?, message=COALESCE(?, message), template=COALESCE(?, template), approach=COALESCE(?, approach), "
      + "decided_by=COALESCE(decided_by, ?), sync_at=? WHERE id=?", t, body.message ?? null, template, approach, by, s, leadId);
  } else if (action === "reject") {
    const reason = body.reason || "Other";
    upd("UPDATE leads SET status='rejected', decided_at=?, reject_reason=?, decided_by=?, sync_at=? WHERE id=?", t, reason, by, s, leadId);
    if (reason === "Broker" && r.poster_id) { // learn: the next ad from this poster is flagged automatically
      upd("INSERT OR REPLACE INTO known_posters VALUES (?,?,?,?)", r.source, r.poster_id, BROKER_LABEL, t);
    }
  } else if (action === "later") {
    upd("UPDATE leads SET status='later', decided_at=?, decided_by=?, sync_at=? WHERE id=?", t, by, s, leadId);
  } else if (action === "undo") {
    if (r.status === "rejected" && r.reject_reason === "Broker" && r.poster_id) { // un-learn a mis-click
      upd("DELETE FROM known_posters WHERE source=? AND poster_id=? AND label=?", r.source, r.poster_id, BROKER_LABEL);
    }
    upd("UPDATE leads SET status='new', decided_at=NULL, reject_reason=NULL, sent_at=NULL, decided_by=NULL, sync_at=? WHERE id=?", s, leadId);
  } else if (action === "unsend") {
    upd("UPDATE leads SET status='approved', sent_at=NULL, sync_at=? WHERE id=?", s, leadId);
  } else if (OUTCOMES.includes(action)) {
    upd("UPDATE leads SET outcome=?, outcome_at=?, sync_at=? WHERE id=?", action, t, s, leadId);
  } else if (action === "set_outcome") {
    const prev = body.outcome || null;
    if (prev && !OUTCOMES.includes(prev)) return [{ error: "bad outcome" }, 400];
    upd("UPDATE leads SET outcome=?, outcome_at=?, sync_at=? WHERE id=?", prev, prev ? t : null, s, leadId);
  } else if (action === "rebucket") {
    const b = body.bucket;
    if (!["review", "broker", "hidden"].includes(b)) return [{ error: "bad bucket" }, 400];
    upd("UPDATE leads SET bucket=?, sync_at=? WHERE id=?", b, s, leadId);
  } else if (action === "note") {
    upd("UPDATE leads SET notes=?, sync_at=? WHERE id=?", body.notes || "", s, leadId);
  } else {
    return [{ error: "unknown action" }, 400];
  }
  await db.batch(stmts);
  return [{ ok: true }, 200];
}

export async function apiDraft(desk, leadId, body) {
  const r = await desk.db.prepare("SELECT * FROM leads WHERE id=?").bind(leadId).first();
  if (!r) return [{ error: "not found" }, 404];
  const lang = body.lang === "en" ? "en" : "fr";
  try {
    const d = await drafts.getOrWrite(desk.db, leadDict(r), lang, await desk.settings(), await approachDefs(desk), await samplesOf(desk),
      { fresh: Boolean(body.fresh), other: Boolean(body.other) });
    return [{ lang, ...d }, 200];
  } catch (e) {
    if (e instanceof drafts.DraftError) return [{ error: e.message }, 200];
    throw e;
  }
}

// ---------------------------------------------------------------- the checks, for ads added on the desk and Recheck
export async function checker(desk) {
  const known = Object.fromEntries((await all(desk.db, "SELECT poster_id, label FROM known_posters")).map((r) => [r.poster_id, r.label]));
  return new Checker(await desk.areas(), await desk.settings(), await desk.config("known_brokers"), known, (await desk.kv("reference")) || {});
}

const LEAD_FIELDS = ["id", "source", "source_id", "url", "title", "snippet", "address", "area", "phase", "lat", "lng",
  "price", "bedrooms", "unit_type", "poster_id", "poster_type", "posted_at", "image", "search", "lang", "flags", "bucket", "detailed"];

/** Check and store leads the desk itself received (by hand, the capture button, Recheck). */
export async function processLeads(desk, leads, check, { refresh = true } = {}) {
  const db = desk.db;
  const perPoster = {};
  for (const l of leads) if (l.poster_id) perPoster[l.poster_id] = (perPoster[l.poster_id] || 0) + 1;
  const counts = { new: 0, already_seen: 0, review: 0, broker: 0, hidden: 0 };
  const stmts = [];
  for (const lead of leads) {
    const [area, flags] = check.run(lead);
    const n = perPoster[lead.poster_id] || 0;
    if (n >= 3 && !flags.some((f) => ["broker", "property_manager"].includes(f.code))) {
      flags.push({ code: "property_manager", level: "info", text: `Same poster has ${n} ads today` });
    }
    Object.assign(lead, { area, flags, bucket: bucket(flags), phase: check.phase[area] ?? null,
      lang: detectLang((lead.title || "") + " " + (lead.snippet || "")) });
    const row = Object.fromEntries(LEAD_FIELDS.map((k) => [k, lead[k] ?? null]));
    row.flags = JSON.stringify(lead.flags);
    const have = await db.prepare("SELECT id FROM leads WHERE id=?").bind(row.id).first();
    if (have) {
      counts.already_seen += 1;
      if (!refresh) continue;
      const fields = LEAD_FIELDS.filter((k) => k !== "id");
      stmts.push(db.prepare(`UPDATE leads SET ${fields.map((k) => `${k}=?`).join(", ")}, sync_at=? WHERE id=?`)
        .bind(...fields.map((k) => row[k]), syncStamp(), row.id));
    } else {
      counts.new += 1; counts[lead.bucket] = (counts[lead.bucket] || 0) + 1;
      const cols = [...LEAD_FIELDS, "first_seen", "sync_at"];
      stmts.push(db.prepare(`INSERT INTO leads (${cols.join(",")}) VALUES (${cols.map(() => "?").join(",")})`)
        .bind(...LEAD_FIELDS.map((k) => row[k]), now(), syncStamp()));
    }
  }
  for (let i = 0; i < stmts.length; i += 200) await db.batch(stmts.slice(i, i + 200));
  return counts;
}

/** An ad pasted in by hand or sent with the capture button (see desk/feed.py add_manual). */
export async function addManual(desk, form) {
  const url = String(form.url || "").trim();
  const source = form.source || sourceOf(url) || "facebook";
  const price = String(form.price ?? "").trim();
  const lead = {
    id: url ? await canonicalId(url, source) : "manual:" + (await sha1hex((form.title || "") + (form.address || ""))).slice(0, 12),
    source, source_id: "", url, title: String(form.title || "").trim(), snippet: String(form.text || "").trim(),
    address: String(form.address || "").trim(), price: /^\d+$/.test(price) ? parseInt(price, 10) : null,
    poster_id: String(form.poster || "").trim(), poster_type: "", posted_at: String(form.posted_at || "").trim() || now(),
    search: form.via || "manual",
  };
  await processLeads(desk, [lead], await checker(desk), { refresh: false });
  const r = await desk.db.prepare("SELECT bucket FROM leads WHERE id=?").bind(lead.id).first();
  return { id: lead.id, bucket: r ? r.bucket : "" };
}

/** Re-run the checks on undecided leads (after adding brokers, a Centris export or new areas). */
export async function recheckAll(desk) {
  const rows = (await all(desk.db, "SELECT * FROM leads WHERE status='new'")).map(leadDict);
  for (const lead of rows) {
    if (((lead.title || "") + (lead.snippet || "")).includes("removed]")) {
      lead.number_hits = lead.flags.filter((f) => f.code === "broker").flatMap((f) =>
        ["Phone matches a known broker", "Email matches a known broker"].filter((h) => f.text.includes(h)));
    }
  }
  return processLeads(desk, rows, await checker(desk));
}

/** Undecided ads posted (or first seen) more than expire_after_days ago are set aside (desk/feed.py expire_old). */
export async function expireOld(db, days) {
  if (!days) return 0;
  const cutoff = new Date(Date.now() - parseFloat(days) * 86400000);
  const r = await db.prepare("UPDATE leads SET status='expired', decided_at=?, sync_at=? WHERE status='new' "
    + "AND substr(COALESCE(NULLIF(posted_at, ''), first_seen), 1, 10) < ?").bind(now(), syncStamp(), today(cutoff)).run();
  return r.meta.changes || 0;
}

/** Re-tag ads when an area changes phase (desk/db.py sync_phases). */
export async function syncPhases(db, areas) {
  let changed = 0;
  for (const [name, a] of Object.entries(areas)) {
    const n = parseInt(a.phase ?? 1, 10);
    const rows = await all(db, "SELECT id, flags FROM leads WHERE area=? AND phase IS NOT ?", name, n);
    const stmts = rows.map((r) => {
      let flags = [];
      try { flags = JSON.parse(r.flags || "[]"); } catch { flags = []; }
      flags = flags.filter((f) => f.code !== "phase2");
      if (n > 1) flags.push({ code: "phase2", level: "info", text: "Phase 2 area" });
      return db.prepare("UPDATE leads SET phase=?, flags=?, sync_at=? WHERE id=?").bind(n, JSON.stringify(flags), syncStamp(), r.id);
    });
    for (let i = 0; i < stmts.length; i += 200) await db.batch(stmts.slice(i, i + 200));
    changed += rows.length;
  }
  return changed;
}

// ---------------------------------------------------------------- reading the boards (done by the Mac)
const STALE_MS = 15 * 60000; // the Mac reports every minute; after this long it counts as away

/** What the scan bar shows: the Mac's last report, and whether the Mac is there at all. */
export async function scanStatus(desk) {
  const st = (await desk.kv("agent_status")) || {};
  const seen = Date.parse(st.reported_at || "");
  const away = Number.isNaN(seen) || Date.now() - seen > STALE_MS;
  const req = await desk.kv("scan_request");
  const waiting = Boolean(req && !req.taken_at) && !away;
  const lines = waiting && !st.running ? ["Asking the Mac to read the boards"] : st.lines || [];
  return { running: (Boolean(st.running) || waiting) && !away, lines, last: st.last || null, boards: st.boards || [],
    mac_seen: st.reported_at || null, mac_away: away };
}

/** "Check boards": leave a request the Mac picks up within a minute. The Mac decides which boards are due. */
export async function requestScan(desk, body) {
  const st = await scanStatus(desk);
  if (st.running || st.mac_away) return { started: false, ...st };
  await desk.put("scan_request", { at: syncStamp(), by: desk.user.email, force: Boolean(body.force) });
  return scanStatus(desk).then((s) => ({ started: true, ...s }));
}

export async function apiLeaseImport(desk, body) {
  const records = Array.isArray(body) ? body : body.records || [];
  return importRecords(desk.db, records, await desk.settings(), Boolean(body.dry_run));
}

export { json };
