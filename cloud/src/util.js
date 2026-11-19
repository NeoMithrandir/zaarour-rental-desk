// Small helpers shared by the Worker: HTML escaping, Montreal-time dates (the desk's records are in local time, as on
// the Mac), responses, hashing.

const TZ = "America/Toronto"; // Montreal

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#x27;" }[c]));
}

function parts(d) {
  const f = new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  });
  return Object.fromEntries(f.formatToParts(d).map((p) => [p.type, p.value]));
}

/** 'YYYY-MM-DDTHH:MM:SS' in Montreal time, like the Mac's db.now(). */
export function now(d = new Date()) {
  const p = parts(d);
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}

/** Today's date in Montreal, 'YYYY-MM-DD'. */
export function today(d = new Date()) {
  return now(d).slice(0, 10);
}

/** When the Mac should fetch a row again: UTC with milliseconds, only for syncing. */
export function syncStamp() {
  return new Date().toISOString();
}

/** A Montreal local 'YYYY-MM-DDTHH:MM:SS' (or a date) as milliseconds since 1970, for comparisons. */
export function localMs(s) {
  if (!s) return NaN;
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!m) return NaN;
  const asUtc = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  // The offset Montreal had at about that moment.
  const p = parts(new Date(asUtc));
  const shown = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  return asUtc - (shown - asUtc);
}

// ---------------------------------------------------------------- plain dates (no time zone involved)
export function parseDate(s) {
  const m = String(s || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) throw new RangeError("bad date");
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  if (d.getUTCMonth() !== +m[2] - 1 || d.getUTCDate() !== +m[3]) throw new RangeError("bad date");
  return d;
}

export function iso(d) {
  return d.toISOString().slice(0, 10);
}

export function addDays(d, n) {
  return new Date(d.getTime() + n * 86400000);
}

export function addMonths(d, months) {
  const total = d.getUTCMonth() + months;
  const y = d.getUTCFullYear() + Math.floor(total / 12);
  const m = ((total % 12) + 12) % 12;
  const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, m, Math.min(d.getUTCDate(), last)));
}

export function todayDate() {
  return parseDate(today());
}

// ---------------------------------------------------------------- responses
export function html(body, status = 200, headers = {}) {
  return new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8", ...SECURITY, ...headers } });
}

export function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...headers } });
}

export function redirect(location, status = 303) {
  return new Response("", { status, headers: { Location: location } });
}

export const SECURITY = {
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "same-origin",
  "X-Content-Type-Options": "nosniff",
};

// ---------------------------------------------------------------- hashing
export async function sha1hex(text) {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Constant-time comparison of two strings (tokens). */
export function sameSecret(a, b) {
  a = String(a || ""); b = String(b || "");
  if (!a || !b || a.length !== b.length) return false;
  let x = 0;
  for (let i = 0; i < a.length; i++) x |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return x === 0;
}

/** Python's str.format_map for {name} fill-ins ({{ and }} are literal braces). Unknown names throw. */
export function formatMap(template, values) {
  return template.replace(/\{\{|\}\}|\{([^{}]*)\}/g, (m, name) => {
    if (m === "{{") return "{";
    if (m === "}}") return "}";
    if (!(name in values)) throw new Error(`unknown fill-in {${name}}`);
    return String(values[name] ?? "");
  });
}

/** Python-like f"{n:,.2f}". */
export function money2(n) {
  return Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function money0(n) {
  return Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/** Python's f"{x:g}" for the small numbers the desk shows (percentages). */
export function g(x) {
  return String(Number(Number(x).toPrecision(6)));
}

export function deepMerge(base, over) {
  const out = { ...base };
  for (const [k, v] of Object.entries(over || {})) {
    out[k] = v && typeof v === "object" && !Array.isArray(v) && base?.[k] && typeof base[k] === "object" && !Array.isArray(base[k])
      ? deepMerge(base[k], v) : v;
  }
  return out;
}
