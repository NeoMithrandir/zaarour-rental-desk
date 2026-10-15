"""Rent increase calculator following the TAL's method for notices given from 1 January 2026.

Source: Règlement sur les critères de fixation de loyer (chapter T-15.01, r. 2), as amended by
décret 1455-2025 (Gazette officielle, 17 December 2025), articles 3, 3.1, 4 and 5. The yearly
percentages come from config/tal_rates.json.

The rent can rise by:
  1. the base percentage (3-year average of Québec CPI) applied to the current rent;
  2. municipal taxes: their % change, minus the base percentage (never below zero once the change is
     positive), applied to this year's monthly taxes;
  3. school taxes and 4. fire and liability insurance: same, applied to last period's monthly amount;
  5. 5% of capital expenditures made during the reference period (net of grants and indemnities;
     a loan-financed share can't add more than the year's repayment);
  6. the full-year cost of a new service, accessory or outbuilding.
Items 2 to 6 are building-wide and count for this unit by its share of the building's rents (or of
the benefiting units' rents when the work or service only concerns some units).

The TAL's percentages are guidance, not a cap: a landlord can ask more and the tenant can refuse.
The TAL's own online tool remains the reference; this one is for preparing the conversation.
"""
import json
from datetime import date, datetime

from .config import CONFIG_DIR

# Building-level inputs, all optional. (key, label, help)
INPUTS = [
    ("building_rents", "Total monthly rents of the building ($)", "All units, including this one (vacant units at their estimated rent). Leave blank for a single unit."),
    ("municipal_tax_ref", "Municipal taxes, reference year ($/year)", "Taxes payable during the reference period"),
    ("municipal_tax_next", "Municipal taxes, following year ($/year)", "The new tax bill"),
    ("school_tax_prev", "School taxes, previous year ($/year)", ""),
    ("school_tax_ref", "School taxes, reference year ($/year)", ""),
    ("insurance_prev", "Fire and liability insurance, previous year ($/year)", "Premiums for at most 12 months"),
    ("insurance_ref", "Fire and liability insurance, reference year ($/year)", ""),
    ("capex", "Major work done during the reference period ($)", "Net of any government grant or insurance indemnity"),
    ("capex_benefiting_rents", "Monthly rents of the units that benefit from the work ($)", "Only if the work concerns some units; leave blank when it serves the whole building"),
    ("capex_loan_repayment", "Yearly loan repayment for that work ($/year)", "Capital and interest, only if a loan financed it"),
    ("new_service_cost", "New service, accessory or outbuilding, cost for a full year ($)", "Excluding the capital cost"),
]
INPUT_KEYS = [k for k, _l, _h in INPUTS]
CURRENT_RENT = ("current_rent", "Current monthly rent ($)", "If it changed since the lease was entered (e.g. after a renewal)")


def _load():
    with open(CONFIG_DIR / "tal_rates.json", encoding="utf-8") as f:
        return json.load(f)


def official_tool_url():
    return _load().get("official_tool_url", "https://www.tal.gouv.qc.ca")


def rates_for(renewal_start):
    """The published percentages for a renewed lease starting on `renewal_start`.
    Returns (period, provisional). Provisional means the period's rates aren't published yet."""
    periods = _load()["periods"]
    d = renewal_start.isoformat()
    for p in periods:
        if p["renewal_start_from"] <= d <= p["renewal_start_to"]:
            return p, False
    latest = max(periods, key=lambda p: p["renewal_start_from"])
    return latest, d > latest["renewal_start_to"]


def _num(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace(" ", "").replace(" ", "").replace("$", "").replace(",", "."))


def _adjusted_pct(before, after, base_pct):
    """Art. 4: % change; when positive, minus the base percentage, floored at zero. A drop stays negative."""
    if not before or after is None:
        return None
    change = (after - before) / before * 100
    return max(change - base_pct, 0.0) if change > 0 else change


def compute(rent, renewal_start, inputs=None):
    """Monthly rent increase for one dwelling. `inputs` holds the optional building figures (INPUT_KEYS)."""
    inputs = {k: _num(v) for k, v in (inputs or {}).items() if k in INPUT_KEYS}
    rent = _num(rent)
    if not rent:
        return None
    if isinstance(renewal_start, str):
        renewal_start = datetime.strptime(renewal_start, "%Y-%m-%d").date()
    period, provisional = rates_for(renewal_start)
    base = float(period["base_pct"])
    building = inputs.get("building_rents") or rent
    share = min(rent / building, 1.0) if building else 1.0
    lines = [{"label": f"Base percentage ({base:g} %)", "amount": rent * base / 100}]

    def tax_line(label, pct, monthly_amount):
        if pct is None or not monthly_amount:
            return
        lines.append({"label": f"{label} ({pct:+.2f} % after the base)", "amount": pct / 100 * monthly_amount * share})

    tax_line("Municipal taxes", _adjusted_pct(inputs.get("municipal_tax_ref"), inputs.get("municipal_tax_next"), base),
             (inputs.get("municipal_tax_ref") or 0) / 12)
    tax_line("School taxes", _adjusted_pct(inputs.get("school_tax_prev"), inputs.get("school_tax_ref"), base),
             (inputs.get("school_tax_prev") or 0) / 12)
    tax_line("Insurance", _adjusted_pct(inputs.get("insurance_prev"), inputs.get("insurance_ref"), base),
             (inputs.get("insurance_prev") or 0) / 12)

    capex = inputs.get("capex")
    if capex:
        capex_pct = float(period.get("capex_pct", 5.0))
        benefiting = inputs.get("capex_benefiting_rents")
        capex_share = min(rent / benefiting, 1.0) if benefiting else share
        yearly = capex * capex_pct / 100 * capex_share
        loan = inputs.get("capex_loan_repayment")
        if loan:
            yearly = min(yearly, loan * capex_share)
        lines.append({"label": f"Major work ({capex_pct:g} % of ${capex:,.0f})", "amount": yearly / 12})

    service = inputs.get("new_service_cost")
    if service:
        lines.append({"label": "New service or accessory", "amount": service / 12 * share})

    for line in lines:
        line["amount"] = round(line["amount"], 2)
    increase = round(sum(l["amount"] for l in lines), 2)
    return {
        "rent": rent,
        "renewal_start": renewal_start.isoformat(),
        "base_pct": base,
        "period": f"{period['renewal_start_from']} to {period['renewal_start_to']}",
        "provisional": provisional,
        "share_pct": round(share * 100, 2),
        "lines": lines,
        "increase": increase,
        "increase_pct": round(increase / rent * 100, 2),
        # Rents are quoted in whole dollars on a notice; round the new rent to the nearest dollar.
        "new_rent": int(round(rent + increase)),
        "building_inputs_used": any(inputs.get(k) for k in INPUT_KEYS),
    }


def for_lease(r, today=None):
    """The calculation for a tracked lease's next renewal (current term end + 1 day), or None without a rent."""
    from . import leases  # avoid a cycle at import time
    from datetime import timedelta
    _start, end, _renewed = leases.current_term(r, today)
    inputs = r.get("tal_inputs") or {}
    return compute(inputs.get("current_rent") or r.get("rent"), end + timedelta(days=1), inputs)
