"""CLI-frontend interface modes.

The interface mode is chosen at launch via ``--mode`` and is NOT persisted:
a bare launch always uses :data:`DEFAULT_MODE` (web, so ``docker run`` serves
the browser UI out of the box). Running ``--mode X`` is a one-off — it never
writes a preference file that would silently override the default next time.

``/switch-mode`` still changes the mode live within a running process; it just
doesn't survive a restart.
"""
from __future__ import annotations

MODES = ("tui", "repl", "web", "gui", "headless")
DEFAULT_MODE = "web"
