"""Lease renewal tracker (plan items E.1 and E.2, and the data engine 3 needs).

One form per signed lease replaces the hand-written email to the office. Saving it:
  1. builds the email to the office (opened in Stefan's mail app; nothing is sent automatically),
  2. builds a calendar file for Stefan's renewal call, 6 months before the lease ends,
  3. builds a lease prep sheet that follows the sections of the mandatory TAL lease form,
  4. keeps the record, which starts the clean lease end-date list engine 3 needs.
"""
import json
from datetime import date, datetime, timedelta

from . import db
from .config import TEMPLATE_DIR, load_settings

# (key, label, kind, required, section). kind: text, email, tel, date, money, number, textarea, check, select:a|b
FIELDS = [
    ("address", "Property address (civic number, street, unit)", "text", True, "Dwelling"),
    ("city", "City / borough", "text", True, "Dwelling"),
    ("postal_code", "Postal code", "text", False, "Dwelling"),
    ("unit_size", "Size (e.g. 4½)", "text", False, "Dwelling"),
    ("dwelling_type", "Type", "select:Apartment|Condo|House|Duplex/triplex unit|Room", False, "Dwelling"),
    ("furnished", "Furnished", "check", False, "Dwelling"),
    ("parking", "Parking (number and type)", "text", False, "Dwelling"),
    ("storage", "Locker / storage", "text", False, "Dwelling"),
    ("centris_no", "Centris number", "text", False, "Dwelling"),

    ("landlord_name", "Landlord name(s)", "text", True, "Landlord"),
    ("landlord_phone", "Landlord phone", "tel", False, "Landlord"),
    ("landlord_email", "Landlord email", "email", False, "Landlord"),
    ("landlord_address", "Landlord mailing address", "text", False, "Landlord"),

    ("tenant_names", "Tenant name(s)", "text", True, "Tenant"),
    ("tenant_phone", "Tenant phone", "tel", False, "Tenant"),
    ("tenant_email", "Tenant email", "email", False, "Tenant"),
    ("tenant_current_address", "Tenant current address", "text", False, "Tenant"),

    ("lease_start", "Lease start", "date", True, "Lease"),
    ("lease_end", "Lease end", "date", True, "Lease"),
    ("rent", "Monthly rent ($)", "money", True, "Lease"),
    ("services_cost", "Services / other charges per month ($)", "money", False, "Lease"),
    ("first_payment_date", "First payment date", "date", False, "Lease"),
    ("payment_method", "Payment method", "select:Cheque|E-transfer|Post-dated cheques|Other", False, "Lease"),
    ("heating", "Heating paid by", "select:Tenant|Landlord", False, "Lease"),
    ("hot_water", "Hot water paid by", "select:Tenant|Landlord", False, "Lease"),
    ("electricity", "Electricity paid by", "select:Tenant|Landlord", False, "Lease"),
    ("appliances", "Appliances included", "text", False, "Lease"),
    ("pets", "Pets", "select:Not allowed|Allowed|Allowed with conditions", False, "Lease"),
    ("smoking", "Smoking", "select:Not allowed|Allowed", False, "Lease"),
    ("lowest_rent_12m", "Lowest rent paid in the last 12 months ($), for TAL section G", "money", False, "Lease"),
    ("new_building", "Building ready for habitation less than 5 years ago (TAL section F)", "check", False, "Lease"),
    ("building_rules", "Building rules given to tenant", "check", False, "Lease"),

    ("commission", "Commission ($)", "money", False, "Deal"),
    ("double_ended", "We found the tenant too (double end)", "check", False, "Deal"),
    ("co_broker", "Other broker / agency", "text", False, "Deal"),
    ("photographer", "Photography to pay", "check", False, "Deal"),
    ("lead_source", "Where the landlord came from", "select:Kijiji|Facebook Marketplace|Ads|Repeat client|Referral|Centris|Other", False, "Deal"),
    ("notes_for_office", "Notes for the office (lease paperwork)", "textarea", False, "Deal"),
]
FIELD_KEYS = [f[0] for f in FIELDS]
REQUIRED = [f[0] for f in FIELDS if f[3]]


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _add_months(d, months):
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def lease_months(start, end):
    """Whole months covered: 2026-07-01 to 2027-06-30 -> 12."""
    return (end.year - start.year) * 12 + end.month - start.month + (1 if end.day >= start.day - 1 else 0)


def renewal_window(start, end):
    """Notice-of-modification window for the landlord (Civil Code of Québec, art. 1942).

    Lease of 12 months or more: 3 to 6 months before the end. Shorter lease: 1 to 2 months before.
    Returns (window_opens, window_closes).
    """
    if lease_months(start, end) >= 12:
        return _add_months(end, -6), _add_months(end, -3)
    return _add_months(end, -2), _add_months(end, -1)


def validate(form):
    errors = [f"{label} is required" for key, label, _k, req, _s in FIELDS if req and not str(form.get(key, "")).strip()]
    if not errors:
        try:
            if _d(form["lease_end"]) <= _d(form["lease_start"]):
                errors.append("Lease end must be after lease start")
        except ValueError:
            errors.append("Lease dates must be valid dates")
    return errors


def followup_date(form, settings=None, today=None):
    settings = settings or load_settings()
    months = int(settings.get("followup_months_before_end", 6))
    start, end = _d(form["lease_start"]), _d(form["lease_end"])
    if lease_months(start, end) >= 12:
        target = _add_months(end, -months)
    else:  # short lease: call when the (shorter) notice window opens
        target = renewal_window(start, end)[0]
    today = today or date.today()
    if target < today:  # short lease or entered late: call next week rather than in the past
        target = today + timedelta(days=7)
    return target


def save(conn, form, settings=None):
    settings = settings or load_settings()
    clean = {k: str(form.get(k) or "").strip() for k in FIELD_KEYS}
    fu = followup_date(clean, settings)
    cur = conn.execute("INSERT INTO rentals (created_at, data, followup_date, lease_end, address) VALUES (?,?,?,?,?)",
                       (db.now(), json.dumps(clean, ensure_ascii=False), fu.isoformat(), clean["lease_end"], clean["address"]))
    conn.commit()
    return cur.lastrowid


def _upgrade(d):
    """Leases saved before the rename kept their notes under the old key."""
    if "notes_for_tanya" in d:
        d.setdefault("notes_for_office", d.pop("notes_for_tanya"))
    return d


def get(conn, rid):
    r = conn.execute("SELECT * FROM rentals WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    d = _upgrade(json.loads(r["data"]))
    d.update({"id": r["id"], "created_at": r["created_at"], "followup_date": r["followup_date"]})
    return d


def _yes(v):
    return "Yes" if v in (True, "on", "1", "yes", "Yes") else "No"


def office_email(r, settings=None):
    settings = settings or load_settings()
    agent = settings.get("agent", {})
    start, end = _d(r["lease_start"]), _d(r["lease_end"])
    opens, closes = renewal_window(start, end)
    subject = f"Nouvelle location / New rental: {r['address']}, {r['city']} ({r['lease_start']})"
    lines = [
        "Bonjour,",
        "",
        "Voici une nouvelle location à préparer. / Here is a new rental to prepare.",
        "",
        f"Property: {r['address']}, {r['city']} {r.get('postal_code', '')}".rstrip(),
        f"Size / type: {r.get('unit_size', '')} {r.get('dwelling_type', '')}".rstrip(),
        f"Centris #: {r.get('centris_no') or 'n/a'}",
        "",
        f"Landlord: {r['landlord_name']} | {r.get('landlord_phone', '')} | {r.get('landlord_email', '')}",
        f"Tenant(s): {r['tenant_names']} | {r.get('tenant_phone', '')} | {r.get('tenant_email', '')}",
        "",
        f"Lease: {r['lease_start']} to {r['lease_end']}",
        f"Rent: ${r['rent']}/month" + (f" + ${r['services_cost']} services" if r.get("services_cost") else ""),
        f"Heating: {r.get('heating') or '?'} | Hot water: {r.get('hot_water') or '?'} | Electricity: {r.get('electricity') or '?'}",
        f"Appliances: {r.get('appliances') or 'none listed'} | Parking: {r.get('parking') or 'none'} | Furnished: {_yes(r.get('furnished'))}",
        "",
        f"Commission: ${r.get('commission') or '?'} | Double end: {_yes(r.get('double_ended'))} | Co-broker: {r.get('co_broker') or 'none'}",
        f"Photography to pay: {_yes(r.get('photographer'))} | Lead source: {r.get('lead_source') or '?'}",
    ]
    if r.get("notes_for_office"):
        lines += ["", "Notes:", r["notes_for_office"]]
    lines += ["", f"Renewal call booked for {r['followup_date']} (notice window {opens} to {closes}).",
              "", "Merci,", agent.get("name", "")]
    return {"to": settings.get("office_email") or settings.get("tanya_email", ""), "subject": subject, "body": "\n".join(lines)}


def _ics_escape(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def followup_ics(r):
    """All-day calendar event on the follow-up date, with a reminder the morning of."""
    fu = _d(r["followup_date"])
    start, end = _d(r["lease_start"]), _d(r["lease_end"])
    opens, closes = renewal_window(start, end)
    desc = (f"Lease ends {r['lease_end']}. Notice-of-increase window: {opens} to {closes}.\n"
            f"Landlord: {r['landlord_name']} {r.get('landlord_phone', '')} {r.get('landlord_email', '')}\n"
            f"Tenant(s): {r['tenant_names']}\nRent: ${r['rent']}\n"
            "Ask: renew or re-list? Planned increase? Offer to prepare the notice.")
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zaarour//Rentals desk//FR", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:rental-{r['id']}-followup@zaarour.ca",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{fu.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{(fu + timedelta(days=1)).strftime('%Y%m%d')}",
        "SUMMARY:" + _ics_escape(f"Renewal call: {r['landlord_name']}, {r['address']}"),
        "DESCRIPTION:" + _ics_escape(desc),
        "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:PT9H", "DESCRIPTION:Renewal call", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", ""])


# Sections of the mandatory TAL lease form ("Bail de logement"), so the office can copy field by field.
LEASE_SECTIONS = [
    ("A. Parties (lessor / lessee)", ["landlord_name", "landlord_address", "landlord_phone", "landlord_email",
                                       "tenant_names", "tenant_current_address", "tenant_phone", "tenant_email"]),
    ("B. Description of the dwelling", ["address", "city", "postal_code", "unit_size", "dwelling_type",
                                        "furnished", "parking", "storage", "appliances"]),
    ("C. Term of the lease", ["lease_start", "lease_end"]),
    ("D. Rent", ["rent", "services_cost", "first_payment_date", "payment_method"]),
    ("E. Services and conditions", ["heating", "hot_water", "electricity", "pets", "smoking", "building_rules"]),
    ("F. Restrictions on the right to have the rent fixed", ["new_building"]),
    ("G. Notice to a new lessee (lowest rent in the last 12 months)", ["lowest_rent_12m"]),
]
LABELS = {f[0]: f[1] for f in FIELDS}


def lease_sheet(r):
    """Rows for the printable lease prep sheet: [(section, [(label, value)])]."""
    out = []
    for title, keys in LEASE_SECTIONS:
        rows = []
        for k in keys:
            v = r.get(k, "")
            kind = next(f[2] for f in FIELDS if f[0] == k)
            if kind == "check":
                v = _yes(v)
            rows.append((LABELS[k], v or ""))
        out.append((title, rows))
    start, end = _d(r["lease_start"]), _d(r["lease_end"])
    opens, closes = renewal_window(start, end)
    out.append(("For the file", [("Lease length (months)", str(lease_months(start, end))),
                                 ("Landlord notice-of-change window", f"{opens} to {closes}"),
                                 ("Stefan's renewal call", r["followup_date"])]))
    return out


def export_rows(conn):
    """All rentals entered so far, flattened (the start of engine 3's lease end-date list)."""
    rows = []
    for r in conn.execute("SELECT * FROM rentals ORDER BY lease_end"):
        d = _upgrade(json.loads(r["data"]))
        d.update({"id": r["id"], "created_at": r["created_at"], "followup_date": r["followup_date"]})
        rows.append(d)
    return rows


# -- renewals --------------------------------------------------------------------------
DUE_SOON_DAYS = 14


def current_term(r, today=None):
    """The lease term running today. A Québec lease renews itself for the same length unless someone
    ends it (Civil Code art. 1941), so a past end date rolls forward unless the tenancy is marked ended.
    Returns (start, end, renewed)."""
    today = today or date.today()
    start, end = _d(r["lease_start"]), _d(r["lease_end"])
    if end >= today or r.get("tenancy_status") == "ended":
        return start, end, False
    months = max(lease_months(start, end), 1)
    while end < today:
        start = end + timedelta(days=1)
        end = _add_months(start, months) - timedelta(days=1)
    return start, end, True


def next_call(r, settings=None, today=None):
    """Renewal call date for the current term (the saved date until the lease renews itself)."""
    start, end, renewed = current_term(r, today)
    if not renewed:
        return _d(r["followup_date"])
    return followup_date({"lease_start": start.isoformat(), "lease_end": end.isoformat()}, settings, today)


def renewal_status(r, today=None, settings=None):
    """'ended' when the tenancy is marked ended and its end date passed; 'check' for imported data a person
    hasn't confirmed yet; 'due' when the renewal call is overdue or within two weeks; 'upcoming' before that."""
    today = today or date.today()
    if r.get("tenancy_status") == "ended" and _d(r["lease_end"]) < today:
        return "ended"
    if (r.get("_import") or {}).get("needs_review"):
        return "check"
    if next_call(r, settings, today) <= today + timedelta(days=DUE_SOON_DAYS):
        return "due"
    return "upcoming"


def renewal_message(r, settings=None, lang="fr"):
    """The message Stefan sends the landlord at the renewal call date (FR or EN)."""
    settings = settings or load_settings()
    agent = settings.get("agent", {})
    start, end, _renewed = current_term(r)
    opens, closes = renewal_window(start, end)
    with open(TEMPLATE_DIR / f"renewal_landlord_{lang}.txt", encoding="utf-8") as f:
        body = f.read().strip()
    first = (r.get("landlord_name") or "").split(" ")[0].strip(",") if lang == "en" else (r.get("landlord_name") or "")
    body = body.format_map({
        "landlord_name": first or ("Madame, Monsieur" if lang == "fr" else "there"),
        "address": r["address"] + (f", {r['city']}" if r.get("city") else ""),
        "lease_end": end.isoformat(), "window_opens": opens.isoformat(), "window_closes": closes.isoformat(),
        "agent_name": agent.get("name", ""), "agent_phone": agent.get("phone", ""),
    })
    subject = (f"Renouvellement du bail : {r['address']}" if lang == "fr" else f"Lease renewal: {r['address']}")
    return {"to": r.get("landlord_email", ""), "subject": subject, "body": body}
