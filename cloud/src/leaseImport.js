// Leases from outside the desk into the renewal tracker (port of desk/lease_import.py; the data shape is in
// docs/lease-import-spec.md). The Mac's `python3 -m desk import-leases` sends its inbox files here.
import { addressKey } from "./text.js";
import { FIELD_KEYS, KIND, followupDate } from "./leases.js";
import { iso, now, parseDate, syncStamp } from "./util.js";

const SPEC_VERSION = 1;
const SOURCES = ["nexone", "centris", "sales_tracker", "prospects", "lease_pdf", "email", "manual", "other"];
const END_BASIS = ["stated", "computed_from_term", "inferred"];
const TENANCY = ["active", "ended", "unknown"];
const REQUIRED = ["external_id", "source", "address", "city", "landlord_name", "lease_start", "lease_end"];
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const blank = (v) => v === null || v === undefined || v === "";
const py = (v) => (Array.isArray(v) ? "[" + v.map((x) => `'${x}'`).join(", ") + "]" : String(v));

function money(v) {
  if (blank(v)) return "";
  if (typeof v === "boolean") throw new Error("not a number");
  const n = typeof v === "number" ? v : Number(String(v).replace(/[\s  $]/g, "").replace(",", "."));
  if (!Number.isFinite(n) || String(v).trim() === "") throw new Error(`could not convert string to float: '${v}'`);
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

function bool(v) {
  if (blank(v)) return "";
  if (typeof v === "boolean") return v ? "on" : "";
  const s = String(v).trim().toLowerCase();
  if (["true", "yes", "oui", "1", "on"].includes(s)) return "on";
  if (["false", "no", "non", "0", "off"].includes(s)) return "";
  throw new Error("expected true or false");
}

/** Normalise one lease field to the form's format: [value, error, warning]. Shared with the photo scan. */
export function cleanField(key, v) {
  const kind = KIND[key];
  try {
    if (kind === "date") {
      v = blank(v) ? "" : String(v).trim();
      if (v) { try { parseDate(v); } catch { throw new Error("expected YYYY-MM-DD"); } }
    } else if (kind === "money") {
      v = money(v);
    } else if (kind === "check") {
      v = bool(v);
    } else if (kind.startsWith("select:")) {
      v = blank(v) ? "" : String(v).trim();
      const options = kind.slice(7).split("|");
      const match = options.find((o) => o.toLowerCase() === v.toLowerCase());
      if (v && !match) return ["", null, `'${v}' is not one of ${py(options)}; left blank`];
      v = match || "";
    } else if (kind === "email") {
      v = blank(v) ? "" : String(v).trim().toLowerCase();
      if (v && !EMAIL_RE.test(v)) return ["", null, `'${v}' doesn't look like an email; left blank`];
    } else {
      v = v === null || v === undefined ? "" : String(v).trim();
    }
  } catch (e) {
    return ["", e.message, null];
  }
  return [v, null, null];
}

/** Validate one record: [lease, meta, errors, warnings]. */
export function cleanRecord(rec, settings) {
  const errors = [], warnings = [];
  if ((rec.spec_version ?? SPEC_VERSION) !== SPEC_VERSION) errors.push(`spec_version must be ${SPEC_VERSION}`);
  for (const k of REQUIRED) if (!String(rec[k] ?? "").trim()) errors.push(`${k} is required`);
  if (rec.source && !SOURCES.includes(rec.source)) errors.push(`source must be one of ${py([...SOURCES].sort())}`);
  const out = {};
  for (const key of FIELD_KEYS) {
    const [value, err, warn] = cleanField(key, rec[key]);
    out[key] = value;
    if (err) errors.push(`${key}: ${err}`);
    if (warn) warnings.push(`${key}: ${warn}`);
  }
  const tenancy = String(rec.tenancy_status || "unknown").trim().toLowerCase();
  if (!TENANCY.includes(tenancy)) errors.push(`tenancy_status must be one of ${py([...TENANCY].sort())}`);
  out.tenancy_status = tenancy;
  if (out.lease_start && out.lease_end && out.lease_end <= out.lease_start) errors.push("lease_end must be after lease_start");
  const basis = rec.lease_end_basis || "stated";
  if (!END_BASIS.includes(basis)) errors.push(`lease_end_basis must be one of ${py([...END_BASIS].sort())}`);
  let confidence = rec.confidence ?? 1.0;
  confidence = typeof confidence === "boolean" ? Number(confidence) : Number(confidence);
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    errors.push("confidence must be a number from 0 to 1");
    confidence = 0;
  }
  const minConf = parseFloat(settings.import_min_confidence ?? 0.9);
  const meta = {
    external_id: String(rec.external_id || "").trim(), source: rec.source, source_ref: rec.source_ref || "",
    confidence, lease_end_basis: basis, evidence: rec.evidence || {},
    // Ali's rule: no tracker on wrong data. Low confidence or a guessed end date waits for a person.
    needs_review: confidence < minConf || basis === "inferred",
    imported_at: now(),
  };
  return [out, meta, errors, warnings];
}

async function existingByHand(db, lease) {
  const key = addressKey(lease.address);
  if (!key) return null;
  const { results } = await db.prepare("SELECT id, data FROM rentals WHERE COALESCE(origin,'form')='form'").all();
  for (const r of results) {
    const d = JSON.parse(r.data);
    if (d.lease_start === lease.lease_start && addressKey(d.address || "") === key) return r.id;
  }
  return null;
}

/** Add or update each record. Returns a report; rejected records carry their errors. */
export async function importRecords(db, records, settings, dryRun = false) {
  const report = { added: 0, updated: 0, skipped: [], rejected: [], warnings: [], needs_review: 0 };
  const writes = new Map(); // by external id: a record repeated in one import counts once, the last one wins
  for (let i = 0; i < records.length; i++) {
    const rec = records[i] || {};
    const [lease, meta, errors, warnings] = cleanRecord(rec, settings);
    const ref = meta.external_id || `record ${i + 1}`;
    if (errors.length) { report.rejected.push({ record: rec, errors }); continue; }
    report.warnings.push(...warnings.map((w) => `${ref}: ${w}`));
    if (writes.has(meta.external_id)) {
      report.skipped.push(`${ref}: appears twice in this import, the later record is used`);
      const prev = writes.get(meta.external_id);
      report[prev.kind] -= 1;
      report.needs_review -= prev.review;
      writes.delete(meta.external_id);
    }
    const row = await db.prepare("SELECT id, data FROM rentals WHERE external_id=?").bind(meta.external_id).first();
    if (row && (JSON.parse(row.data)._import || {}).verified_at) {
      report.skipped.push(`${ref}: already checked by a person, kept as is`);
      continue;
    }
    if (!row) {
      const hand = await existingByHand(db, lease);
      if (hand) { report.skipped.push(`${ref}: already entered with the New lease form (lease #${hand})`); continue; }
    }
    lease._import = meta;
    const fu = iso(followupDate(lease, settings));
    const data = JSON.stringify(lease);
    if (row) {
      writes.set(meta.external_id, { kind: "updated", review: meta.needs_review ? 1 : 0,
        stmt: db.prepare("UPDATE rentals SET data=?, followup_date=?, lease_end=?, address=?, sync_at=? WHERE id=?")
          .bind(data, fu, lease.lease_end, lease.address, syncStamp(), row.id) });
      report.updated += 1;
    } else {
      writes.set(meta.external_id, { kind: "added", review: meta.needs_review ? 1 : 0,
        stmt: db.prepare("INSERT INTO rentals (created_at, data, followup_date, lease_end, address, external_id, origin, sync_at) "
          + "VALUES (?,?,?,?,?,?, 'import', ?)").bind(now(), data, fu, lease.lease_end, lease.address, meta.external_id, syncStamp()) });
      report.added += 1;
    }
    report.needs_review += meta.needs_review ? 1 : 0;
  }
  if (!dryRun && writes.size) await db.batch([...writes.values()].map((w) => w.stmt));
  return report;
}

export async function markVerified(db, rid) {
  const r = await db.prepare("SELECT data FROM rentals WHERE id=?").bind(rid).first();
  if (!r) return false;
  const d = JSON.parse(r.data);
  d._import = { ...(d._import || {}), needs_review: false, verified_at: now() };
  await db.prepare("UPDATE rentals SET data=?, sync_at=? WHERE id=?").bind(JSON.stringify(d), syncStamp(), rid).run();
  return true;
}
