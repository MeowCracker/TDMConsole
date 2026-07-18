"""aiohttp-based web server for the WebUI frontend.

Reuses the already-required ``aiohttp`` (no new dependency): a tiny static SPA
plus a WebSocket that streams :class:`~tdm_cli.state.MinerState` snapshots and
receives ``/`` commands routed through the shared
:class:`~tdm_cli.commands.CommandProcessor`.

All broadcasting is snapshot-based off ``manager.state`` — the frontend event
hooks stay no-ops, exactly like the Textual tick loop.
"""
from __future__ import annotations

import json
import asyncio
import logging
from pathlib import Path
from contextlib import suppress
from typing import Any, TYPE_CHECKING

from aiohttp import web, WSMsgType

from tdm_cli.commands import CommandProcessor

if TYPE_CHECKING:
    from tdm_cli.gui import GUIManager

logger = logging.getLogger("TwitchDrops")

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Shown in the WebUI footer under the Settings button (source & docs link).
REPO_URL = "https://github.com/MeowCracker/TDM-CLI"


def snapshot(manager: GUIManager) -> dict[str, Any]:
    """A JSON-serialisable view of the whole miner state for the browser."""
    s = manager.state
    twitch = manager._twitch
    watching = s.watching_channel
    pinned = manager.channels.get_selection()
    settings = twitch.settings
    return {
        "status": s.status,
        "mode": manager.mode,
        "login": {
            "available": s.login_available,
            "prompt": s.login_prompt,
            "url": s.login_url,
            "code": s.login_code,
            "userId": s.user_id,
            "status": s.login_status,
        },
        "watching": {"channel": s.watching_channel, "game": s.watching_game},
        "drop": {
            "rewards": s.drop_rewards,
            "progress": s.drop_progress,
            "remaining": s.drop_remaining,
        },
        "campaign": {
            "name": s.campaign_name,
            "game": s.campaign_game,
            "progress": s.campaign_progress,
            "claimed": s.campaign_claimed,
            "total": s.campaign_total,
            "remaining": s.campaign_remaining,
        },
        "websockets": [
            {"idx": i, "status": st, "topics": t}
            for i, (st, t) in sorted(s.websockets.items())
        ],
        "channels": [
            {
                "id": c.id,
                "name": c.name,
                "game": c.game.name if c.game is not None else None,
                "online": c.online,
                "pending": c.pending_online,
                "viewers": c.viewers,
                "drops": c.drops_enabled,
                "watching": c.name == watching,
                "pinned": pinned is c,
                "locked": pinned is c,
            }
            for c in twitch.channels.values()
        ],
        "campaigns": [
            {
                "game": c.game.name,
                "name": c.name,
                "claimed": c.claimed_drops,
                "total": c.total_drops,
                "progress": c.progress,
                "active": c.active,
                "upcoming": c.upcoming,
            }
            # Sorted by progress (highest first); stable tiebreak by name.
            for c in sorted(
                twitch.inventory,
                key=lambda c: (-c.progress, c.game.name, c.name),
            )
        ],
        "settings": {
            "priority": list(settings.priority),
            "exclude": sorted(settings.exclude),
            "proxy": str(settings.proxy),
            "priorityMode": settings.priority_mode.name,
        },
    }


def _log_payload(entries) -> dict[str, Any]:
    return {
        "type": "log",
        "lines": [
            {"seq": e.seq, "stamp": e.stamp, "text": e.text, "style": e.style}
            for e in entries
        ],
    }


class WebServer:
    def __init__(self, manager: GUIManager, host: str, port: int) -> None:
        self._m = manager
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._broadcast_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._log_cursor = 0
        # Command output is folded into the shared miner log so every client sees it.
        self._processor = CommandProcessor(
            manager, lambda text, style="": manager.state.add_log(text, style)
        )

    @property
    def url(self) -> str:
        shown = "localhost" if self._host in ("0.0.0.0", "127.0.0.1") else self._host
        return f"http://{shown}:{self._port}"

    async def start(self) -> None:
        app = web.Application()
        app.add_routes(
            [
                web.get("/", self._handle_index),
                web.get("/ws", self._handle_ws),
                web.get("/static/{name}", self._handle_static),
                web.get("/meta", self._handle_meta),
                web.get("/i18n/{lang}", self._handle_i18n),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    def request_stop(self) -> None:
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def cleanup(self) -> None:
        if self._broadcast_task is not None:
            self._broadcast_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._broadcast_task
            self._broadcast_task = None
        for ws in list(self._clients):
            with suppress(Exception):
                await ws.close()
        self._clients.clear()
        if self._site is not None:
            with suppress(Exception):
                await self._site.stop()
            self._site = None
        if self._runner is not None:
            with suppress(Exception):
                await self._runner.cleanup()
            self._runner = None

    # routes ------------------------------------------------------------------
    _NO_STORE = {"Cache-Control": "no-store"}

    async def _handle_index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC_DIR / "index.html", headers=self._NO_STORE)

    async def _handle_static(self, request: web.Request) -> web.StreamResponse:
        # Serve app.css / app.js with no-store so edits are always picked up
        # (a stale cached stylesheet was leaving the modal overlay on screen).
        name = request.match_info["name"]
        if "/" in name or "\\" in name or ".." in name:
            raise web.HTTPNotFound()
        path = STATIC_DIR / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers=self._NO_STORE)

    async def _handle_meta(self, request: web.Request) -> web.StreamResponse:
        from tdm_cli.web import i18n
        from tdm_cli import versioning

        info = versioning.version_info()
        return web.json_response(
            {
                # app version (TDMConsole itself) and the bundled engine
                # (TwitchDropsMiner) version + pinned commit, kept separate.
                "version": info["app"],
                "engine": info["engine"],
                "engineCommit": info["engineCommit"],
                "repo": REPO_URL,
                "languages": i18n.available_languages(),
                "default": i18n.default_language(),
            },
            headers=self._NO_STORE,
        )

    async def _handle_i18n(self, request: web.Request) -> web.StreamResponse:
        from tdm_cli.web import i18n

        lang = request.match_info["lang"]
        return web.json_response(i18n.strings_for(lang), headers=self._NO_STORE)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._clients.add(ws)
        # Prime the new client with a full snapshot + the whole log backlog.
        with suppress(Exception):
            await ws.send_json({"type": "state", "data": snapshot(self._m)})
            await ws.send_json(_log_payload(list(self._m.state.log_lines)))
        try:
            async for msg in ws:
                if msg.type is WSMsgType.TEXT:
                    self._dispatch(msg.data)
                elif msg.type is WSMsgType.ERROR:
                    break
        finally:
            self._clients.discard(ws)
        return ws

    def _dispatch(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            return
        kind = data.get("type")
        if kind == "command":
            text = str(data.get("text", "")).strip()
            if text:
                self._processor.dispatch(text)
        elif kind == "action":
            name = data.get("name")
            if name == "login-hide":
                # User dismissed the login modal in the browser; keep polling.
                self._m.state.login_prompt = False

    # broadcast ---------------------------------------------------------------
    async def _broadcast_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.5)
                if not self._clients:
                    # keep the log cursor moving so a future client isn't flooded
                    if self._m.state.log_lines:
                        self._log_cursor = self._m.state.log_lines[-1].seq
                    continue
                state_msg = {"type": "state", "data": snapshot(self._m)}
                new_logs = self._m.state.logs_since(self._log_cursor)
                if new_logs:
                    self._log_cursor = new_logs[-1].seq
                    log_msg = _log_payload(new_logs)
                else:
                    log_msg = None
                dead: list[web.WebSocketResponse] = []
                for ws in self._clients:
                    try:
                        await ws.send_json(state_msg)
                        if log_msg is not None:
                            await ws.send_json(log_msg)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self._clients.discard(ws)
        except asyncio.CancelledError:
            raise
