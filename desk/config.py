"""Paths and settings for the rentals desk."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = ROOT / "templates"
UI_DIR = Path(__file__).resolve().parent / "ui"
EXTENSION_DIR = ROOT / "extension"  # the Chrome extension that fills a board's message box (desk/send.py)
DB_PATH = DATA_DIR / "desk.sqlite3"


def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_settings():
    """The example file's defaults, overridden by settings.json when present.

    New options added to the example later still apply to an older settings.json.
    """
    with open(CONFIG_DIR / "settings.example.json", encoding="utf-8") as f:
        settings = json.load(f)
    path = CONFIG_DIR / "settings.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            settings = _merge(settings, json.load(f))
    return settings


def load_areas():
    with open(CONFIG_DIR / "areas.json", encoding="utf-8") as f:
        return json.load(f)["areas"]
