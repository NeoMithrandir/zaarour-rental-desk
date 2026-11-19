// Which way of opening a first message gets landlords to answer, and how new messages lean on it.
// Port of desk/approaches.py: the reasoning (what counts as success, how shares are worked out) is written up there.
import { detectLang } from "./outreach.js";
import { localMs, sha1hex } from "./util.js";

export const TEMPLATE = "template";
export const UNSET = "custom";
const POSITIVE = ["replied", "presentation", "listing"];
const DEFAULTS = {
  min_sample: 30, min_show: 5, no_reply_days: 14, lean_light: 0.25, lean_full: 0.75,
  prior_strength: 10, prior_rate: 0.08, window: 40,
  outcome_worth: { replied: 0.5, presentation: 0.8, listing: 1.0 },
  desk_test: { messages: 300, success_signed: 6, stop_signed: 2 },
};
export const BUILT_IN = {
  [TEMPLATE]: { id: TEMPLATE, name: "Standard message", what: "The desk's standard wording, the same for every ad.", instruction: "", active: false },
  [UNSET]: { id: UNSET, name: "Custom, no set approach", what: "Written for the ad without a set way of opening.", instruction: "", active: false },
};
const DRAWS = 3000;

export function cfg(settings) {
  const out = { ...DEFAULTS };
  for (const [k, v] of Object.entries(settings?.learn || {})) {
    out[k] = DEFAULTS[k] && typeof DEFAULTS[k] === "object" && v && typeof v === "object" ? { ...DEFAULTS[k], ...v } : v;
  }
  return out;
}

/** The approaches in config/approaches.json, in file order. raw: the parsed file. */
export function load(raw) {
  const out = [], seen = new Set(Object.keys(BUILT_IN));
  for (const a of raw?.approaches || []) {
    const id = String(a.id || "");
    if (!/^[a-z0-9_]{1,40}$/.test(id) || seen.has(id) || !String(a.instruction || "").trim()) continue;
    seen.add(id);
    out.push({ id, name: a.name || id, what: a.what || "", instruction: a.instruction.trim(), active: a.active === undefined ? true : Boolean(a.active) });
  }
  return out;
}

export function filedUnder(approach, template) {
  return approach || (String(template || "").startsWith("draft:") ? UNSET : TEMPLATE);
}

export function state(outcome, sentAt, noReplyDays = 14, nowMs = Date.now()) {
  if (outcome) return outcome;
  const sent = localMs(sentAt);
  return !Number.isNaN(sent) && sent <= nowMs - noReplyDays * 86400000 ? "quiet" : "waiting";
}

export function wilson(k, n, z = 1.96) {
  if (!n) return [0, 0];
  const p = k / n;
  const centre = (p + (z * z) / (2 * n)) / (1 + (z * z) / n);
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / (1 + (z * z) / n);
  return [Math.max(0, centre - half), Math.min(1, centre + half)];
}

const zero = () => ({ sent: 0, waiting: 0, n: 0, replied: 0, presentation: 0, listing: 0, declined: 0, no_reply: 0, points: 0 });

async function tally(db, c, nowMs) {
  const out = {};
  const { results } = await db.prepare("SELECT approach, template, outcome, sent_at FROM leads WHERE status='sent'").all();
  for (const r of results) {
    const id = filedUnder(r.approach, r.template);
    const t = (out[id] ||= zero());
    const s = state(r.outcome, r.sent_at, c.no_reply_days, nowMs);
    t.sent += 1;
    if (s === "waiting") { t.waiting += 1; continue; }
    t.n += 1;
    t.replied += POSITIVE.includes(s) ? 1 : 0;
    t.presentation += ["presentation", "listing"].includes(s) ? 1 : 0;
    t.listing += s === "listing" ? 1 : 0;
    t.declined += s === "declined" ? 1 : 0;
    t.no_reply += ["no_reply", "quiet"].includes(s) ? 1 : 0;
    t.points += parseFloat(c.outcome_worth[s] || 0);
  }
  return out;
}

// A seeded generator, so the same numbers always give the same shares.
function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function normal(rng) {
  let u = 0; while (!u) u = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rng());
}
function gamma(rng, a) {
  if (a < 1) return gamma(rng, a + 1) * Math.pow(rng() || 1e-12, 1 / a);
  const d = a - 1 / 3, c = 1 / Math.sqrt(9 * d);
  for (;;) {
    let x, v;
    do { x = normal(rng); v = 1 + c * x; } while (v <= 0);
    v = v * v * v;
    const u = rng();
    if (u < 1 - 0.0331 * x ** 4 || Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
  }
}
function beta(rng, a, b) {
  const x = gamma(rng, a), y = gamma(rng, b);
  return x / (x + y);
}

export function shares(arms, c, priorMean) {
  const k = arms.length;
  if (!k) return {};
  const lean = Math.min(...arms.map((a) => a.n)) < c.min_sample ? c.lean_light : c.lean_full;
  let best;
  if (new Set(arms.map((a) => `${a.n}:${a.points.toFixed(6)}`)).size === 1) {
    best = Object.fromEntries(arms.map((a) => [a.id, 1 / k]));
  } else {
    const mean = Math.min(0.99, Math.max(0.01, priorMean));
    const a0 = c.prior_strength * mean, b0 = c.prior_strength * (1 - mean);
    const rng = mulberry32(0), wins = Object.fromEntries(arms.map((a) => [a.id, 0]));
    for (let i = 0; i < DRAWS; i++) {
      let top = null, topV = -1;
      for (const a of arms) { const v = beta(rng, a0 + a.points, b0 + a.n - a.points); if (v > topV) { topV = v; top = a.id; } }
      wins[top] += 1;
    }
    best = Object.fromEntries(Object.entries(wins).map(([i, w]) => [i, w / DRAWS]));
  }
  return Object.fromEntries(arms.map((a) => [a.id, (1 - lean) / k + lean * best[a.id]]));
}

export async function stats(db, settings, defs, withShares = true, nowMs = Date.now()) {
  const c = cfg(settings);
  const t0 = await tally(db, c, nowMs);
  const known = Object.fromEntries(defs.map((a) => [a.id, a]));
  const ids = [...defs.map((a) => a.id), ...Object.keys(t0).filter((i) => !known[i])];
  if (!ids.includes(TEMPLATE)) ids.push(TEMPLATE);
  const vals = Object.values(t0);
  const totalN = vals.reduce((s, t) => s + t.n, 0), totalPoints = vals.reduce((s, t) => s + t.points, 0);
  const totalReplied = vals.reduce((s, t) => s + t.replied, 0);
  const priorMean = (totalPoints + c.prior_rate * c.prior_strength) / (totalN + c.prior_strength);
  const rows = [];
  for (const i of ids) {
    const a = known[i] || BUILT_IN[i] || { id: i, name: i, what: "No longer in config/approaches.json.", instruction: "", active: false };
    const t = t0[i] || zero();
    const [lo, hi] = wilson(t.replied, t.n);
    const othersN = totalN - t.n, othersK = totalReplied - t.replied;
    const others = othersN ? othersK / othersN : null;
    let reading;
    if (!t.sent) reading = "unused";
    else if (t.n < c.min_show) reading = "started";
    else if (t.n < c.min_sample) reading = "early";
    else if (others === null) reading = "alone";
    else { const [oLo, oHi] = wilson(othersK, othersN); reading = lo > oHi ? "ahead" : hi < oLo ? "behind" : "same"; }
    rows.push({ id: a.id, name: a.name, what: a.what, active: a.active, ...t, rate: t.n ? t.replied / t.n : null,
      range: [lo, hi], others, reading, share: null, score: (t.points + c.prior_strength * priorMean) / (t.n + c.prior_strength) });
  }
  const arms = rows.filter((r) => r.active);
  if (withShares) for (const [i, s] of Object.entries(shares(arms, c, priorMean))) arms.find((r) => r.id === i).share = s;
  const least = arms.length ? Math.min(...arms.map((r) => r.n)) : 0;
  const phase = !arms.length ? "none" : least < c.min_sample ? "learning" : "leaning";
  const order = Object.fromEntries(ids.map((i, n) => [i, n]));
  rows.sort((x, y) => (Number(!x.n) - Number(!y.n)) || ((x.n ? -x.score : 0) - (y.n ? -y.score : 0)) || order[x.id] - order[y.id]);
  const out = { rows, phase, least, min_sample: c.min_sample, no_reply_days: c.no_reply_days,
    base: totalN ? totalReplied / totalN : null, sent: vals.reduce((s, t) => s + t.sent, 0), known: totalN };
  out.headline = headline(out);
  return out;
}

export const pct = (x) => `${Math.round(100 * x)}%`;
const of = (r) => `${r.replied} ${r.replied === 1 ? "reply" : "replies"} from ${r.n} message${r.n === 1 ? "" : "s"}`;

export function headline(st) {
  if (!st.known) return "No answers to read yet. This fills in as you mark what happened to each sent message.";
  const solid = st.rows.filter((r) => ["ahead", "behind", "same", "alone"].includes(r.reading));
  const ahead = solid.filter((r) => r.reading === "ahead");
  if (ahead.length) { const r = ahead[0]; return `${r.name} is winning: ${of(r)} (${pct(r.rate)}), against ${pct(r.others)} for the rest.`; }
  const early = st.rows.filter((r) => r.reading === "early" && r.replied);
  const behind = solid.filter((r) => r.reading === "behind");
  if (behind.length) { const r = behind[behind.length - 1]; return `No approach is clearly ahead. ${r.name} is behind: ${pct(r.rate)} replied, against ${pct(r.others)} for the rest.`; }
  if (solid.length >= 2) return "No approach is clearly ahead: the differences so far are within chance.";
  if (early.length) {
    const r = early[0];
    return `Ahead so far: ${r.name}, with ${of(r)}. Too early to rely on: it takes about ${st.min_sample} messages with a known result per approach.`;
  }
  return `Too early to tell. It takes about ${st.min_sample} messages with a known result per approach.`;
}

export async function brief(db, settings, defs) {
  const st = await stats(db, settings, defs, false);
  const names = { ...Object.fromEntries(Object.entries(BUILT_IN).map(([i, a]) => [i, a.name])), ...Object.fromEntries(defs.map((a) => [a.id, a.name])) };
  return { phase: st.phase, least: st.least, min_sample: st.min_sample, no_reply_days: st.no_reply_days, names, active: defs.filter((a) => a.active).length };
}

/** The approach for this ad's message: the active one furthest below its share lately. null when none is active. */
export async function pick(db, leadId, settings, defs, exclude = []) {
  const c = cfg(settings);
  const arms = (await stats(db, settings, defs)).rows.filter((r) => r.share !== null && !exclude.includes(r.id));
  if (!arms.length) return null;
  const { results } = await db.prepare("SELECT approach, MAX(created_at) AS at FROM drafts WHERE lead_id!=? AND approach IS NOT NULL "
    + "GROUP BY lead_id ORDER BY at DESC LIMIT ?").bind(leadId, parseInt(c.window, 10)).all();
  const recent = results.map((r) => r.approach).filter((a) => arms.some((r) => r.id === a));
  const whole = arms.reduce((s, r) => s + r.share, 0);
  let best = null, bestKey = null;
  for (const r of arms) {
    const gap = Math.round(((r.share / whole) * (recent.length + 1) - recent.filter((a) => a === r.id).length) * 1e6) / 1e6;
    const key = [gap, await sha1hex(`${leadId}:${r.id}`)];
    if (!bestKey || key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) { best = r.id; bestKey = key; }
  }
  return best;
}

/** Answered messages of this approach, as sent: same language first, furthest outcome first. */
export async function answered(db, approachId, lang, settings) {
  const d = settings?.drafts || {};
  const least = parseInt(d.proven_min ?? 3, 10), most = parseInt(d.proven_max ?? 3, 10);
  if (!approachId || most <= 0) return [];
  const { results } = await db.prepare("SELECT message, outcome, outcome_at FROM leads WHERE status='sent' AND approach=? "
    + "AND outcome IN ('replied','presentation','listing') AND COALESCE(message,'')!='' ORDER BY outcome_at DESC").bind(approachId).all();
  if (results.length < Math.max(1, least)) return [];
  const worth = cfg(settings).outcome_worth;
  const keyed = results.map((r, i) => ({ r, i, k1: detectLang(r.message) !== lang ? 1 : 0, k2: -parseFloat(worth[r.outcome] || 0) }));
  keyed.sort((a, b) => a.k1 - b.k1 || a.k2 - b.k2 || a.i - b.i);
  return keyed.slice(0, most).map((x) => x.r.message);
}

export async function deskTest(db, settings) {
  const t = cfg(settings).desk_test;
  const { results } = await db.prepare("SELECT outcome FROM leads WHERE status='sent' ORDER BY sent_at, id").all();
  const first = results.slice(0, parseInt(t.messages, 10));
  const count = (outs) => first.filter((r) => outs.includes(r.outcome)).length;
  const signed = count(["listing"]);
  let st;
  if (signed >= t.success_signed) st = "success";
  else if (results.length < t.messages) st = "running";
  else st = signed <= t.stop_signed ? "stop" : "between";
  return { ...t, sent: first.length, sent_all: results.length, replied: count(POSITIVE), declined: count(["declined"]),
    presentation: count(["presentation", "listing"]), signed, state: st };
}

export function verdict(t) {
  const n = t.sent, k = t.signed, goal = t.success_signed, stop = t.stop_signed, total = t.messages;
  const s = k === 1 ? "" : "s";
  if (t.state === "success") return `Goal reached: ${k} signed listings from ${n} messages. The plan's bar is ${goal} from ${total}.`;
  if (t.state === "stop") return `${k} signed listing${s} from the first ${total} messages. That is the plan's stop line (${stop} or fewer): `
    + `stop and rethink before sending more. Listings from these ${total} still count if they sign later.`;
  if (t.state === "between") return `${k} signed listings from the first ${total} messages: between the stop line (${stop}) and the goal (${goal}). `
    + `A judgement call. Listings from these ${total} still count if they sign later.`;
  if (!n) return "No messages sent yet. The test starts with the first message you mark sent.";
  return `${k} signed listing${s} from ${n} message${n === 1 ? "" : "s"} so far. A listing takes weeks to sign after a `
    + `first message, so read this at ${total} messages, not before.`;
}
