"""Stefan's own text, editable from the desk's "My details" page: his contact details (settings.json "agent"
and office_email), the message templates (templates/*.txt) and his style samples (drafts.samples_path).

Everything is read fresh on each request, so a save shows up in the next message the desk renders.
Only these keys of settings.json are ever written here; permissions and the rest are left as they are.
"""
import json
import os
import string
import tempfile

from . import drafts
from .config import CONFIG_DIR, ROOT, TEMPLATE_DIR

DETAILS = [  # (key in settings.agent, label); office_email sits at the top level of settings
    ("name", "Your name"),
    ("phone", "Phone"),
    ("email", "Email"),
    ("title_fr", "Title in French"),
    ("title_en", "Title in English"),
    ("agency", "Agency"),
    ("rental_page_url", "Rental page link"),
]

_AGENT = {"agent_name", "agent_phone"}
TEMPLATES = {  # file name: (label, the fill-ins it may use)
    "outreach_fr": ("First message, French", _AGENT | {"city", "agent_title_fr", "agent_title_en", "agent_email", "agency", "rental_page_url"}),
    "outreach_en": ("First message, English", _AGENT | {"city", "agent_title_fr", "agent_title_en", "agent_email", "agency", "rental_page_url"}),
    "followup_fr": ("Follow-up, French", _AGENT | {"city", "agent_title_fr", "agent_title_en", "agent_email", "agency", "rental_page_url"}),
    "followup_en": ("Follow-up, English", _AGENT | {"city", "agent_title_fr", "agent_title_en", "agent_email", "agency", "rental_page_url"}),
    "renewal_landlord_fr": ("Lease renewal to the landlord, French", _AGENT | {"landlord_name", "address", "lease_end", "window_opens", "window_closes", "tal_paragraph"}),
    "renewal_landlord_en": ("Lease renewal to the landlord, English", _AGENT | {"landlord_name", "address", "lease_end", "window_opens", "window_closes", "tal_paragraph"}),
}

SAMPLES_FILE = "my-messages.txt"


def _write(path, text):
    """Write through a temporary file, so a crash mid-save never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save_details(form):
    path = CONFIG_DIR / "settings.json"
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    agent = dict(raw.get("agent") or {})
    for key, _label in DETAILS:
        if key in form:
            agent[key] = form[key].strip()
    raw["agent"] = agent
    if "office_email" in form:
        raw["office_email"] = form["office_email"].strip()
    _write(path, json.dumps(raw, ensure_ascii=False, indent=2) + "\n")


def read_template(name):
    return (TEMPLATE_DIR / f"{name}.txt").read_text(encoding="utf-8")


def check_template(name, text):
    """The problems that would stop the desk from filling this template in ([] when it's fine)."""
    if not text.strip():
        return ["The message is empty."]
    allowed = TEMPLATES[name][1]
    try:
        used = {f for _lit, f, _spec, _conv in string.Formatter().parse(text) if f is not None}
    except ValueError:
        return ["A curly bracket { or } is missing its pair. Fill-ins look like {agent_phone}."]
    unknown = sorted("{" + f + "}" for f in used - allowed)
    if unknown:
        return [f"The desk doesn't know {u}. It can fill in: " + ", ".join(f"{{{a}}}" for a in sorted(allowed))
                for u in unknown]
    return []


def save_template(name, text):
    problems = check_template(name, text)
    if not problems:
        _write(TEMPLATE_DIR / f"{name}.txt", text.replace("\r\n", "\n").strip() + "\n")
    return problems


def samples_path(settings):
    """The file the page edits: the samples file itself, or my-messages.txt inside the samples folder."""
    p = drafts.samples_dir(settings) or ROOT / "data" / "outreach-samples"
    return p if p.suffix.lower() in drafts.SAMPLE_TYPES else p / SAMPLES_FILE


def read_samples(settings):
    p = samples_path(settings)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def save_samples(settings, text):
    _write(samples_path(settings), text.replace("\r\n", "\n").strip() + "\n")
