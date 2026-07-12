"""Import-time bootstrap that lets the pristine upstream core run as a CLI.

Call :func:`setup` exactly once, *before* importing ``twitch`` / ``settings`` /
etc. It:

1. puts the ``TwitchDropsMiner`` submodule on ``sys.path`` so its modules import
   as top-level names (``twitch``, ``constants``, ``utils``, ...);
2. installs lightweight stubs for GUI-only third-party deps (``tkinter``,
   ``PIL``) *only if they are not installed*, so a headless box needs neither
   python3-tk nor Pillow — ``utils.py`` imports them at module top-level but only
   ``set_root_icon`` (never called in CLI) actually uses them;
3. registers :mod:`tdm_cli.gui` as ``sys.modules["gui"]`` so the upstream
   ``from gui import GUIManager`` resolves to the terminal implementation.

Nothing in the submodule is modified, which is what keeps upstream syncing to a
plain ``git submodule update --remote``.
"""
from __future__ import annotations

import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
SUBMODULE_DIR = os.path.normpath(os.path.join(_HERE, os.pardir, "TwitchDropsMiner"))

_done = False


def setup(settings_path: str | None = None, cookies_path: str | None = None) -> None:
    """Prepare the import environment. Call once, before importing upstream code.

    ``settings_path`` / ``cookies_path`` override where the miner reads/writes
    ``settings.json`` and ``cookies.jar`` (the ``-c/--config`` and ``--jar``
    CLI flags). They must be applied here because upstream modules bind these
    constants at import time (``from constants import SETTINGS_PATH, ...``).
    """
    global _done
    if _done:
        return
    if not os.path.isfile(os.path.join(SUBMODULE_DIR, "twitch.py")):
        raise RuntimeError(
            f"Upstream submodule not found at {SUBMODULE_DIR}.\n"
            "Initialise it with:  git submodule update --init --recursive"
        )
    if SUBMODULE_DIR not in sys.path:
        sys.path.insert(0, SUBMODULE_DIR)
    _patch_constants(settings_path, cookies_path)
    _stub_gui_deps()
    from tdm_cli import gui as cli_gui

    sys.modules["gui"] = cli_gui
    _done = True


def _patch_constants(settings_path: str | None, cookies_path: str | None) -> None:
    """Repoint upstream constants before any other upstream module imports them.

    - ``LANG_PATH``: upstream derives it from ``WORKING_DIR`` = the directory of
      ``sys.argv[0]``. For us that is the outer repo (where ``main.py`` lives),
      not the submodule that actually ships ``lang/``. Runtime *state*
      (settings.json, cookies.jar, log.txt, lock.file, cache/) intentionally
      keeps landing in the outer dir — only the resource lookup is repointed.
    - ``SETTINGS_PATH`` / ``COOKIES_PATH``: user-provided overrides.
    """
    from pathlib import Path

    import constants

    constants.LANG_PATH = Path(SUBMODULE_DIR, "lang")
    if settings_path:
        constants.SETTINGS_PATH = Path(settings_path).expanduser().resolve()
    if cookies_path:
        constants.COOKIES_PATH = Path(cookies_path).expanduser().resolve()


class _Dummy:
    """A permissive placeholder: constructing / calling / attribute access all
    return another ``_Dummy``. Never actually exercised in the CLI path."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __call__(self, *args: object, **kwargs: object) -> "_Dummy":
        return self

    def __getattr__(self, _name: str) -> "_Dummy":
        return self


def _new_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda _n: _Dummy()  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod


def _stub_gui_deps() -> None:
    """Install stub modules for GUI-only deps that are not importable."""
    # tkinter (+ common submodules) — utils.py: ``import tkinter as tk``
    try:
        import tkinter  # noqa: F401
    except Exception:
        tk = _new_module("tkinter")
        tk.Tk = _Dummy  # type: ignore[attr-defined]
        tk.TclError = type("TclError", (Exception,), {})  # type: ignore[attr-defined]
        for sub in ("ttk", "font", "messagebox", "constants", "filedialog"):
            child = _new_module(f"tkinter.{sub}")
            setattr(tk, sub, child)

    # Pillow — utils.py: ``from PIL.ImageTk import PhotoImage`` /
    # ``from PIL import Image as Image_module``
    try:
        import PIL.Image  # noqa: F401
        import PIL.ImageTk  # noqa: F401
    except Exception:
        pil = _new_module("PIL")
        image_mod = _new_module("PIL.Image")
        image_mod.open = lambda *a, **k: _Dummy()  # type: ignore[attr-defined]
        image_mod.Image = _Dummy  # type: ignore[attr-defined]
        imagetk_mod = _new_module("PIL.ImageTk")
        imagetk_mod.PhotoImage = _Dummy  # type: ignore[attr-defined]
        pil.Image = image_mod  # type: ignore[attr-defined]
        pil.ImageTk = imagetk_mod  # type: ignore[attr-defined]


def verify_contract() -> list[str]:
    """Best-effort check that our shim satisfies the upstream core.

    Returns a list of human-readable problems (empty == OK). Detects the two
    ways an upstream bump can break us: the ``gui`` injection not taking effect,
    or a component losing a member the core still calls.
    """
    problems: list[str] = []
    try:
        import twitch  # noqa: F401  (import proves ``from gui import GUIManager`` resolved)
        from tdm_cli import gui as cli_gui
    except Exception as exc:  # pragma: no cover - surfaced to the user
        return [f"importing upstream 'twitch' with the CLI gui shim failed: {exc!r}"]

    if getattr(twitch, "GUIManager", None) is not cli_gui.GUIManager:
        problems.append("twitch.GUIManager is not the CLI shim (injection failed)")

    # Members the core relies on, grouped by component.
    required: dict[str, tuple[str, ...]] = {
        "": (
            "close_requested", "running", "wait_until_closed", "coro_unless_closed",
            "prevent_close", "start", "stop", "close", "close_window", "save",
            "grab_attention", "set_games", "display_drop", "clear_drop", "print",
        ),
        "status": ("update", "clear"),
        "tray": ("change_icon", "notify", "update_title"),
        "login": ("ask_enter_code", "ask_login", "update", "clear"),
        "progress": ("start_timer", "stop_timer", "minute_almost_done", "display"),
        "channels": ("clear", "get_selection", "set_watching", "clear_watching",
                     "display", "remove", "clear_selection"),
        "inv": ("clear", "add_campaign", "update_drop"),
        "websockets": ("update", "remove"),
        "settings": ("set_games", "clear_selection"),
        "output": ("print",),
    }
    Manager = cli_gui.GUIManager
    for comp, members in required.items():
        target: object = Manager
        if comp:
            component_cls = {
                "status": cli_gui.StatusBar,
                "tray": cli_gui.TrayIcon,
                "login": cli_gui.LoginForm,
                "progress": cli_gui.CampaignProgress,
                "channels": cli_gui.ChannelList,
                "inv": cli_gui.InventoryOverview,
                "settings": cli_gui.SettingsPanel,
                "websockets": cli_gui.WebsocketStatus,
                "output": cli_gui.ConsoleOutput,
            }.get(comp)
            if component_cls is None:
                problems.append(f"missing component class for '.{comp}'")
                continue
            target = component_cls
        for member in members:
            if not hasattr(target, member):
                dotted = f".{comp}" if comp else "GUIManager"
                problems.append(f"{dotted} is missing '{member}'")
    return problems
