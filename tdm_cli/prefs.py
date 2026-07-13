"""CLI-frontend preferences (interface mode), persisted separately from the
upstream ``settings.json`` so the pristine core never sees unknown keys.

Stored in ``tdm-cli.json`` next to the other state files (``WORKING_DIR``,
i.e. the outer repo root). Import only after :func:`tdm_cli.bootstrap.setup`.
"""
from __future__ import annotations

import json
from pathlib import Path

MODES = ("tui", "repl", "headless")
DEFAULT_MODE = "tui"


def _prefs_path() -> Path:
    from constants import WORKING_DIR

    return Path(WORKING_DIR, "tdm-cli.json")


def load_mode() -> str | None:
    """Saved interface mode, or None if no valid preference exists."""
    try:
        data = json.loads(_prefs_path().read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    mode = data.get("mode")
    return mode if mode in MODES else None


def save_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown interface mode: {mode!r}")
    path = _prefs_path()
    try:
        data = json.loads(path.read_text(encoding="utf8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["mode"] = mode
    path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf8")
