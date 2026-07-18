"""Freeze the engine submodule's commit hash into tdm_cli/_build_info.py.

Run at build time (Docker / PyInstaller / release CI), before packaging, from
the repo root. Once frozen or inside a container there is no ``.git`` to query,
so the hash must be captured here and read back via ``tdm_cli.versioning``.

Usage:  python scripts/write_build_info.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBMODULE = ROOT / "TwitchDropsMiner"
OUT = ROOT / "tdm_cli" / "_build_info.py"


def short_hash() -> str:
    out = subprocess.run(
        ["git", "-C", str(SUBMODULE), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    h = out.stdout.strip()
    if not h:
        print(f"warning: could not read submodule commit: {out.stderr.strip()}",
              file=sys.stderr)
    return h


def main() -> None:
    commit = short_hash()
    OUT.write_text(
        '"""Generated at build time by scripts/write_build_info.py — do not edit.\n'
        'Records the engine (TwitchDropsMiner) commit the build was pinned to."""\n'
        f'ENGINE_COMMIT = {commit!r}\n',
        encoding="utf8",
    )
    print(f"wrote {OUT.relative_to(ROOT)}: ENGINE_COMMIT={commit!r}")


if __name__ == "__main__":
    main()
