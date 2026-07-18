"""Version reporting — keeps TDMConsole's own version separate from the bundled
mining engine's.

Two independent version sources:

* **TDMConsole** (this project) — :data:`tdm_cli.__version__`.
* **Engine** (DevilXD's TwitchDropsMiner, used pristine as a submodule) — the
  submodule's ``version.__version__`` plus the short commit hash it is pinned to.

The engine commit hash is resolved in this order:

1. ``tdm_cli/_build_info.py`` — written at build time (Docker / PyInstaller /
   release CI) by ``scripts/write_build_info.py``. This is the only source that
   works once frozen or inside a container, where no ``.git`` exists.
2. Live ``git`` in a source checkout (developer running from the repo).
3. ``"unknown"`` — nothing else worked (should not happen for a real release).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tdm_cli import __version__ as APP_VERSION

_HERE = Path(__file__).resolve().parent
_SUBMODULE = _HERE.parent / "TwitchDropsMiner"


def engine_version() -> str:
    """Upstream TwitchDropsMiner version (its ``version.__version__``)."""
    try:
        from version import __version__ as v  # upstream module, on sys.path

        return str(v)
    except Exception:
        return "unknown"


def _build_info_hash() -> str | None:
    """Engine commit hash frozen in at build time, if present."""
    try:
        from tdm_cli._build_info import ENGINE_COMMIT  # type: ignore

        return str(ENGINE_COMMIT) or None
    except Exception:
        return None


def _git_hash() -> str | None:
    """Short commit hash of the submodule via live git (source checkout only)."""
    if not (_SUBMODULE / ".git").exists() and not (_SUBMODULE / "twitch.py").is_file():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(_SUBMODULE), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        h = out.stdout.strip()
        return h or None
    except Exception:
        return None


def engine_commit() -> str:
    """Short commit hash the engine submodule is pinned to."""
    return _build_info_hash() or _git_hash() or "unknown"


def version_line() -> str:
    """One-line version string: app version + engine version @ commit."""
    return f"TDMConsole v{APP_VERSION} (engine: TwitchDropsMiner {engine_version()} @ {engine_commit()})"


def version_info() -> dict[str, str]:
    """Structured version info (used by the web /meta endpoint)."""
    return {
        "app": APP_VERSION,
        "engine": engine_version(),
        "engineCommit": engine_commit(),
    }
