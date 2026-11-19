// Lease renewal tracker (plan items E.1 and E.2): port of desk/leases.py. One form per signed lease; saving builds
// the email to the office (opened in the person's mail app, nothing is sent here), a calendar file for the renewal
// call, the lease prep sheet, and keeps the record.
import * as tal from "./tal.js";
import { addDays, addMonths, formatMap, iso, now, parseDate, syncStamp, todayDate } from "./util.js";

// [key, label, kind, required, section]. kind: text, email, tel, date, money, number, textarea, check, select:a|b
export const FIELDS = [
  ["address", "Property address (civic number, street, unit)", "text", true, "Dwelling"],
  ["city", "City / borough", "text", true, "Dwelling"],
  ["postal_code", "Postal code", "text", false, "Dwelling"],
  ["unit_size", "Size (e.g. 4½)", "text", false, "Dwelling"],
  ["dwelling_type", "Type", "select:Apartment|Condo|House|Duplex/triplex unit|Room", false, "Dwelling"],
  ["furnished", "Furnished", "check", false, "Dwelling"],
  ["parking", "Parking (number and type)", "text", false, "Dwelling"],
  ["storage", "Locker / storage", "text", false, "Dwelling"],
  ["centris_no", "Centris number", "text", false, "Dwelling"],

  ["landlord_name", "Landlord name(s)", "text", true, "Landlord"],
  ["landlord_phone", "Landlord phone", "tel", false, "Landlord"],
  ["landlord_email", "Landlord email", "email", false, "Landlord"],
  ["landlord_address", "Landlord mailing address", "text", false, "Landlord"],

  ["tenant_names", "Tenant name(s)", "text", true, "Tenant"],
  ["tenant_phone", "Tenant phone", "tel", false, "Tenant"],
  ["tenant_email", "Tenant email", "email", false, "Tenant"],
  ["tenant_current_address", "Tenant current address", "text", false, "Tenant"],

  ["lease_start", "Lease start", "date", true, "Lease"],
  ["lease_end", "Lease end", "date", true, "Lease"],
  ["rent", "Monthly rent ($)", "money", true, "Lease"],
  ["services_cost", "Services / other charges per month ($)", "money", false, "Lease"],
  ["first_payment_date", "First payment date", "date", false, "Lease"],
  ["payment_method", "Payment method", "select:Cheque|E-transfer|Post-dated cheques|Other", false, "Lease"],
  ["heating", "Heating paid by", "select:Tenant|Landlord", false, "Lease"],
  ["hot_water", "Hot water paid by", "select:Tenant|Landlord", false, "Lease"],
  ["electricity", "Electricity paid by", "select:Tenant|Landlord", false, "Lease"],
  ["appliances", "Appliances included", "text", false, "Lease"],
  ["pets", "Pets", "select:Not allowed|Allowed|Allowed with conditions", false, "Lease"],
  ["smoking", "Smoking", "select:Not allowed|Allowed", false, "Lease"],
  ["lowest_rent_12m", "Lowest rent paid in the last 12 months ($), for TAL section G", "money", false, "Lease"],
  ["new_building", "Building ready for habitation less than 5 years ago (TAL section F)", "check", false, "Lease"],
  ["building_rules", "Building rules given to tenant", "check", false, "Lease"],

  ["commission", "Commission ($)", "money", false, "Deal"],
  ["double_ended", "We found the tenant too (double end)", "check", false, "Deal"],
  ["co_broker", "Other broker / agency", "text", false, "Deal"],
  ["photographer", "Photography to pay", "check", false, "Deal"],
  ["lead_source", "Where the landlord came from", "select:Kijiji|Facebook Marketplace|Ads|Repeat client|Referral|Centris|Other", false, "Deal"],
  ["notes_for_office", "Notes for the office (lease paperwork)", "textarea", false, "Deal"],
];
export const FIELD_KEYS = FIELDS.map((f) => f[0]);
export const LABELS = Object.fromEntries(FIELDS.map((f) => [f[0], f[1]]));
export const KIND = Object.fromEntries(FIELDS.map((f) => [f[0], f[2]]));

/** Whole months covered: 2026-07-01 to 2027-06-30 -> 12. */
export function leaseMonths(start, end) {
  return (end.getUTCFullYear() - start.getUTCFullYear()) * 12 + end.getUTCMonth() - start.getUTCMonth()
    + (end.getUTCDate() >= start.getUTCDate() - 1 ? 1 : 0);
}

/** Notice-of-modification window for the landlord (Civil Code of Québec, art. 1942): [opens, closes]. */
export function renewalWindow(start, end) {
  if (leaseMonths(start, end) >= 12) return [addMonths(end, -6), addMonths(end, -3)];
  return [addMonths(end, -2), addMonths(end, -1)];
}

export function validate(form) {
  const errors = FIELDS.filter(([k, , , req]) => req && !String(form[k] ?? "").trim()).map(([, label]) => `${label} is required`);
  if (!errors.length) {
    try {
      if (parseDate(form.lease_end) <= parseDate(form.lease_start)) errors.push("Lease end must be after lease start");
    } catch {
      errors.push("Lease dates must be valid dates");
    }
  }
  return errors;
}

export function followupDate(form, settings, today = todayDate()) {
  const months = parseInt(settings.followup_months_before_end ?? 6, 10);
  const start = parseDate(form.lease_start), end = parseDate(form.lease_end);
  let target = leaseMonths(start, end) >= 12 ? addMonths(end, -months) : renewalWindow(start, end)[0];
  if (target < today) target = addDays(today, 7); // entered late: call next week rather than in the past
  return target;
}

export async function save(db, form, settings, by = "") {
  const clean = Object.fromEntries(FIELD_KEYS.map((k) => [k, String(form[k] ?? "").trim()]));
  let scan = null;
  try { scan = JSON.parse(form.scan_meta || "null"); } catch { scan = null; }
  if (scan) clean._scan = scan;
  const fu = iso(followupDate(clean, settings));
  const r = await db.prepare("INSERT INTO rentals (created_at, data, followup_date, lease_end, address, created_by, sync_at) VALUES (?,?,?,?,?,?,?)")
    .bind(now(), JSON.stringify(clean), fu, clean.lease_end, clean.address, by, syncStamp()).run();
  return r.meta.last_row_id;
}

function upgrade(d) {
  if ("notes_for_tanya" in d) { if (!("notes_for_office" in d)) d.notes_for_office = d.notes_for_tanya; delete d.notes_for_tanya; }
  return d;
}

function rowToLease(r) {
  const d = upgrade(JSON.parse(r.data));
  return Object.assign(d, { id: r.id, created_at: r.created_at, followup_date: r.followup_date });
}

export async function get(db, rid) {
  const r = await db.prepare("SELECT * FROM rentals WHERE id=?").bind(rid).first();
  return r ? rowToLease(r) : null;
}

export async function exportRows(db) {
  const { results } = await db.prepare("SELECT * FROM rentals ORDER BY lease_end").all();
  return results.map(rowToLease);
}

const yes = (v) => ([true, "on", "1", "yes", "Yes"].includes(v) ? "Yes" : "No");

export function officeEmail(r, settings) {
  const agent = settings.agent || {};
  const [opens, closes] = renewalWindow(parseDate(r.lease_start), parseDate(r.lease_end));
  const subject = `Nouvelle location / New rental: ${r.address}, ${r.city} (${r.lease_start})`;
  const lines = [
    "Bonjour,", "",
    "Voici une nouvelle location à préparer. / Here is a new rental to prepare.", "",
    `Property: ${r.address}, ${r.city} ${r.postal_code || ""}`.trimEnd(),
    `Size / type: ${r.unit_size || ""} ${r.dwelling_type || ""}`.trimEnd(),
    `Centris #: ${r.centris_no || "n/a"}`, "",
    `Landlord: ${r.landlord_name} | ${r.landlord_phone || ""} | ${r.landlord_email || ""}`,
    `Tenant(s): ${r.tenant_names} | ${r.tenant_phone || ""} | ${r.tenant_email || ""}`, "",
    `Lease: ${r.lease_start} to ${r.lease_end}`,
    `Rent: $${r.rent}/month` + (r.services_cost ? ` + $${r.services_cost} services` : ""),
    `Heating: ${r.heating || "?"} | Hot water: ${r.hot_water || "?"} | Electricity: ${r.electricity || "?"}`,
    `Appliances: ${r.appliances || "none listed"} | Parking: ${r.parking || "none"} | Furnished: ${yes(r.furnished)}`, "",
    `Commission: $${r.commission || "?"} | Double end: ${yes(r.double_ended)} | Co-broker: ${r.co_broker || "none"}`,
    `Photography to pay: ${yes(r.photographer)} | Lead source: ${r.lead_source || "?"}`,
  ];
  if (r.notes_for_office) lines.push("", "Notes:", r.notes_for_office);
  lines.push("", `Renewal call booked for ${r.followup_date} (notice window ${iso(opens)} to ${iso(closes)}).`, "", "Merci,", agent.name || "");
  return { to: settings.office_email || settings.tanya_email || "", subject, body: lines.join("\n") };
}

const icsEscape = (s) => String(s).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\n/g, "\\n");

export function followupIcs(r) {
  const fu = parseDate(r.followup_date);
  const [opens, closes] = renewalWindow(parseDate(r.lease_start), parseDate(r.lease_end));
  const desc = `Lease ends ${r.lease_end}. Notice-of-increase window: ${iso(opens)} to ${iso(closes)}.\n`
    + `Landlord: ${r.landlord_name} ${r.landlord_phone || ""} ${r.landlord_email || ""}\n`
    + `Tenant(s): ${r.tenant_names}\nRent: $${r.rent}\n`
    + "Ask: renew or re-list? Planned increase? Offer to prepare the notice.";
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const d8 = (d) => iso(d).replace(/-/g, "");
  return [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zaarour//Rentals desk//FR", "CALSCALE:GREGORIAN",
    "BEGIN:VEVENT", `UID:rental-${r.id}-followup@zaarour.ca`, `DTSTAMP:${stamp}`,
    `DTSTART;VALUE=DATE:${d8(fu)}`, `DTEND;VALUE=DATE:${d8(addDays(fu, 1))}`,
    "SUMMARY:" + icsEscape(`Renewal call: ${r.landlord_name}, ${r.address}`),
    "DESCRIPTION:" + icsEscape(desc),
    "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:PT9H", "DESCRIPTION:Renewal call", "END:VALARM",
    "END:VEVENT", "END:VCALENDAR", ""].join("\r\n");
}

const LEASE_SECTIONS = [
  ["A. Parties (lessor / lessee)", ["landlord_name", "landlord_address", "landlord_phone", "landlord_email",
    "tenant_names", "tenant_current_address", "tenant_phone", "tenant_email"]],
  ["B. Description of the dwelling", ["address", "city", "postal_code", "unit_size", "dwelling_type", "furnished", "parking", "storage", "appliances"]],
  ["C. Term of the lease", ["lease_start", "lease_end"]],
  ["D. Rent", ["rent", "services_cost", "first_payment_date", "payment_method"]],
  ["E. Services and conditions", ["heating", "hot_water", "electricity", "pets", "smoking", "building_rules"]],
  ["F. Restrictions on the right to have the rent fixed", ["new_building"]],
  ["G. Notice to a new lessee (lowest rent in the last 12 months)", ["lowest_rent_12m"]],
];

/** Rows for the printable lease prep sheet: [[section, [[label, value]]]]. */
export function leaseSheet(r) {
  const out = LEASE_SECTIONS.map(([title, keys]) => [title, keys.map((k) => [LABELS[k], KIND[k] === "check" ? yes(r[k]) : r[k] || ""])]);
  const start = parseDate(r.lease_start), end = parseDate(r.lease_end);
  const [opens, closes] = renewalWindow(start, end);
  out.push(["For the file", [["Lease length (months)", String(leaseMonths(start, end))],
    ["Landlord notice-of-change window", `${iso(opens)} to ${iso(closes)}`], ["Stefan's renewal call", r.followup_date]]]);
  return out;
}

// -- renewals --------------------------------------------------------------------------
const DUE_SOON_DAYS = 14;

/** The lease term running today (a Québec lease renews itself, art. 1941): [start, end, renewed]. */
export function currentTerm(r, today = todayDate()) {
  let start = parseDate(r.lease_start), end = parseDate(r.lease_end);
  if (end >= today || r.tenancy_status === "ended") return [start, end, false];
  const months = Math.max(leaseMonths(start, end), 1);
  while (end < today) {
    start = addDays(end, 1);
    end = addDays(addMonths(start, months), -1);
  }
  return [start, end, true];
}

export function nextCall(r, settings, today = todayDate()) {
  const [start, end, renewed] = currentTerm(r, today);
  if (!renewed) return parseDate(r.followup_date);
  return followupDate({ lease_start: iso(start), lease_end: iso(end) }, settings, today);
}

export function renewalStatus(r, settings, today = todayDate()) {
  if (r.tenancy_status === "ended" && parseDate(r.lease_end) < today) return "ended";
  if ((r._import || {}).needs_review) return "check";
  if (nextCall(r, settings, today) <= addDays(today, DUE_SOON_DAYS)) return "due";
  return "upcoming";
}

const moneyLang = (n, lang = "fr") => Math.round(n).toLocaleString("en-US").replace(/,/g, lang === "fr" ? " " : ",");

export function talParagraph(rates, r, lang = "fr") {
  const c = tal.forLease(rates, r, currentTerm);
  if (!c) return "";
  const pct = c.increase_pct.toFixed(1).replace(".", lang === "fr" ? "," : ".");
  const base = String(c.base_pct).replace(".", lang === "fr" ? "," : ".");
  if (lang === "fr") {
    const what = c.building_inputs_used ? "le taux de base, plus les taxes, assurances et travaux de l'immeuble" : `le taux de base de ${base} %`;
    const prov = c.provisional ? " Le taux de l'an prochain sera publié en janvier : ce calcul utilise celui de cette année." : "";
    return `\nSelon la méthode du Tribunal administratif du logement (${what}), votre loyer de ${moneyLang(c.rent)} $ `
      + `pourrait passer à ${moneyLang(c.new_rent)} $ par mois (+${pct} %).${prov}\n`;
  }
  const what = c.building_inputs_used ? "the base rate plus the building's taxes, insurance and work" : `the ${base}% base rate`;
  const prov = c.provisional ? " Next year's rate comes out in January; this uses this year's." : "";
  return `\nUsing the Tribunal administratif du logement's method (${what}), your rent of $${moneyLang(c.rent, "en")} `
    + `could go to $${moneyLang(c.new_rent, "en")} a month (+${pct}%).${prov}\n`;
}

export async function saveTalInputs(db, rid, inputs) {
  const r = await db.prepare("SELECT data FROM rentals WHERE id=?").bind(rid).first();
  if (!r) return false;
  const d = JSON.parse(r.data);
  d.tal_inputs = Object.fromEntries(Object.entries(inputs).filter(([, v]) => String(v ?? "").trim()).map(([k, v]) => [k, String(v).trim()]));
  await db.prepare("UPDATE rentals SET data=?, sync_at=? WHERE id=?").bind(JSON.stringify(d), syncStamp(), rid).run();
  return true;
}

/** The message to the landlord at the renewal call date (FR or EN). template: the renewal_landlord_<lang> text. */
export function renewalMessage(rates, r, settings, lang, template) {
  const agent = settings.agent || {};
  const [start, end] = currentTerm(r);
  const [opens, closes] = renewalWindow(start, end);
  const first = lang === "en" ? String(r.landlord_name || "").split(" ")[0].replace(/^,+|,+$/g, "").trim() : r.landlord_name || "";
  const body = formatMap(String(template).trim(), {
    tal_paragraph: talParagraph(rates, r, lang),
    landlord_name: first || (lang === "fr" ? "Madame, Monsieur" : "there"),
    address: r.address + (r.city ? `, ${r.city}` : ""),
    lease_end: iso(end), window_opens: iso(opens), window_closes: iso(closes),
    agent_name: agent.name || "", agent_phone: agent.phone || "",
  });
  const subject = lang === "fr" ? `Renouvellement du bail : ${r.address}` : `Lease renewal: ${r.address}`;
  return { to: r.landlord_email || "", subject, body };
}
