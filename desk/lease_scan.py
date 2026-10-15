"""Fill the New lease form from photos or scans of a lease, using Claude's vision.

The pages are saved locally (data/scans/<scan id>/) and sent to the Claude API, which reads them
and streams back one field per line as it finds it. Each field is normalised with the same rules as
the lease import (docs/lease-import-spec.md), so a scanned lease and an imported one have one shape.
Nothing is saved to the tracker here: the form fills in, the user checks it and clicks Save.

Needs the `anthropic` package (python3 -m pip install anthropic) and an API key: the
ANTHROPIC_API_KEY environment variable, `anthropic_api_key` in config/settings.json, or an
`ant auth login` profile.
"""
import base64
import json
import re
from datetime import datetime

from . import leases
from .config import DATA_DIR, load_settings
from .lease_import import clean_field

SCAN_DIR = DATA_DIR / "scans"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
PDF = "application/pdf"
MAX_FILES = 20
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", PDF: ".pdf"}


class ScanError(Exception):
    """Something the person can fix (missing package or key, unsupported file); shown as is."""


def _field_guide():
    lines = []
    for key, label, kind, _req, _sec in leases.FIELDS:
        if key in ("commission", "double_ended", "co_broker", "photographer", "lead_source", "notes_for_office"):
            continue  # deal details aren't on a lease
        fmt = {"date": "YYYY-MM-DD", "money": "number, dollars per month unless the label says otherwise",
               "check": "true or false", "email": "email address", "tel": "phone number as written"}.get(kind, "text")
        if kind.startswith("select:"):
            fmt = "one of: " + " | ".join(kind[7:].split("|"))
        lines.append(f"- {key}: {label} ({fmt})")
    return "\n".join(lines)


SYSTEM = """You read residential lease documents from Québec (usually the TAL "Bail de logement" form, in French or English, \
sometimes with annexes or handwriting) and extract the lease's details for a broker's office.

Output one JSON object per line and nothing else: no prose, no code fences. As soon as you have read a field, write
{"field": "<key>", "value": <value>, "confidence": <0 to 1>, "evidence": "<the words on the page, short>", "page": <page number>}
Only use these keys:
""" + _field_guide() + """

Rules:
- Only report what the pages actually say. Leave a field out rather than guess. If you infer something (e.g. the end date
  from a stated 12-month term), say so in evidence and lower confidence below 0.9.
- confidence is how sure you are the value is right and correctly read: below 0.9 for hard-to-read handwriting, a partly
  visible page, or an inference.
- Several lessors or lessees: join the names with ", ". The lessor (locateur) is the landlord; the lessee (locataire) is the tenant.
- Rent is the monthly rent (loyer) for the dwelling; services_cost is the monthly cost of services and other charges.
- "Who pays" fields (heating, hot_water, electricity) are Landlord or Tenant.
- After the last field, write one final line: {"done": true, "document": "<what the pages are>", "notes": "<anything a person should check, or empty>"}"""


def _client(settings):
    try:
        import anthropic
    except ImportError:
        raise ScanError("The photo scan needs the Anthropic package. In Terminal, run: python3 -m pip install anthropic")
    key = settings.get("anthropic_api_key") or None
    return anthropic, (anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic())


def save_pages(files):
    """Keep the pages on this Mac. files: [{"name", "type", "data": base64}]. Returns (scan_id, pages)."""
    if not files:
        raise ScanError("Add at least one photo or PDF.")
    if len(files) > MAX_FILES:
        raise ScanError(f"At most {MAX_FILES} pages at a time.")
    scan_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = SCAN_DIR / scan_id
    folder.mkdir(parents=True, exist_ok=True)
    pages = []
    for i, f in enumerate(files, 1):
        mtype = (f.get("type") or "").lower()
        if mtype not in IMAGE_TYPES and mtype != PDF:
            raise ScanError(f"{f.get('name') or 'A file'} isn't a photo (JPEG, PNG, WebP) or a PDF.")
        data = re.sub(r"^data:[^,]*,", "", f.get("data") or "")
        raw = base64.b64decode(data)
        (folder / f"page-{i:02d}{EXT[mtype]}").write_bytes(raw)
        pages.append({"type": mtype, "data": base64.standard_b64encode(raw).decode("ascii")})
    return scan_id, pages


def _content(pages):
    blocks = []
    for i, p in enumerate(pages, 1):
        blocks.append({"type": "text", "text": f"Page {i}:"})
        if p["type"] == PDF:
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": PDF, "data": p["data"]}})
        else:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": p["type"], "data": p["data"]}})
    blocks.append({"type": "text", "text": "Extract this lease's details, one JSON line per field."})
    return blocks


def parse_line(line, min_conf=0.9):
    """One line of the model's output -> an event for the browser, or None."""
    line = line.strip().strip("`")
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if obj.get("done"):
        return {"type": "done", "document": obj.get("document", ""), "notes": obj.get("notes", "")}
    key = obj.get("field")
    if key not in leases.FIELD_KEYS:
        return None
    value, err, warn = clean_field(key, obj.get("value"))
    try:
        conf = max(0.0, min(1.0, float(obj.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    if err or warn or value == "":
        return {"type": "skipped", "field": key, "raw": obj.get("value"), "reason": err or warn or "empty"}
    return {"type": "field", "field": key, "value": value, "confidence": conf,
            "check": conf < min_conf, "evidence": str(obj.get("evidence") or "")[:300], "page": obj.get("page")}


def lines_from(chunks):
    """Join streamed text chunks and yield complete lines as they finish."""
    buf = ""
    for chunk in chunks:
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            yield line
    if buf.strip():
        yield buf


def extract(files, settings=None, client=None):
    """Generator of events: {"type": "start"|"field"|"skipped"|"done"|"error", ...}."""
    settings = settings or load_settings()
    min_conf = float(settings.get("import_min_confidence", 0.9))
    scan_id, pages = save_pages(files)
    yield {"type": "start", "scan_id": scan_id, "pages": len(pages)}
    if client is None:
        anthropic, client = _client(settings)
        api_errors = (anthropic.APIStatusError, anthropic.APIConnectionError)
    else:
        api_errors = ()
    model = settings.get("lease_scan_model", "claude-opus-5-5")
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=16000,
            output_config={"effort": settings.get("lease_scan_effort", "low")},
            # If the model declines (a safety classifier false positive), the API retries on its default fallback.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": _content(pages)}],
        ) as stream:
            seen = set()
            for line in lines_from(stream.text_stream):
                ev = parse_line(line, min_conf)
                if not ev or (ev["type"] == "field" and ev["field"] in seen):
                    continue
                if ev["type"] == "field":
                    seen.add(ev["field"])
                yield ev
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            yield {"type": "error", "message": "The pages couldn't be read automatically. Fill the form by hand."}
        elif final.stop_reason == "max_tokens":
            yield {"type": "error", "message": "The reading stopped early. Check the remaining fields by hand."}
    except api_errors as e:
        status = getattr(e, "status_code", None)
        msg = {401: "The API key was refused. Check anthropic_api_key in config/settings.json.",
               429: "Too many requests right now. Wait a minute and try again."}.get(status, f"The API call failed: {e}")
        yield {"type": "error", "message": msg}
