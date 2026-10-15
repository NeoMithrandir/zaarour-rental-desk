"""Outreach messages (plan items 1.5, 1.6, 1.10): pick the language, fill the template, check length."""
import hashlib
import re

from .config import TEMPLATE_DIR
from .text import fold

DISCLAIMER = {
    "fr": "Si vous êtes déjà sous contrat avec un courtier, veuillez ignorer ce message.",
    "en": "If you're already under contract with a broker, please disregard this message.",
}

_FR = re.compile(r"\b(a louer|logement|appartement|chambres?|libre|inclus|electromenagers|disponible|le|la|les|et|avec|pres|metro|stationnement)\b")
_EN = re.compile(r"\b(for rent|apartment|available|included|bedrooms?|the|and|with|near|parking|utilities)\b")


def detect_lang(text):
    t = fold(text)
    fr, en = len(_FR.findall(t)), len(_EN.findall(t))
    return "en" if en > fr else "fr"  # Quebec default: French on a tie


def _city(address, area):
    parts = [p.strip() for p in (address or "").split(",")]
    for i, p in enumerate(parts):
        if p.upper().startswith("QC") and i > 0:
            city = parts[i - 1]
            break
    else:
        city = ""
    if not city or re.match(r"\d", city):
        city = (area or "").split(" / ")[0]
    if fold(city) in ("montreal", "montréal") and area:
        city = area.split(" / ")[0]
    return city or "votre secteur"


def load_template(kind, lang):
    with open(TEMPLATE_DIR / f"{kind}_{lang}.txt", encoding="utf-8") as f:
        return f.read().strip()


def template_id(kind, lang):
    """'outreach_fr@1a2b3c4d': the template file plus a hash of its text, so rewordings are told apart."""
    return f"{kind}_{lang}@" + hashlib.sha1(load_template(kind, lang).encode("utf-8")).hexdigest()[:8]


def render(lead, settings, lang=None, kind="outreach"):
    lang = lang or lead.get("lang") or "fr"
    agent = settings.get("agent", {})
    values = {
        "city": _city(lead.get("address"), lead.get("area")),
        "agent_name": agent.get("name", ""),
        "agent_title_fr": agent.get("title_fr", ""),
        "agent_title_en": agent.get("title_en", ""),
        "agent_phone": agent.get("phone", ""),
        "agent_email": agent.get("email", ""),
        "agency": agent.get("agency", ""),
        "rental_page_url": agent.get("rental_page_url", ""),
    }
    msg = load_template(kind, lang).format_map(values)
    # The disclaimer is required on every solicitation (1.6): never let an edited template drop it.
    if DISCLAIMER[lang] not in msg:
        msg = msg.rstrip() + "\n\n" + DISCLAIMER[lang]
    return msg


def length_check(msg, settings):
    limit = int(settings.get("message_char_limit", 1000))
    return {"length": len(msg), "limit": limit, "ok": len(msg) <= limit}
