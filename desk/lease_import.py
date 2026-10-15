"""Import leases from outside the desk (the RAG process over Nexone, Centris, the sales tracker...)
into the lease renewal tracker. The data shape is specified in docs/lease-import-spec.md.

Drop .jsonl (or .csv) files in data/import/inbox/. `python3 -m desk import-leases` (and every morning run)
validates each record, adds or updates it in the tracker, moves the file to data/import/done/ and writes
any rejected records, with the reason, to data/import/rejected/.
"""
import csv
import json
import re
from datetime import datetime
from pathlib import Path

from . import db, leases
from .config import DATA_DIR, load_settings
from .text import address_key

IMPORT_DIR = DATA_DIR / "import"
SPEC_VERSION = 1
SOURCES = {"nexone", "centris", "sales_tracker", "prospects", "lease_pdf", "email", "manual", "other"}
END_BASIS = {"stated", "computed_from_term", "inferred"}
TENANCY = {"active", "ended", "unknown"}
REQUIRED = ["external_id", "source", "address", "city", "landlord_name", "lease_start", "lease_end"]
KIND = {k: kind for k, _label, kind, _req, _sec in leases.FIELDS}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _money(v):
    if v in (None, ""):
        return ""
    if isinstance(v, bool):
        raise ValueError("not a number")
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        n = float(str(v).replace(" ", "").replace(" ", "").replace("$", "").replace(",", "."))
    return str(int(n)) if n == int(n) else f"{n:.2f}"


def _bool(v):
    if v in (None, ""):
        return ""
    if isinstance(v, bool):
        return "on" if v else ""
    s = str(v).strip().lower()
    if s in ("true", "yes", "oui", "1", "on"):
        return "on"
    if s in ("false", "no", "non", "0", "off"):
        return ""
    raise ValueError("expected true or false")


def clean_field(key, v):
    """Normalise one lease field to the form's format. Returns (value, error, warning).
    Shared by the import and the photo scan, so both arrive in the same shape."""
    kind = KIND[key]
    try:
        if kind == "date":
            v = "" if v in (None, "") else str(v).strip()
            if v and (not DATE_RE.match(v) or not datetime.strptime(v, "%Y-%m-%d")):
                raise ValueError("expected YYYY-MM-DD")
        elif kind == "money":
            v = _money(v)
        elif kind == "check":
            v = _bool(v)
        elif kind.startswith("select:"):
            v = "" if v in (None, "") else str(v).strip()
            options = kind[7:].split("|")
            match = next((o for o in options if o.lower() == v.lower()), None)
            if v and not match:
                return "", None, f"'{v}' is not one of {options}; left blank"
            v = match or ""
        elif kind == "email":
            v = "" if v in (None, "") else str(v).strip().lower()
            if v and not EMAIL_RE.match(v):
                return "", None, f"'{v}' doesn't look like an email; left blank"
        else:
            v = "" if v is None else str(v).strip()
    except (ValueError, TypeError) as e:
        return "", str(e), None
    return v, None, None


def clean_record(rec, settings=None):
    """Validate one record. Returns (lease_fields, meta, errors, warnings)."""
    settings = settings or load_settings()
    errors, warnings = [], []
    if rec.get("spec_version", SPEC_VERSION) != SPEC_VERSION:
        errors.append(f"spec_version must be {SPEC_VERSION}")
    for k in REQUIRED:
        if not str(rec.get(k) or "").strip():
            errors.append(f"{k} is required")
    if rec.get("source") and rec["source"] not in SOURCES:
        errors.append(f"source must be one of {sorted(SOURCES)}")

    out = {}
    for key in leases.FIELD_KEYS:
        out[key], err, warn = clean_field(key, rec.get(key))
        if err:
            errors.append(f"{key}: {err}")
        if warn:
            warnings.append(f"{key}: {warn}")

    tenancy = str(rec.get("tenancy_status") or "unknown").strip().lower()
    if tenancy not in TENANCY:
        errors.append(f"tenancy_status must be one of {sorted(TENANCY)}")
    out["tenancy_status"] = tenancy

    if out["lease_start"] and out["lease_end"] and out["lease_end"] <= out["lease_start"]:
        errors.append("lease_end must be after lease_start")

    basis = rec.get("lease_end_basis") or "stated"
    if basis not in END_BASIS:
        errors.append(f"lease_end_basis must be one of {sorted(END_BASIS)}")
    try:
        confidence = float(rec.get("confidence", 1.0))
        if not 0 <= confidence <= 1:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("confidence must be a number from 0 to 1")
        confidence = 0.0
    min_conf = float(settings.get("import_min_confidence", 0.9))
    meta = {
        "external_id": str(rec.get("external_id") or "").strip(),
        "source": rec.get("source"),
        "source_ref": rec.get("source_ref", ""),
        "confidence": confidence,
        "lease_end_basis": basis,
        "evidence": rec.get("evidence") or {},
        # Ali's rule: no tracker on wrong data. Low confidence or a guessed end date waits for a person.
        "needs_review": confidence < min_conf or basis == "inferred",
        "imported_at": db.now(),
    }
    return out, meta, errors, warnings


def _existing_by_hand(conn, lease):
    """A lease Stefan already entered with the form, for the same unit and start date."""
    key = address_key(lease["address"])
    if not key:
        return None
    for r in conn.execute("SELECT id, data FROM rentals WHERE COALESCE(origin,'form')='form'"):
        d = json.loads(r["data"])
        if d.get("lease_start") == lease["lease_start"] and address_key(d.get("address", "")) == key:
            return r["id"]
    return None


def import_records(conn, records, settings=None, dry_run=False):
    """Add or update each record. Returns a report dict; rejected records carry their errors."""
    settings = settings or load_settings()
    report = {"added": 0, "updated": 0, "skipped": [], "rejected": [], "warnings": [], "needs_review": 0}
    for i, rec in enumerate(records, 1):
        lease, meta, errors, warnings = clean_record(rec, settings)
        ref = meta["external_id"] or f"record {i}"
        if errors:
            report["rejected"].append({"record": rec, "errors": errors})
            continue
        report["warnings"] += [f"{ref}: {w}" for w in warnings]
        row = conn.execute("SELECT id, data FROM rentals WHERE external_id=?", (meta["external_id"],)).fetchone()
        if row and json.loads(row["data"]).get("_import", {}).get("verified_at"):
            report["skipped"].append(f"{ref}: already checked by a person, kept as is")
            continue
        if not row:
            hand = _existing_by_hand(conn, lease)
            if hand:
                report["skipped"].append(f"{ref}: already entered with the New lease form (lease #{hand})")
                continue
        lease["_import"] = meta
        fu = leases.followup_date(lease, settings).isoformat()
        data = json.dumps(lease, ensure_ascii=False)
        if row:
            conn.execute("UPDATE rentals SET data=?, followup_date=?, lease_end=?, address=? WHERE id=?",
                         (data, fu, lease["lease_end"], lease["address"], row["id"]))
            report["updated"] += 1
        else:
            conn.execute("INSERT INTO rentals (created_at, data, followup_date, lease_end, address, external_id, origin) "
                         "VALUES (?,?,?,?,?,?, 'import')", (db.now(), data, fu, lease["lease_end"], lease["address"], meta["external_id"]))
            report["added"] += 1
        report["needs_review"] += meta["needs_review"]
    conn.rollback() if dry_run else conn.commit()
    return report


def read_file(path):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    records = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except ValueError as e:
                    records.append({"_bad_line": n, "_error": str(e)})
    return records


def import_inbox(conn=None, paths=None, log=print, dry_run=False):
    """Import every file in data/import/inbox/ (or the given paths), then file them away."""
    conn = conn or db.connect()
    inbox, done, rejected = IMPORT_DIR / "inbox", IMPORT_DIR / "done", IMPORT_DIR / "rejected"
    for d in (inbox, done, rejected):
        d.mkdir(parents=True, exist_ok=True)
    files = [Path(p) for p in paths] if paths else sorted(p for p in inbox.iterdir() if p.suffix.lower() in (".jsonl", ".csv"))
    totals = {"added": 0, "updated": 0, "rejected": 0}
    for f in files:
        records = read_file(f)
        bad = [r for r in records if "_bad_line" in r]
        report = import_records(conn, [r for r in records if "_bad_line" not in r], dry_run=dry_run)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rej = report["rejected"] + [{"record": r, "errors": [f"line {r['_bad_line']} is not valid JSON: {r['_error']}"]} for r in bad]
        if rej and not dry_run:
            with open(rejected / f"{stamp}-{f.stem}.jsonl", "w", encoding="utf-8") as out:
                for r in rej:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
        if f.parent.resolve() == inbox.resolve() and not dry_run:
            f.rename(done / f"{stamp}-{f.name}")
        log(("[dry run, nothing saved] " if dry_run else "") + f"Leases from {f.name}: {report['added']} added, {report['updated']} updated, {len(rej)} rejected, "
            f"{len(report['skipped'])} skipped, {report['needs_review']} to check by hand.")
        for line in report["skipped"] + report["warnings"] + [f"rejected: {'; '.join(r['errors'])}" for r in rej]:
            log(f"  - {line}")
        totals["added"] += report["added"]
        totals["updated"] += report["updated"]
        totals["rejected"] += len(rej)
    return totals


def mark_verified(conn, rid):
    r = conn.execute("SELECT data FROM rentals WHERE id=?", (rid,)).fetchone()
    if not r:
        return False
    d = json.loads(r["data"])
    meta = d.setdefault("_import", {})
    meta["needs_review"] = False
    meta["verified_at"] = db.now()
    conn.execute("UPDATE rentals SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), rid))
    conn.commit()
    return True
