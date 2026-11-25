"""Shared install configuration. Environment variables override the local JSON file."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_HOME = Path(os.getenv("LOCALAPPDATA", Path.home() / ".local/share")) / "Soceth3DStudio"
CONFIG = Path(os.getenv("SOCETH_CONFIG", APP_HOME / "install.json"))


def preferences():
    if CONFIG.is_file():
        return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    return {}


def location(key, fallback):
    return Path(os.getenv("SOCETH_" + key.upper()) or preferences().get(key) or fallback).expanduser().resolve()


RUNTIME = location("runtime", ROOT / "runtime" if (ROOT / "runtime/venv").is_dir() else APP_HOME / "runtime")
DATA = location("data", ROOT / "user_data" if (ROOT / "user_data/studio.sqlite3").is_file() else APP_HOME / "data")
PROJECTS = location("projects", DATA / "projects")
LOGS = location("logs", DATA / "logs")
CACHE = location("cache", RUNTIME)


def configured(key):
    return os.getenv("SOCETH_" + key.upper()) or preferences().get(key)
