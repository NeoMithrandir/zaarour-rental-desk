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


def save_boards(form):
    """Boards on the desk: a board left unticked keeps being read, but its new ads stay off the review screen."""
    from .send import ROUTES
    path = CONFIG_DIR / "settings.json"
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw["hidden_sources"] = [k for k in ROUTES if f"show_{k}" not in form]
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
        drop_suggestion(name)
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


# ---------------------------------------------------------------- wording suggested from the samples
SUGGEST = ["outreach_fr", "outreach_en"]   # the samples are first messages, so they shape the first-message wording
SUGGESTIONS = ROOT / "data" / "template-suggestions.json"


def suggestions():
    try:
        return json.loads(SUGGESTIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _store(sugg):
    _write(SUGGESTIONS, json.dumps(sugg, ensure_ascii=False, indent=2) + "\n")


def drop_suggestion(name):
    sugg = suggestions()
    if sugg.pop(name, None) is not None:
        _store(sugg)


def suggest_prompt(samples, name, settings):
    lang = name.rsplit("_", 1)[1]
    allowed = sorted(TEMPLATES[name][1])
    shown = "\n\n".join(f'<sample n="{i + 1}">\n{s["text"]}\n</sample>' for i, s in enumerate(samples))
    return f"""You rewrite a real estate broker's standard first message to landlords who posted a rental ad themselves,
so it sounds like the broker. The desk fills in this message for every ad, so it must not mention any one ad.

Messages the broker wrote before (some may be in French, some in English; take the voice from all of them):
{shown}

The current standard message:
<current>
{read_template(name)}
</current>

Rules:
- Write it in {"French (Québec)" if lang == "fr" else "English"}, in the broker's voice, length and structure.
- Keep what the current message offers; never invent facts, prices or promises.
- Use these fill-ins, written exactly like this, where the details go: {", ".join("{" + a + "}" for a in allowed)}.
  Use {{city}} for the ad's city and {{agent_phone}} for the phone number. No other curly brackets.
- End with this line exactly: {drafts.outreach.DISCLAIMER[lang]}
- Output only the message."""


def suggest_templates(settings, client=None):
    """Write new first-message wording in French and English from the samples, for Stefan to review.
    Returns {name: problem} for the ones that failed; the rest are stored as suggestions, not saved."""
    samples = drafts.load_samples(settings)
    run, sugg, failed = drafts.runner(settings, client), suggestions(), {}
    for name in SUGGEST:
        lang = name.rsplit("_", 1)[1]
        try:
            text, _model = run(suggest_prompt(drafts.pick_samples(samples, lang, settings), name, settings),
                               "Write the standard message now.")
        except drafts.DraftError as e:
            failed[name] = str(e)
            continue
        text = drafts.finish(text, lang)
        problems = check_template(name, text)
        if problems:
            failed[name] = problems[0]
        else:
            sugg[name] = text
    _store(sugg)
    return failed
