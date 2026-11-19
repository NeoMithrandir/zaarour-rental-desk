// The automatic checks Stefan does by hand today (port of desk/checks.py, used for ads added on the desk and for
// Recheck; the Mac runs the same checks in Python on what its scanners read).
//
// A flag is {code, level, text}: level "hide" (tenant ad, out of area), "broker", "check" (needs a look), "info".
import { addressKey, cityOf, fold, postalFsa } from "./text.js";

const TENANT_PATTERNS = [
  "cession de bail", "transfert de bail", "transfer(t)? de bail", "lease transfer", "lease takeover",
  "reprise de bail", "sous[- ]?locat", "sous[- ]?lou", "sublet", "sub-let", "sublease",
  "chambre a louer", "room for rent", "room available", "colocat", "roommate", "coloc\\b",
  "\\bcherche\\b.{0,30}\\b(logement|appartement|appart|4 ?1/2|3 ?1/2)", "looking for (an? )?(apartment|room|place)",
  "residence etudiante", "student residence",
];
const TENANT_RE = new RegExp(TENANT_PATTERNS.join("|"));
const PM_PATTERNS = /gestion|management|property manager|gestionnaire|immeubles|appartements .{0,20}inc|\binc\b|ltee|\bltd\b/;
const SMALL_UNIT_RE = /\b1 ?1\/2\b|\b1\.5\b|1½|studio|bachelor|garconniere/;
const PHONE_RE = /(\d{3})\D{0,3}(\d{3})\D{0,2}(\d{4})/g;
const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.]+/g;
const GENERIC_CITIES = new Set(["montreal", "montreal, qc", "ville de montreal", "quebec", ""]);

const rad = (x) => (x * Math.PI) / 180;
function km(lat1, lng1, lat2, lng2) {
  const x = rad(lng2 - lng1) * Math.cos(rad((lat1 + lat2) / 2));
  const y = rad(lat2 - lat1);
  return 6371 * Math.hypot(x, y);
}
const digits = (p) => String(p || "").replace(/\D/g, "").slice(-10);
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function parseCsv(text) {
  const rows = [];
  const lines = String(text || "").replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim());
  const split = (line) => {
    const out = []; let cur = "", q = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
      else if (c === '"') q = true; else if (c === ",") { out.push(cur); cur = ""; } else cur += c;
    }
    out.push(cur);
    return out;
  };
  const head = split(lines.shift() || "");
  for (const l of lines) {
    const cells = split(l);
    rows.push(Object.fromEntries(head.map((h, i) => [h, cells[i] ?? ""])));
  }
  return rows;
}

export class Checker {
  /** areas: config/areas.json "areas"; known: {poster_id: label}; reference: {centris, found_brokers} from the Mac. */
  constructor(areas, settings, brokersCsv, knownPosters = {}, reference = {}) {
    this.areas = areas;
    this.minRent = settings.min_rent || 0;
    this.fsaToArea = {}; this.prefixToArea = {}; this.cityToArea = {}; this.catchAllCities = {};
    this.phase = {}; this.notes = {};
    for (const [name, a] of Object.entries(areas)) {
      this.phase[name] = parseInt(a.phase ?? 1, 10);
      if (a.note) this.notes[name] = a.note;
      for (const f of a.fsa || []) (f.length === 3 ? this.fsaToArea : this.prefixToArea)[f.toUpperCase()] = name;
      const cities = a.catch_all ? this.catchAllCities : this.cityToArea;
      for (const c of a.cities || []) cities[fold(c)] = name;
    }
    this.keywords = []; this.phones = new Set(); this.emails = new Set(); this.posterIds = {};
    for (const row of parseCsv(brokersCsv)) {
      const kind = (row.kind || "").trim(), value = (row.value || "").trim(), label = (row.label || "").trim();
      if (kind === "keyword") this.keywords.push([new RegExp("(?<![a-z0-9])" + escapeRe(fold(value)) + "(?![a-z0-9])"), label || value]);
      else if (kind === "phone") this.phones.add(digits(value));
      else if (kind === "email") this.emails.add(value.toLowerCase());
      else if (kind === "poster_id") this.posterIds[value] = label || "Known broker";
    }
    Object.assign(this.posterIds, knownPosters);
    this.centris = reference.centris || null;  // {address key: MLS} when the Mac has a Matrix export loaded
    this.foundBrokers = reference.found_brokers || {};
  }

  nearest(lat, lng, catchAll) {
    let best = "", bestD = null;
    for (const [name, a] of Object.entries(this.areas)) {
      if (Boolean(a.catch_all) !== catchAll || !a.center) continue;
      const d = km(lat, lng, a.center[0], a.center[1]);
      if (d <= parseFloat(a.radius_km || 0) && (bestD === null || d < bestD)) { best = name; bestD = d; }
    }
    return best;
  }

  areaOf(address, lat, lng) {
    const fsa = postalFsa(address);
    if (fsa) return this.fsaToArea[fsa] || this.prefixToArea[fsa.slice(0, 2)] || "";
    const city = cityOf(address);
    if (Object.hasOwn(this.cityToArea, city)) return this.cityToArea[city];
    for (const [c, name] of Object.entries(this.cityToArea)) if (c.length > 4 && city.includes(c)) return name;
    lat = lat == null || lat === "" ? NaN : parseFloat(lat);
    lng = lng == null || lng === "" ? NaN : parseFloat(lng);
    const hasPos = !Number.isNaN(lat) && !Number.isNaN(lng);
    const generic = GENERIC_CITIES.has(city);
    if (!generic) {
      if (Object.hasOwn(this.catchAllCities, city)) return this.catchAllCities[city];
      for (const [c, name] of Object.entries(this.catchAllCities)) if (c.length > 4 && !GENERIC_CITIES.has(c) && city.includes(c)) return name;
    }
    if (hasPos) { const near = this.nearest(lat, lng, false); if (near) return near; }
    if (Object.hasOwn(this.catchAllCities, city)) return this.catchAllCities[city];
    if (hasPos) return this.nearest(lat, lng, true);
    return "";
  }

  /** [area, flags] for a lead with title/snippet/address/price/poster_type/poster_id. */
  run(lead) {
    const flags = [];
    const text = fold([lead.check_title || lead.title || "", lead.check_text || lead.snippet || ""].join("\n"));
    if (TENANT_RE.test(text)) flags.push({ code: "tenant_ad", level: "hide", text: "Tenant ad (lease transfer, sublet or room)" });

    const area = this.areaOf(lead.address || "", lead.lat, lead.lng);
    if (!area && !String(lead.address || "").trim() && lead.lat == null) {
      flags.push({ code: "area_unknown", level: "check", text: "No address in the ad: check the area" });
    } else if (!area) {
      flags.push({ code: "out_of_area", level: "hide", text: "Outside every target area" });
    } else if ((this.phase[area] ?? 1) > 1) {
      flags.push({ code: "phase2", level: "info", text: "Phase 2 area" });
    }
    if (Object.hasOwn(this.notes, area)) flags.push({ code: "area_note", level: "info", text: this.notes[area] });

    const hits = this.keywords.filter(([rx]) => rx.test(text)).map(([, label]) => label);
    const phones = new Set([...(text + "\n" + (lead.phone || "")).matchAll(PHONE_RE)].map((m) => m[1] + m[2] + m[3]));
    const emails = new Set(((lead.snippet || "") + " " + (lead.check_text || "")).match(EMAIL_RE)?.map((e) => e.toLowerCase()) || []);
    if ([...phones].some((p) => this.phones.has(p))) hits.push("Phone matches a known broker");
    if ([...emails].some((e) => this.emails.has(e))) hits.push("Email matches a known broker");
    for (const n of String(lead.phone || "").split(", ")) {
      if (n && Object.hasOwn(this.foundBrokers, digits(n))) hits.push(`Landlord's number found online with a broker (${this.foundBrokers[digits(n)]})`);
    }
    hits.push(...(lead.number_hits || []));
    const pid = lead.poster_id;
    if (pid && Object.hasOwn(this.posterIds, pid)) hits.push(`Poster marked before: ${this.posterIds[pid]}`);
    if (hits.length) {
      const label = hits.includes("Our own listing") ? "Our own listing" : "Likely broker";
      flags.push({ code: "broker", level: "broker", text: `${label}: ` + [...new Set(hits)].join(", ") });
    }

    if (lead.poster_type === "professional" && !hits.length) {
      flags.push({ code: "property_manager", level: "info", text: "Professional poster, probably a property manager (keep: one PM can mean 80 units)" });
    } else if (PM_PATTERNS.test(text) && !hits.length) {
      flags.push({ code: "property_manager", level: "info", text: "Mentions management company" });
    }

    const price = lead.price;
    if (SMALL_UNIT_RE.test(text) || (price && this.minRent && price < this.minRent)) {
      flags.push({ code: "small", level: "info", text: "Small unit or low rent" });
    }

    const key = addressKey(lead.address || "");
    if (this.centris === null) {
      flags.push(key
        ? { code: "centris_unchecked", level: "info", text: "Not checked against Centris yet (no Matrix export loaded)" }
        : { code: "centris_unchecked", level: "check", text: "No civic number in the ad: check Centris by hand" });
    } else if (key && Object.hasOwn(this.centris, key)) {
      flags.push({ code: "on_centris", level: "broker", text: `Address is already listed on Centris (${this.centris[key]})` });
    } else if (!key) {
      flags.push({ code: "centris_unchecked", level: "check", text: "No civic number in the ad: check Centris by hand" });
    }
    return [area, flags];
  }
}

/** Which tab of the review screen a lead goes in. */
export function bucket(flags) {
  const levels = new Set(flags.map((f) => f.level));
  if (levels.has("hide")) return "hidden";
  if (levels.has("broker")) return "broker";
  return "review";
}
