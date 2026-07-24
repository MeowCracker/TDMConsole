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
import hashlib
import logging
import os
import secrets
import subprocess
import time
from datetime import datetime, timezone
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
REPO_URL = "https://github.com/MeowCracker/TDMConsole"
SESSION_COOKIE = "tdmconsole_session"
SESSION_TTL_SECONDS = 3 * 24 * 60 * 60
_AUTH_EXPIRES_AT = web.RequestKey("auth_expires_at", float)
_PUBLIC_PATHS = frozenset(
    {
        "/login",
        "/healthcheck",
        "/static/app.css",
        "/static/favicon.png",
        "/static/login.css",
        "/static/login.js",
    }
)


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, remainder = divmod(total, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _format_bytes(value: int, *, precision: int | None = None) -> str:
    amount = max(0, value)
    for unit in ("B", "K", "M", "G", "T"):
        if amount < 1024 or unit == "T":
            if precision is not None:
                return f"{amount:.{precision}f}{unit}"
            if unit == "B" or amount >= 10 or amount.is_integer():
                return f"{amount:.0f}{unit}"
            return f"{amount:.1f}{unit}"
        amount /= 1024
    return "0B"


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
        return int(value)
    except (OSError, ValueError):
        return None


def _cgroup_vcpu_limit() -> float | None:
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()
        if quota != "max" and int(period) > 0:
            return int(quota) / int(period)
    except (OSError, ValueError):
        pass

    quota = _read_int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"))
    period = _read_int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
    if quota is not None and period is not None and quota > 0 and period > 0:
        return quota / period
    return None


def _system_memory_limit() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return 0


def _cgroup_memory_limit() -> int:
    for path in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            value = path.read_text(encoding="ascii").strip()
            if value != "max":
                limit = int(value)
                if limit > 0:
                    return limit
        except (OSError, ValueError):
            continue
    return _system_memory_limit()


def _process_rss_bytes() -> int:
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        return int(result.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _cache_size_bytes() -> int:
    try:
        from constants import CACHE_PATH

        cache_path = Path(CACHE_PATH)
    except (ImportError, AttributeError):
        cache_path = Path(os.environ.get("TDM_DATA_DIR", "."), "cache")

    total = 0
    try:
        for path in cache_path.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


async def _process_vcpu_usage() -> float:
    start_cpu = time.process_time()
    start_wall = time.monotonic()
    await asyncio.sleep(0.1)
    elapsed = time.monotonic() - start_wall
    if elapsed <= 0:
        return 0.0
    return max(0.0, (time.process_time() - start_cpu) / elapsed)


def _runtime_payload(started_at: float, process_vcpus: float) -> dict[str, Any]:
    from tdm_cli import versioning

    now = time.time()
    version = versioning.version_info()
    vcpu_limit = _cgroup_vcpu_limit() or float(os.cpu_count() or 1)
    rss = _process_rss_bytes()
    memory_limit = _cgroup_memory_limit()
    cache_size = _cache_size_bytes()
    return {
        "status": "ok",
        "uptime": _format_duration(now - started_at),
        "uptimeSeconds": max(0, int(now - started_at)),
        "startedAt": datetime.fromtimestamp(started_at, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "version": version["app"],
        "engine": {
            "version": version["engine"],
            "commit": version["engineCommit"],
        },
        "cpu": {
            "usage": f"{process_vcpus:.1f}/{vcpu_limit:.1f} vCPU",
            "usedVcpu": round(process_vcpus, 3),
            "limitVcpu": vcpu_limit,
        },
        "memory": {
            "usage": (
                f"{_format_bytes(rss, precision=2)}/"
                f"{_format_bytes(memory_limit, precision=2)}"
            ),
            "usedBytes": rss,
            "limitBytes": memory_limit,
        },
        "cache": {
            "size": _format_bytes(cache_size),
            "sizeBytes": cache_size,
        },
    }


class _MemorySessions:
    """Process-local WebUI sessions; a process restart invalidates every token."""

    def __init__(self, username: str, password: str) -> None:
        if not username or not password:
            raise ValueError("WebUI username and password cannot be empty")
        if ":" in username:
            raise ValueError("WebUI username cannot contain ':'")
        self._username = self._digest(username)
        self._password = self._digest(password)
        self._sessions: dict[str, float] = {}

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()

    def create(self, username: str, password: str) -> str | None:
        username_ok = secrets.compare_digest(self._digest(username), self._username)
        password_ok = secrets.compare_digest(self._digest(password), self._password)
        if not (username_ok and password_ok):
            return None
        now = time.monotonic()
        self._purge(now)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + SESSION_TTL_SECONDS
        return token

    def validate(self, token: str | None) -> float | None:
        if not token:
            return None
        expires_at = self._sessions.get(token)
        if expires_at is None:
            return None
        if expires_at <= time.monotonic():
            self._sessions.pop(token, None)
            return None
        return expires_at

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def _purge(self, now: float) -> None:
        for token, expires_at in list(self._sessions.items()):
            if expires_at <= now:
                self._sessions.pop(token, None)


def _session_auth_middleware(sessions: _MemorySessions):
    @web.middleware
    async def require_session(request: web.Request, handler):
        expires_at = sessions.validate(request.cookies.get(SESSION_COOKIE))
        if expires_at is not None:
            request[_AUTH_EXPIRES_AT] = expires_at
        if request.path in _PUBLIC_PATHS:
            return await handler(request)
        if expires_at is None:
            if request.method == "GET" and request.path == "/":
                return web.HTTPFound("/login?next=/")
            return web.json_response(
                {"error": "authentication_required"},
                status=401,
                headers={"Cache-Control": "no-store"},
            )
        return await handler(request)

    return require_session


def _login_response(token: str, *, secure: bool) -> web.Response:
    response = web.json_response(
        {"ok": True, "expiresIn": SESSION_TTL_SECONDS},
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="Strict",
        path="/",
    )
    return response


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
        "engineUpdating": manager.engine_update_running,
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
            _campaign_snapshot(c)
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


def _campaign_snapshot(campaign: Any) -> dict[str, Any]:
    """Return the campaign details needed by the WebUI, without Twitch internals."""
    return {
        "game": campaign.game.name,
        "name": campaign.name,
        "claimed": campaign.claimed_drops,
        "total": campaign.total_drops,
        "progress": campaign.progress,
        "active": campaign.active,
        "upcoming": campaign.upcoming,
        "drops": [
            {
                "rewards": [
                    {"name": benefit.name, "image": str(benefit.image_url)}
                    for benefit in drop.benefits
                ],
                "claimed": drop.is_claimed,
                "progress": drop.progress,
                "currentMinutes": drop.current_minutes,
                "requiredMinutes": drop.required_minutes,
            }
            for drop in campaign.drops
        ],
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
    def __init__(
        self,
        manager: GUIManager,
        host: str,
        port: int,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        if (username is None) != (password is None):
            raise ValueError("WebUI username and password must be provided together")
        self._m = manager
        self._host = host
        self._port = port
        self._sessions = (
            _MemorySessions(username, password)
            if username is not None and password is not None
            else None
        )
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._broadcast_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._log_cursor = 0
        self._started_at = time.time()
        # Command output is folded into the shared miner log so every client sees it.
        self._processor = CommandProcessor(
            manager, lambda text, style="": manager.state.add_log(text, style)
        )

    @property
    def url(self) -> str:
        shown = "localhost" if self._host in ("0.0.0.0", "127.0.0.1") else self._host
        return f"http://{shown}:{self._port}"

    async def start(self) -> None:
        middlewares = (
            [_session_auth_middleware(self._sessions)] if self._sessions else []
        )
        app = web.Application(middlewares=middlewares)
        app.add_routes(
            [
                web.get("/", self._handle_index),
                web.get("/login", self._handle_login_page),
                web.post("/login", self._handle_login),
                web.post("/logout", self._handle_logout),
                web.get("/session", self._handle_session),
                web.get("/ws", self._handle_ws),
                web.get("/static/{name}", self._handle_static),
                web.get("/healthcheck", self._handle_healthcheck),
                web.get("/runtime", self._handle_runtime),
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

    async def _handle_login_page(self, request: web.Request) -> web.StreamResponse:
        if self._sessions is None or _AUTH_EXPIRES_AT in request:
            raise web.HTTPFound("/")
        return web.FileResponse(STATIC_DIR / "login.html", headers=self._NO_STORE)

    async def _handle_login(self, request: web.Request) -> web.StreamResponse:
        if self._sessions is None:
            raise web.HTTPNotFound()
        try:
            payload = await request.json()
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "invalid_request"},
                status=400,
                headers=self._NO_STORE,
            )
        if not isinstance(payload, dict):
            return web.json_response(
                {"error": "invalid_request"},
                status=400,
                headers=self._NO_STORE,
            )
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            token = None
        else:
            token = self._sessions.create(username, password)
        if token is None:
            return web.json_response(
                {"error": "invalid_credentials"},
                status=401,
                headers=self._NO_STORE,
            )
        return _login_response(token, secure=request.secure)

    async def _handle_logout(self, request: web.Request) -> web.StreamResponse:
        if self._sessions is None:
            raise web.HTTPNotFound()
        self._sessions.revoke(request.cookies.get(SESSION_COOKIE))
        response = web.json_response({"ok": True}, headers=self._NO_STORE)
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    async def _handle_session(self, request: web.Request) -> web.StreamResponse:
        expires_at = request.get(_AUTH_EXPIRES_AT)
        expires_in = (
            max(0, int(expires_at - time.monotonic()))
            if isinstance(expires_at, float)
            else None
        )
        return web.json_response(
            {"ok": True, "expiresIn": expires_in},
            headers=self._NO_STORE,
        )

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
                "authEnabled": self._sessions is not None,
                "languages": i18n.available_languages(),
                "default": i18n.default_language(),
            },
            headers=self._NO_STORE,
        )

    async def _handle_healthcheck(self, request: web.Request) -> web.StreamResponse:
        return web.Response(
            text="ok",
            content_type="text/plain",
            headers=self._NO_STORE,
        )

    async def _handle_runtime(self, request: web.Request) -> web.StreamResponse:
        process_vcpus = await _process_vcpu_usage()
        return web.json_response(
            _runtime_payload(self._started_at, process_vcpus),
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
        expiry_task: asyncio.Task[None] | None = None
        expires_at = request.get(_AUTH_EXPIRES_AT)
        if isinstance(expires_at, float):
            expiry_task = asyncio.create_task(self._expire_websocket(ws, expires_at))
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
            if expiry_task is not None:
                expiry_task.cancel()
                with suppress(asyncio.CancelledError):
                    await expiry_task
            self._clients.discard(ws)
        return ws

    @staticmethod
    async def _expire_websocket(ws: web.WebSocketResponse, expires_at: float) -> None:
        await asyncio.sleep(max(0.0, expires_at - time.monotonic()))
        if not ws.closed:
            await ws.close(code=4401, message=b"session expired")

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
