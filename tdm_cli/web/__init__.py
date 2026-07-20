"""WebUI frontend — an aiohttp server + WebSocket dashboard for Docker.

Unlike the terminal frontends it does not take over stdout, so it needs no TTY:
``main.py`` selects it for ``--mode web`` and never downgrades it. Host/port are
set on this module (from CLI flags / env) before the manager is constructed.
"""
from __future__ import annotations

import sys
import asyncio
from contextlib import suppress
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from tdm_cli.gui import GUIManager
    from tdm_cli.web.server import WebServer

# Set by main.py before Twitch()/GUIManager is constructed.
HOST: str = "127.0.0.1"
PORT: int = 8080
AUTH_USERNAME: str | None = None
AUTH_PASSWORD: str | None = None


class WebFrontend:
    def __init__(self, manager: GUIManager) -> None:
        self._m = manager
        self._server: WebServer | None = None
        self._task: asyncio.Task[Any] | None = None
        self._intentional_stop = False

    # lifecycle -------------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._intentional_stop = False
            self._task = asyncio.create_task(self._serve())

    async def _serve(self) -> None:
        from tdm_cli.web.server import WebServer

        self._server = WebServer(
            self._m,
            HOST,
            PORT,
            username=AUTH_USERNAME,
            password=AUTH_PASSWORD,
        )
        try:
            await self._server.start()
        except Exception as exc:
            print(f"Web server failed to start on {HOST}:{PORT}: {exc!r}", file=sys.stderr)
            if not self._intentional_stop and not self._m.close_requested:
                self._m.close()
            self._server = None
            return
        url = self._server.url
        # Both to the miner log (for other frontends) and stdout (Docker logs).
        self._m.print(f"Web UI serving on {url}")
        print(f"TDMConsole web UI serving on {url}  (Ctrl+C to stop)", flush=True)
        try:
            await self._server.wait_closed()
        finally:
            await self._server.cleanup()
            self._server = None

    def begin_intentional_stop(self) -> None:
        self._intentional_stop = True

    def stop(self) -> None:
        self._intentional_stop = True
        if self._server is not None:
            self._server.request_stop()

    def is_stopped(self) -> bool:
        return self._task is None or self._task.done()

    async def wait_stopped(self) -> None:
        self.stop()
        if self._task is not None:
            with suppress(Exception):
                await self._task
            self._task = None

    def close_window(self) -> None:
        pass

    def attention(self) -> None:
        pass

    # event hooks — dashboard state is snapshot-driven; logs also go to stdout
    # so `docker logs` shows mining activity (browsers get them via the snapshot).
    def log(self, text: str, style: str = "") -> None:
        from tdm_cli import console

        console.emit(text)

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

    def on_login_available(self, page_url: str, user_code: str, first: bool) -> None:
        if first:
            self._m.state.add_log(
                "Twitch login required — click Login in the web UI to enter your code.",
                "notify",
            )

    def hide_login(self) -> None:
        pass
