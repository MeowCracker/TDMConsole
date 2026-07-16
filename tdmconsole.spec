# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for TDMConsole — a single-file, self-contained executable.

Bundles everything the app needs to run with no external files:
  * the outer ``tdm_cli`` package (all four frontends);
  * the pristine upstream ``TwitchDropsMiner`` submodule, compiled in as
    top-level modules (``twitch``, ``constants``, ``utils``, ...);
  * upstream ``lang/*.json`` (21-language i18n catalogue);
  * our ``tdm_cli/web/static/*`` (the web UI: index.html / app.css / app.js);
  * Textual's runtime data files.

One-file mode (``EXE`` with the data/binary TOCs inlined, no ``COLLECT``): the
launcher unpacks to a temp dir at startup and cleans up on exit, so the user
only ever sees a single executable. Runtime *state* (settings.json, cookies.jar,
log.txt) is written next to the executable — see ``constants.WORKING_DIR``.

Build:  pyinstaller tdmconsole.spec
Output: dist/tdmconsole  (or dist/tdmconsole.exe on Windows)
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# The spec runs from the repo root. Make both the outer package and the upstream
# submodule importable so Analysis can trace and compile them into the bundle.
ROOT = Path(SPECPATH).resolve()
SUBMODULE = ROOT / "TwitchDropsMiner"
for p in (str(ROOT), str(SUBMODULE)):
    if p not in sys.path:
        sys.path.insert(0, p)

if not (SUBMODULE / "twitch.py").is_file():
    raise SystemExit(
        "Upstream submodule missing. Run: git submodule update --init --recursive"
    )

# ---- data files ----------------------------------------------------------
# (source, dest-dir-inside-bundle). Upstream resolves lang/ via _resource_path()
# -> sys._MEIPASS/lang; our web server resolves static/ via __file__ ->
# _MEIPASS/tdm_cli/web/static. Mirror those layouts here.
datas: list[tuple[str, str]] = []

for lang in (SUBMODULE / "lang").glob("*.json"):
    datas.append((str(lang), "lang"))

static_dir = ROOT / "tdm_cli" / "web" / "static"
for asset in static_dir.iterdir():
    if asset.is_file():
        datas.append((str(asset), "tdm_cli/web/static"))

# Textual ships runtime data (tree-sitter grammars, py.typed); pull it all in.
tx_datas, tx_binaries, tx_hidden = collect_all("textual")
datas += tx_datas

# ---- hidden imports ------------------------------------------------------
# Our own subpackages are imported lazily by mode, so name them explicitly.
hiddenimports: list[str] = [
    "truststore",
    *collect_submodules("tdm_cli"),
    *collect_submodules("aiohttp"),
    *tx_hidden,
]

# ---- exclusions ----------------------------------------------------------
# GUI-only deps the CLI never uses (bootstrap stubs tkinter/PIL when absent);
# excluding them keeps the binary small and avoids spurious import failures.
excludes: list[str] = [
    "tkinter", "PIL", "pystray", "selenium", "seleniumwire",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib", "numpy",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT), str(SUBMODULE)],
    datas=datas,
    binaries=tx_binaries,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="tdmconsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # TUI / REPL / web all need a console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
