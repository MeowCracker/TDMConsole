# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for TDMConsole command-line and macOS app builds.

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
log.txt) is written next to the CLI executable; the macOS app uses
``~/Library/Application Support/TDMConsole``.

Build:  uv run pyinstaller tdmconsole.spec
Output: dist/tdmconsole  (or dist/tdmconsole.exe on Windows)
        dist/TDMConsole.app  (macOS only, defaults to native GUI mode)
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

from tdm_cli import __version__ as APP_VERSION

if not (SUBMODULE / "twitch.py").is_file():
    raise SystemExit(
        "Upstream submodule missing. Run: git submodule update --init --recursive"
    )

# Freeze the engine's commit hash into tdm_cli/_build_info.py before Analysis —
# the frozen binary has no .git, so the hash must be baked in at build time.
import subprocess as _sp
_sp.run([sys.executable, str(ROOT / "scripts" / "write_build_info.py")], check=True)

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

# App icon (executable icon), set per-platform below. The web UI's browser-tab
# favicon.png already lives in tdm_cli/web/static/ and is collected by the loop
# above, so it works in both source (`uv run`) and frozen modes.
ASSETS = ROOT / "assets"
if sys.platform == "win32":
    _icon = ASSETS / "favicon.ico"
elif sys.platform == "darwin":
    _icon = ASSETS / "favicon.icns"
else:
    _icon = ASSETS / "favicon.png"   # Linux: PyInstaller accepts a PNG
app_icon: str | None = str(_icon) if _icon.is_file() else None

# Textual ships runtime data (tree-sitter grammars, py.typed); pull it all in.
tx_datas, tx_binaries, tx_hidden = collect_all("textual")
datas += tx_datas

# ---- GUI mode deps -------------------------------------------------------
# `--mode gui` runs upstream's native tkinter window, which needs Pillow and
# pystray for real (bundled here so the single-file exe supports gui mode).
# Their icon assets live in the upstream submodule's icons/ dir.
pil_datas, pil_binaries, pil_hidden = collect_all("PIL")
tray_datas, tray_binaries, tray_hidden = collect_all("pystray")
datas += pil_datas + tray_datas
for icon in (SUBMODULE / "icons").glob("*"):
    if icon.is_file():
        datas.append((str(icon), "icons"))

# ---- hidden imports ------------------------------------------------------
# Our own subpackages are imported lazily by mode, so name them explicitly.
hiddenimports: list[str] = [
    "truststore",
    *collect_submodules("tdm_cli"),
    *collect_submodules("aiohttp"),
    *tx_hidden,
    *pil_hidden,
    *tray_hidden,
]

# ---- exclusions ----------------------------------------------------------
# Heavy deps neither the CLI frontends nor upstream's tkinter GUI ever use.
# tkinter/PIL/pystray are deliberately NOT excluded — gui mode needs them.
excludes: list[str] = [
    "selenium", "seleniumwire",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib", "numpy",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT), str(SUBMODULE)],
    datas=datas,
    binaries=tx_binaries + pil_binaries + tray_binaries,
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
    icon=app_icon,
)

# macOS also gets a normal windowed app bundle. It reuses the same Analysis and
# PYZ as the CLI executable, but keeps binaries outside the bootloader so BUNDLE
# can place them in the standard Contents/Frameworks layout.
if sys.platform == "darwin":
    from PyInstaller.building.osx import BUNDLE

    entitlements = ASSETS / "entitlements.plist"
    if not entitlements.is_file():
        raise SystemExit(f"Missing macOS entitlements: {entitlements}")

    app_exe = EXE(
        pyz,
        a.scripts,
        [],
        name="TDMConsole",
        exclude_binaries=True,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=str(entitlements),
        icon=app_icon,
    )

    app = BUNDLE(
        app_exe,
        a.binaries,
        a.datas,
        name="TDMConsole.app",
        icon=app_icon,
        version=APP_VERSION,
        bundle_identifier="com.github.meowcracker.tdmconsole",
        info_plist={
            "LSApplicationCategoryType": "public.app-category.utilities",
            "NSHighResolutionCapable": True,
        },
    )
