// Rent increase calculator following the TAL's method for notices given from 1 January 2026 (port of desk/tal.py:
// the method, sources and reasoning are written up there). The yearly percentages come from config/tal_rates.json.
import { addDays, g, iso, money0, parseDate } from "./util.js";

export const INPUTS = [
  ["building_rents", "Total monthly rents of the building ($)", "All units, including this one (vacant units at their estimated rent). Leave blank for a single unit."],
  ["municipal_tax_ref", "Municipal taxes, reference year ($/year)", "Taxes payable during the reference period"],
  ["municipal_tax_next", "Municipal taxes, following year ($/year)", "The new tax bill"],
  ["school_tax_prev", "School taxes, previous year ($/year)", ""],
  ["school_tax_ref", "School taxes, reference year ($/year)", ""],
  ["insurance_prev", "Fire and liability insurance, previous year ($/year)", "Premiums for at most 12 months"],
  ["insurance_ref", "Fire and liability insurance, reference year ($/year)", ""],
  ["capex", "Major work done during the reference period ($)", "Net of any government grant or insurance indemnity"],
  ["capex_benefiting_rents", "Monthly rents of the units that benefit from the work ($)", "Only if the work concerns some units; leave blank when it serves the whole building"],
  ["capex_loan_repayment", "Yearly loan repayment for that work ($/year)", "Capital and interest, only if a loan financed it"],
  ["new_service_cost", "New service, accessory or outbuilding, cost for a full year ($)", "Excluding the capital cost"],
];
export const INPUT_KEYS = INPUTS.map((i) => i[0]);
export const CURRENT_RENT = ["current_rent", "Current monthly rent ($)", "If it changed since the lease was entered (e.g. after a renewal)"];

export function officialToolUrl(rates) {
  return rates.official_tool_url || "https://www.tal.gouv.qc.ca";
}

/** [period, provisional] for a renewed lease starting on `renewalStart` ('YYYY-MM-DD'). */
export function ratesFor(rates, renewalStart) {
  const periods = rates.periods;
  for (const p of periods) if (p.renewal_start_from <= renewalStart && renewalStart <= p.renewal_start_to) return [p, false];
  const latest = periods.reduce((a, b) => (b.renewal_start_from > a.renewal_start_from ? b : a));
  return [latest, renewalStart > latest.renewal_start_to];
}

function num(v) {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return v;
  const n = parseFloat(String(v).replace(/[\s  $]/g, "").replace(",", "."));
  if (Number.isNaN(n)) throw new RangeError("not a number");
  return n;
}

function adjustedPct(before, after, base) {
  if (!before || after === null || after === undefined) return null;
  const change = ((after - before) / before) * 100;
  return change > 0 ? Math.max(change - base, 0) : change;
}

const round2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;
const fmtSigned = (x) => (x >= 0 ? "+" : "") + x.toFixed(2);

/** Monthly rent increase for one dwelling. inputs: the optional building figures (INPUT_KEYS). */
export function compute(rates, rent, renewalStart, rawInputs = {}) {
  const inputs = {};
  for (const [k, v] of Object.entries(rawInputs || {})) if (INPUT_KEYS.includes(k)) inputs[k] = num(v);
  rent = num(rent);
  if (!rent) return null;
  const [period, provisional] = ratesFor(rates, renewalStart);
  const base = parseFloat(period.base_pct);
  const building = inputs.building_rents || rent;
  const share = building ? Math.min(rent / building, 1) : 1;
  const lines = [{ label: `Base percentage (${g(base)} %)`, amount: (rent * base) / 100 }];
  const taxLine = (label, pct, monthly) => {
    if (pct === null || !monthly) return;
    lines.push({ label: `${label} (${fmtSigned(pct)} % after the base)`, amount: (pct / 100) * monthly * share });
  };
  taxLine("Municipal taxes", adjustedPct(inputs.municipal_tax_ref, inputs.municipal_tax_next, base), (inputs.municipal_tax_ref || 0) / 12);
  taxLine("School taxes", adjustedPct(inputs.school_tax_prev, inputs.school_tax_ref, base), (inputs.school_tax_prev || 0) / 12);
  taxLine("Insurance", adjustedPct(inputs.insurance_prev, inputs.insurance_ref, base), (inputs.insurance_prev || 0) / 12);
  const capex = inputs.capex;
  if (capex) {
    const capexPct = parseFloat(period.capex_pct ?? 5);
    const benefiting = inputs.capex_benefiting_rents;
    const capexShare = benefiting ? Math.min(rent / benefiting, 1) : share;
    let yearly = ((capex * capexPct) / 100) * capexShare;
    const loan = inputs.capex_loan_repayment;
    if (loan) yearly = Math.min(yearly, loan * capexShare);
    lines.push({ label: `Major work (${g(capexPct)} % of $${money0(capex)})`, amount: yearly / 12 });
  }
  if (inputs.new_service_cost) lines.push({ label: "New service or accessory", amount: (inputs.new_service_cost / 12) * share });
  for (const l of lines) l.amount = round2(l.amount);
  const increase = round2(lines.reduce((s, l) => s + l.amount, 0));
  return {
    rent, renewal_start: renewalStart, base_pct: base,
    period: `${period.renewal_start_from} to ${period.renewal_start_to}`, provisional,
    share_pct: round2(share * 100), lines, increase, increase_pct: round2((increase / rent) * 100),
    new_rent: Math.round(rent + increase),
    building_inputs_used: INPUT_KEYS.some((k) => inputs[k]),
  };
}

/** The calculation for a tracked lease's next renewal (current term end + 1 day), or null without a rent. */
export function forLease(rates, r, currentTerm) {
  const [, end] = currentTerm(r);
  const inputs = r.tal_inputs || {};
  try {
    return compute(rates, inputs.current_rent || r.rent, iso(addDays(end, 1)), inputs);
  } catch {
    return null;
  }
}

export { parseDate };
