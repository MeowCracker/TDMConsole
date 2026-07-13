"""Claude-Code-style command REPL frontend.

A thin variant of the Textual frontend that runs :class:`ReplApp` — a
scrolling output area with a docked ``❯`` command prompt and status line —
instead of the full-screen dashboard. All slash-command logic lives in
:mod:`tdm_cli.commands`; the layout lives in :mod:`tdm_cli.tui.repl_app`.
"""
from __future__ import annotations

from tdm_cli.tui import TextualFrontend


class ReplFrontend(TextualFrontend):
    def _make_app(self):
        from tdm_cli.tui.repl_app import ReplApp

        return ReplApp(self._manager)
