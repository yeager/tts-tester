"""Settings and favorites management for tts-tester."""

import json
import os


CONFIG_DIR = os.path.expanduser("~/.config/tts-tester")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
FAVORITES_FILE = os.path.join(CONFIG_DIR, "favorites.json")


def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_settings():
    """Load per-engine settings."""
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(settings):
    """Save per-engine settings."""
    _ensure_config_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def load_favorites():
    """Load favorites list."""
    try:
        with open(FAVORITES_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_favorites(favorites):
    """Save favorites list."""
    _ensure_config_dir()
    with open(FAVORITES_FILE, "w") as f:
        json.dump(favorites, f, indent=2)
