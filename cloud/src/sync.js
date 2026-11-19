// What the Mac and the online desk say to each other (the Mac side is desk/cloud.py). The Mac reads the boards and
// pushes what it found; people decide on the desk, and the Mac pulls their decisions back for its learnings and its
// broker list. Only the Mac calls these routes (Access service token plus the AGENT_TOKEN secret).
//
// Who owns what: the ad's facts (title, text, price, area, flags, bucket, phone) come from the Mac's scanners; the
// decisions (status, message, outcome, notes, who decided) and the leases belong to the desk. A push never overwrites
// a decision, and a pull never sends facts the Mac would overwrite its own with.
import { expireOld, syncPhases } from "./api.js";
import { kvPut } from "./config.js";
import { json, now, syncStamp } from "./util.js";

const FACTS = ["source", "source_id", "url", "title", "snippet", "address", "area", "phase", "lat", "lng", "price", "bedrooms",
  "unit_type", "poster_id", "poster_type", "posted_at", "image", "search", "lang", "flags", "bucket", "detailed",
  "phone", "phone_via", "phone_checked_at"];
const DECISIONS = ["status", "decided_at", "reject_reason", "message", "sent_at", "outcome", "outcome_at", "notes", "template", "approach"];
const LEAD_COLS = ["id", ...FACTS, "first_seen", ...DECISIONS, "decided_by"];
const CHUNK = 200;

const pick = (o, keys) => keys.map((k) => (o[k] === undefined ? null : typeof o[k] === "object" && o[k] !== null ? JSON.stringify(o[k]) : o[k]));

async function run(db, stmts) {
  for (let i = 0; i < stmts.length; i += CHUNK) await db.batch(stmts.slice(i, i + CHUNK));
}

async function putConfig(desk, body) {
  const changed = [];
  if (body.settings && typeof body.settings === "object") await desk.put("mac_settings", body.settings);
  for (const [name, value] of Object.entries(body.config || {})) {
    if (!["areas", "approaches", "tal_rates", "known_brokers"].includes(name) || value == null) continue;
    const before = await desk.kv("config:" + name);
    if (JSON.stringify(before) !== JSON.stringify(value)) { await desk.put("config:" + name, value); changed.push(name); }
  }
  if (body.reference && typeof body.reference === "object") await desk.put("reference", body.reference);
  if (changed.includes("areas")) await syncPhases(desk.db, (await desk.config("areas")).areas);
  return changed;
}

/** New and refreshed ads from the Mac's scanners, its scan log, settings and status. */
export async function ingest(desk, body) {
  const db = desk.db;
  const leads = body.leads || [];
  let inserted = 0, updated = 0;
  const stmts = [];
  if (leads.length) {
    const ids = leads.map((l) => l.id);
    const have = new Set();
    for (let i = 0; i < ids.length; i += 90) {
      const part = ids.slice(i, i + 90);
      const { results } = await db.prepare(`SELECT id FROM leads WHERE id IN (${part.map(() => "?").join(",")})`).bind(...part).all();
      for (const r of results) have.add(r.id);
    }
    for (const l of leads) {
      if (!l.id) continue;
      if (have.has(l.id)) {
        updated += 1;
        stmts.push(db.prepare(`UPDATE leads SET ${FACTS.map((k) => `${k}=?`).join(", ")} WHERE id=?`).bind(...pick(l, FACTS), l.id));
      } else {
        inserted += 1;
        const row = { ...l, status: l.status || "new", first_seen: l.first_seen || now() };
        stmts.push(db.prepare(`INSERT INTO leads (${LEAD_COLS.join(",")}, sync_at) VALUES (${LEAD_COLS.map(() => "?").join(",")}, ?)`)
          .bind(...pick(row, LEAD_COLS), syncStamp()));
      }
    }
  }
  for (const s of body.scan_runs || []) {
    stmts.push(db.prepare("INSERT OR IGNORE INTO scan_runs (source, started_at, finished_at, ok, ads, new, requests, not_modified, error, mac_id) "
      + "VALUES (?,?,?,?,?,?,?,?,?,?)").bind(...pick(s, ["source", "started_at", "finished_at", "ok", "ads", "new", "requests", "not_modified", "error"]), s.id));
  }
  await run(db, stmts);
  const changed = await putConfig(desk, body);
  if (body.status) await kvPut(db, "agent_status", { ...body.status, reported_at: syncStamp() }, "mac").run();
  const expired = body.final === false ? 0 : await expireOld(db, (await desk.teamSettings()).expire_after_days ?? 30);
  return { ok: true, inserted, updated, expired, config_changed: changed };
}

/** The Mac's heartbeat: its scanner status in, a waiting "Check boards" request out (handed over once). */
export async function poll(desk, body) {
  const db = desk.db;
  if (body.status) await kvPut(db, "agent_status", { ...body.status, reported_at: syncStamp() }, "mac").run();
  const req = await desk.kv("scan_request");
  let scan = null;
  if (req && !req.taken_at) {
    scan = req;
    await desk.put("scan_request", { ...req, taken_at: syncStamp() });
  }
  return { ok: true, scan, time: syncStamp() };
}

const PAGE = 1000;

/** Everything the desk changed since the Mac's last pull. since: the cursor the last pull returned. */
export async function pull(desk, params) {
  const db = desk.db;
  const since = params.get("since") || "";
  const inclusive = params.get("incl") === "1";
  const eventsAfter = parseInt(params.get("events_after") || "0", 10) || 0;
  const start = new Date(Date.now() - 5000).toISOString(); // a write landing during this pull is fetched again next time
  const op = inclusive ? ">=" : ">";
  const leads = (await db.prepare(`SELECT * FROM leads WHERE sync_at ${op} ? ORDER BY sync_at, id LIMIT ?`).bind(since, PAGE).all()).results;
  const more = leads.length === PAGE;
  const drafts = (await db.prepare("SELECT * FROM drafts WHERE lead_id IN (SELECT lead_id FROM drafts WHERE sync_at > ?)").bind(since).all()).results;
  const rentals = (await db.prepare("SELECT * FROM rentals WHERE sync_at > ?").bind(since).all()).results;
  const events = (await db.prepare("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 5000").bind(eventsAfter).all()).results;
  const posters = (await db.prepare("SELECT * FROM known_posters").all()).results;
  let cursor = more ? leads[leads.length - 1].sync_at : start;
  if (!more && since && cursor < since) cursor = since;
  return { cursor, incl: more, more, leads, drafts, rentals, events, events_more: events.length === 5000, known_posters: posters };
}

const MIGRATE = {
  leads: (db, r) => db.prepare(`INSERT OR REPLACE INTO leads (${LEAD_COLS.join(",")}, sync_at) VALUES (${LEAD_COLS.map(() => "?").join(",")}, NULL)`)
    .bind(...pick(r, LEAD_COLS)),
  known_posters: (db, r) => db.prepare("INSERT OR REPLACE INTO known_posters VALUES (?,?,?,?)").bind(...pick(r, ["source", "poster_id", "label", "added_at"])),
  scan_runs: (db, r) => db.prepare("INSERT OR IGNORE INTO scan_runs (source, started_at, finished_at, ok, ads, new, requests, not_modified, error, mac_id) "
    + "VALUES (?,?,?,?,?,?,?,?,?,?)").bind(...pick(r, ["source", "started_at", "finished_at", "ok", "ads", "new", "requests", "not_modified", "error", "id"])),
  events: (db, r) => db.prepare("INSERT OR REPLACE INTO events (id, lead_id, at, action, reason, template, edited, signals, approach) VALUES (?,?,?,?,?,?,?,?,?)")
    .bind(...pick(r, ["id", "lead_id", "at", "action", "reason", "template", "edited", "signals", "approach"])),
  drafts: (db, r) => db.prepare("INSERT OR REPLACE INTO drafts (lead_id, lang, text, template, model, created_at, approach, picked_by) VALUES (?,?,?,?,?,?,?,?)")
    .bind(...pick(r, ["lead_id", "lang", "text", "template", "model", "created_at", "approach", "picked_by"])),
  rentals: (db, r) => db.prepare("INSERT OR REPLACE INTO rentals (id, created_at, data, followup_date, lease_end, address, external_id, origin) VALUES (?,?,?,?,?,?,?,?)")
    .bind(...pick(r, ["id", "created_at", "data", "followup_date", "lease_end", "address", "external_id", "origin"])),
};
const KV_ALLOWED = /^(samples|suggestions|desk_settings|template:[a-z_]+)$/;

/**
 * The one-time move of the Mac's whole desk: one table at a time, in parts. The first part refuses when the online
 * desk already has ads (so a second run can't overwrite what people decided online) unless force is set.
 */
export async function migrate(desk, body) {
  const db = desk.db;
  if (body.start) {
    const n = (await db.prepare("SELECT COUNT(*) n FROM leads").first()).n;
    if (n && !body.force) return [{ error: `The online desk already has ${n} ads. The move from the Mac runs once; `
      + "run it with --force only to replace the online desk with the Mac's copy." }, 409];
    if (body.force) {
      await db.batch(["leads", "known_posters", "scan_runs", "events", "drafts", "rentals"].map((t) => db.prepare(`DELETE FROM ${t}`)));
    }
    await putConfig(desk, body);
    await desk.put("migrated", { at: now(), by: "mac", started: true });
    return [{ ok: true }, 200];
  }
  if (body.table === "kv") {
    const stmts = (body.rows || []).filter((r) => KV_ALLOWED.test(r.key)).map((r) => kvPut(db, r.key, r.value, "mac"));
    await run(db, stmts);
    return [{ ok: true, rows: stmts.length }, 200];
  }
  if (body.table === "done") {
    await syncPhases(db, (await desk.config("areas")).areas);
    const maxEvent = (await db.prepare("SELECT COALESCE(MAX(id), 0) m FROM events").first()).m;
    const counts = {};
    for (const t of Object.keys(MIGRATE)) counts[t] = (await db.prepare(`SELECT COUNT(*) n FROM ${t}`).first()).n;
    await desk.put("migrated", { at: now(), by: "mac", counts });
    return [{ ok: true, events_after: maxEvent, cursor: new Date().toISOString(), counts }, 200];
  }
  const make = MIGRATE[body.table];
  if (!make) return [{ error: "unknown table" }, 400];
  const stmts = (body.rows || []).map((r) => make(db, r));
  await run(db, stmts);
  return [{ ok: true, rows: stmts.length }, 200];
}

export async function route(desk, request, url, ctx) {
  const p = url.pathname;
  if (p === "/api/agent/pull" && request.method === "GET") return json(await pull(desk, url.searchParams));
  if (request.method !== "POST") return json({ error: "not found" }, 404);
  const body = await request.json().catch(() => ({}));
  if (p === "/api/agent/ingest") return json(await ingest(desk, body));
  if (p === "/api/agent/poll") return json(await poll(desk, body));
  if (p === "/api/agent/migrate") { const [res, code] = await migrate(desk, body); return json(res, code); }
  return json({ error: "not found" }, 404);
}
