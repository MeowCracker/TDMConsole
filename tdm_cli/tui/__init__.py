"""Textual (full-screen TUI) frontend.

``TextualFrontend`` is the bridge between :class:`tdm_cli.gui.GUIManager` and
the Textual app: the manager's ``start()`` launches the app as a task on the
same asyncio loop the miner runs on, and all rendering is timer-driven off
``manager.state`` — so the event hooks here are no-ops.
"""
from __future__ import annotations

import sys
import asyncio
from contextlib import suppress
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from tdm_cli.gui import GUIManager
    from tdm_cli.tui.app import MinerApp


class TextualFrontend:
    def __init__(self, manager: GUIManager) -> None:
        self._manager = manager
        self._app: MinerApp | None = None
        self._app_task: asyncio.Task[Any] | None = None

    # lifecycle -------------------------------------------------------------
    def start(self) -> None:
        if self._app_task is None:
            from tdm_cli.tui.app import MinerApp

            self._app = MinerApp(self._manager)
            self._app_task = asyncio.create_task(self._app.run_async())
            self._app_task.add_done_callback(self._on_app_done)

    def _on_app_done(self, task: asyncio.Task[Any]) -> None:
        # If the TUI dies (crash or unexpected exit) while the miner still runs,
        # unwind the whole program instead of mining blind.
        if not task.cancelled() and (exc := task.exception()) is not None:
            print(f"TUI crashed: {exc!r}", file=sys.stderr)
        if not self._manager.close_requested:
            self._manager.close()

    def stop(self) -> None:
        if self._app is not None and self._app.is_running:
            self._app.exit()

    async def wait_stopped(self) -> None:
        """Await full app teardown so the terminal is restored before exit."""
        self.stop()
        if self._app_task is not None:
            with suppress(Exception):
                await self._app_task
            self._app_task = None

    def close_window(self) -> None:
        pass

    def attention(self) -> None:
        if self._app is not None and self._app.is_running:
            self._app.bell()

    # event hooks — rendering is timer-driven off manager.state -------------
    def log(self, text: str, style: str = "") -> None:
        pass

    def on_status(self, text: str) -> None:
        pass

    def on_login(self, status: str, user_id: int | None) -> None:
        pass

    def on_websocket(self, idx: int, status: str) -> None:
        pass

    def on_watching(self, channel: Any) -> None:
        pass

    def on_drop(self, line: str) -> None:
        pass

    def show_login(self, page_url: str, user_code: str) -> None:
        pass

    def hide_login(self) -> None:
        pass
