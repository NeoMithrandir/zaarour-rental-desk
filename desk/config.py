"""Paths and settings for the rentals desk."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = ROOT / "templates"
UI_DIR = Path(__file__).resolve().parent / "ui"
DB_PATH = DATA_DIR / "desk.sqlite3"


def load_settings():
    """settings.json if present, else the example file (so the app runs out of the box)."""
    path = CONFIG_DIR / "settings.json"
    if not path.exists():
        path = CONFIG_DIR / "settings.example.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_areas():
    with open(CONFIG_DIR / "areas.json", encoding="utf-8") as f:
        return json.load(f)["areas"]
