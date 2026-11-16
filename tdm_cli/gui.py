"""CLI drop-in replacement for the upstream tkinter ``GUIManager``.

:mod:`tdm_cli.bootstrap` installs this module as ``sys.modules["gui"]`` before
``twitch`` is imported, so ``from gui import GUIManager`` inside the pristine
upstream code resolves *here* instead of the tkinter implementation.

The public surface below is the **contract** with the upstream core. It mirrors
every ``self.gui.*`` / ``.gui.<component>.*`` access made by ``twitch.py``,
``channel.py``, ``websocket.py``, ``inventory.py`` and ``cache.py`` as of the
pinned submodule commit. If a future upstream bump changes this surface, the
self-check in :func:`tdm_cli.bootstrap.verify_contract` will point at what moved
and only this file needs updating — the submodule itself is never modified.

Contract (component -> members used by the core):
  GUIManager   __init__(twitch), close_requested, running, wait_until_closed(),
               coro_unless_closed(coro), prevent_close(), start(), stop(),
               close(*args), close_window(), save(force=), grab_attention(sound=),
               set_games(games), display_drop(drop, countdown=, subone=),
               clear_drop(), print(message)
  .status      update(text), clear()
  .tray        change_icon(state), notify(message, title), update_title(drop),
               stop(), restore(), minimize()
  .login       ask_enter_code(url, code), ask_login(), update(status, uid), clear(...)
  .progress    start_timer(), stop_timer(), minute_almost_done(),
               display(drop, countdown=, subone=)
  .channels    clear(), get_selection(), set_watching(ch), clear_watching(),
               display(ch, add=), remove(ch), clear_selection()
  .inv         clear(), add_campaign(campaign) [async], update_drop(drop)
  .websockets  update(idx, status=, topics=), remove(idx)
  .settings    set_games(games), clear_selection()
  .help        _invalidate_button.config(state=...)
  .output      print(message)

Presentation is delegated to a *frontend* object (``manager.frontend``):
``HeadlessFrontend`` prints log lines (for servers / ``--no-tui``), while
``tdm_cli.tui.TextualFrontend`` runs a full-screen dashboard. ``main.py`` picks
one by assigning the module-level ``FRONTEND_FACTORY`` before ``Twitch(...)``
is constructed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from time import monotonic, time
from typing import TYPE_CHECKING, Any, Callable, TypeVar

import aiohttp
from constants import OUTPUT_FORMATTER
from exceptions import ExitRequest
from translate import _

from tdm_cli import console
from tdm_cli.console import Color
from tdm_cli.schedule import (
    CronError,
    ScheduleError,
    engine_bootstrap_done as schedule_bootstrap_done,
    load_engine_update_cron,
    mark_engine_bootstrap_done,
    parse_cron,
    store_engine_update_cron,
)
from tdm_cli.state import MinerState

if TYPE_CHECKING:
    from collections import abc

    from yarl import URL

    from twitch import Twitch
    from channel import Channel
    from constants import Game
    from inventory import TimedDrop, DropsCampaign

_T = TypeVar("_T")
logger = logging.getLogger("TwitchDrops")

_RECONNECT_RESTART_DELAY = 180.0
_RECONNECT_RESTART_FAILURES = 3
_RECONNECT_DELAY_TOLERANCE = 1.0
# How often the engine-update schedule compares the clock against the cron
# expression. Cron resolution is one minute, so this only adds a little jitter.
_ENGINE_SCHEDULE_POLL_SECONDS = 20.0
_PROCESS_STARTED_AT_ENV = "TDMCONSOLE_PROCESS_STARTED_AT"
_LAST_RESTART_AT_ENV = "TDMCONSOLE_LAST_RESTART_AT"


def _read_runtime_timestamp(name: str) -> float | None:
    try:
        value = float(os.environ[name])
    except (KeyError, ValueError):
        return None
    return value if value > 0 else None


def _runtime_timestamps() -> tuple[float, float | None]:
    started_at = _read_runtime_timestamp(_PROCESS_STARTED_AT_ENV)
    if started_at is None:
        started_at = time()
        os.environ[_PROCESS_STARTED_AT_ENV] = repr(started_at)
    return started_at, _read_runtime_timestamp(_LAST_RESTART_AT_ENV)


def mark_process_restart() -> None:
    """Record a full-process restart for the replacement process to inherit."""
    os.environ[_LAST_RESTART_AT_ENV] = repr(time())


# Interface-mode -> frontend factory. "tui"/"repl" factories import their heavy
# deps lazily so this module stays importable on a box without textual
# installed. "headless" is always available.
FRONTEND_REGISTRY: dict[str, Callable[["GUIManager"], Any]] = {}


def register_frontend(mode: str, factory: Callable[["GUIManager"], Any]) -> None:
    FRONTEND_REGISTRY[mode] = factory


def make_frontend(mode: str, manager: GUIManager) -> Any:
    factory = FRONTEND_REGISTRY.get(mode)
    if factory is None:
        logger.warning(
            "No frontend registered for mode %r — falling back to headless", mode
        )
        factory = FRONTEND_REGISTRY["headless"]
    return factory(manager)


# --------------------------------------------------------------------------- #
# Frontends
# --------------------------------------------------------------------------- #
class HeadlessFrontend:
    """Plain line-oriented output — servers, containers, ``--no-tui``."""

    def __init__(self, manager: GUIManager) -> None:
        self._manager = manager

    # lifecycle -------------------------------------------------------------
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    async def wait_stopped(self) -> None:
        pass

    def close_window(self) -> None:
        pass

    def attention(self) -> None:
        console.bell()

    # event hooks -----------------------------------------------------------
    _STYLE_COLORS = {"error": Color.RED, "warn": Color.YELLOW, "notify": Color.YELLOW}

    def log(self, text: str, style: str = "") -> None:
        console.emit(text, color=self._STYLE_COLORS.get(style))

    def on_status(self, text: str) -> None:
        console.emit(f"[status] {text}", color=Color.CYAN)

    def on_login(self, status: str, user_id: int | None) -> None:
        suffix = f" (user {user_id})" if user_id is not None else ""
        console.emit(f"[login] {status}{suffix}", color=Color.GREEN)

    def on_websocket(self, idx: int, status: str) -> None:
        console.emit(f"[websocket {idx + 1}] {status}", color=Color.DIM)

    def on_watching(self, channel: Channel) -> None:
        game = channel.game.name if channel.game is not None else "?"
        console.emit(f"[watching] {channel.name} ({game})", color=Color.GREEN)

    def on_drop(self, line: str) -> None:
        console.emit(f"[drop] {line}", color=Color.BLUE)

    def show_login(self, page_url: str, user_code: str) -> None:
        console.banner(
            [
                "Twitch login required",
                "",
                "1. Open this URL in a browser (any device):",
                f"     {page_url}",
                "2. Enter this code:",
                f"     {user_code}",
                "",
                "Mining starts automatically once you authorize.",
            ],
            color=Color.BOLD,
        )
        if console.INTERACTIVE:
            try:
                webbrowser.open(page_url)
            except Exception:
                pass

    def on_login_available(self, page_url: str, user_code: str, first: bool) -> None:
        # Headless has no way to "trigger" login, so it always shows the code.
        if first:
            self.show_login(page_url, user_code)
        else:
            console.emit(
                f"Login code refreshed: {user_code}  ({page_url})", color=Color.YELLOW
            )

    def hide_login(self) -> None:
        pass


# The interface mode used for the *initial* frontend; main.py sets this before
# Twitch() is constructed. Live switching goes through GUIManager.request_frontend.
ACTIVE_MODE: str = "headless"


def _lazy_tui(manager: "GUIManager") -> Any:
    from tdm_cli.tui import TextualFrontend

    return TextualFrontend(manager)


def _lazy_repl(manager: "GUIManager") -> Any:
    from tdm_cli.repl import ReplFrontend

    return ReplFrontend(manager)


def _lazy_web(manager: "GUIManager") -> Any:
    from tdm_cli.web import WebFrontend

    return WebFrontend(manager)


register_frontend("tui", _lazy_tui)
register_frontend("repl", _lazy_repl)
register_frontend("web", _lazy_web)
register_frontend("headless", HeadlessFrontend)


class _ConsoleLogHandler(logging.Handler):
    """Route ``TwitchDrops`` logger records into the miner state + frontend."""

    def __init__(self, manager: GUIManager) -> None:
        super().__init__()
        self._manager = manager

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            self.handleError(record)
            return
        if record.levelno >= logging.ERROR:
            style = "error"
        elif record.levelno >= logging.WARNING:
            style = "warn"
        else:
            style = ""
        self._manager.state.add_log(message, style)
        self._manager.frontend.log(message, style)


class _NoopWidget:
    """Stand-in for a tkinter widget; swallows ``.config(...)`` etc. as no-ops."""

    def config(self, *args: Any, **kwargs: Any) -> None:
        pass

    configure = config

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __getattr__(self, _name: str) -> _NoopWidget:
        return self


# --------------------------------------------------------------------------- #
# Main-tab components
# --------------------------------------------------------------------------- #
class StatusBar:
    def __init__(self, manager: GUIManager):
        self._manager = manager

    def update(self, text: str) -> None:
        state = self._manager.state
        if text == state.status:
            return
        state.status = text
        self._manager.frontend.on_status(text)

    def clear(self) -> None:
        self._manager.state.status = ""


class WebsocketStatus:
    def __init__(self, manager: GUIManager):
        self._manager = manager

    def update(
        self, idx: int, status: str | None = None, topics: int | None = None
    ) -> None:
        ws = self._manager.state.websockets
        old_status, old_topics = ws.get(idx, ("", 0))
        new_status = status if status is not None else old_status
        new_topics = topics if topics is not None else old_topics
        ws[idx] = (new_status, new_topics)
        # Only announce connection-state changes; topic-count churn stays quiet.
        if status is not None and status != old_status:
            self._manager.frontend.on_websocket(idx, status)

    def remove(self, idx: int) -> None:
        self._manager.state.websockets.pop(idx, None)


@dataclass
class LoginData:
    username: str
    password: str
    token: str


class LoginForm:
    def __init__(self, manager: GUIManager):
        self._manager = manager

    def update(self, status: str, user_id: int | None) -> None:
        state = self._manager.state
        state.login_status = status
        state.user_id = user_id
        if user_id is not None:
            # Authorized — retire any pending login prompt.
            state.login_available = False
            if state.login_prompt:
                state.login_prompt = False
                self._manager.frontend.hide_login()
        self._manager.frontend.on_login(status, user_id)

    def clear(
        self, login: bool = False, password: bool = False, token: bool = False
    ) -> None:
        # No stored input fields in CLI; nothing to clear.
        pass

    async def ask_enter_code(self, page_url: URL, user_code: str) -> None:
        """Primary (device-code) login. Non-blocking, and — crucially — it does
        NOT pop a prompt on its own. It records the (rotating) code and marks a
        login as available; the user reveals it via /login or the dashboard's
        Login action. Headless has no way to trigger, so it prints immediately.
        """
        state = self._manager.state
        first = not state.login_available
        state.login_url = str(page_url)
        state.login_code = user_code
        state.login_status = _("gui", "login", "required")
        state.login_available = True
        self._manager.frontend.on_login_available(str(page_url), user_code, first)

    async def ask_login(self) -> LoginData:
        """Legacy username/password flow (rarely reached). Prompts on the TTY
        without blocking the event loop."""
        self.update(_("gui", "login", "required"), None)
        self._manager.grab_attention(sound=False)
        loop = asyncio.get_running_loop()

        async def prompt(text: str, *, secret: bool = False) -> str:
            def _read() -> str:
                if secret:
                    import getpass

                    return getpass.getpass(text)
                return input(text)

            return (await loop.run_in_executor(None, _read)).strip()

        while True:
            username = await prompt("Twitch username: ")
            password = await prompt("Twitch password: ", secret=True)
            token = await prompt("2FA code (blank if none): ")
            if len(username) < 3 or len(password) < 8:
                self._manager.print("Invalid username/password, try again.")
                continue
            return LoginData(username, password, token)


class CampaignProgress:
    """Faithful port of the upstream countdown state machine (drives the mine
    loop's ``minute_almost_done`` timing), rendering into MinerState."""

    ALMOST_DONE_SECONDS = 10

    def __init__(self, manager: GUIManager):
        self._manager = manager
        self._drop: TimedDrop | None = None
        self._seconds: int = 0
        self._timer_task: asyncio.Task[None] | None = None

    def _divmod(self, minutes: int) -> tuple[int, int]:
        if self._seconds < 60 and minutes > 0:
            minutes -= 1
        hours, minutes = divmod(minutes, 60)
        return (hours, minutes)

    def _update_time(self, seconds: int | None = None) -> None:
        if seconds is not None:
            self._seconds = seconds
        drop = self._drop
        state = self._manager.state
        if drop is not None:
            drop_minutes = drop.remaining_minutes
            campaign_minutes = drop.campaign.remaining_minutes
        else:
            drop_minutes = 0
            campaign_minutes = 0
        dseconds = self._seconds % 60
        hours, minutes = self._divmod(drop_minutes)
        state.drop_remaining = f"{hours}:{minutes:02}:{dseconds:02}"
        hours, minutes = self._divmod(campaign_minutes)
        state.campaign_remaining = f"{hours}:{minutes:02}:{dseconds:02}"

    async def _timer_loop(self) -> None:
        self._update_time(60)
        while self._seconds > 0:
            await asyncio.sleep(1)
            self._seconds -= 1
            self._update_time()
        self._timer_task = None

    def start_timer(self) -> None:
        if self._timer_task is None:
            if self._drop is None or self._drop.remaining_minutes <= 0:
                self._update_time(60)
            else:
                self._timer_task = asyncio.create_task(self._timer_loop())

    def stop_timer(self) -> None:
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None

    def minute_almost_done(self) -> bool:
        return self._timer_task is None or self._seconds <= self.ALMOST_DONE_SECONDS

    def display(
        self, drop: TimedDrop | None, *, countdown: bool = True, subone: bool = False
    ) -> None:
        self._drop = drop
        self.stop_timer()
        state = self._manager.state
        if drop is None:
            state.clear_drop()
            self._update_time(0)
            return
        campaign = drop.campaign
        state.drop_rewards = drop.rewards_text()
        state.drop_progress = drop.progress
        state.campaign_name = campaign.name
        state.campaign_game = campaign.game.name
        state.campaign_progress = campaign.progress
        state.campaign_claimed = campaign.claimed_drops
        state.campaign_total = campaign.total_drops
        self._manager.frontend.on_drop(
            f"{campaign.game.name} — {drop.rewards_text()} | "
            f"drop {drop.progress:>5.1%} | "
            f"campaign {campaign.progress:>5.1%} "
            f"({campaign.claimed_drops}/{campaign.total_drops})"
        )
        if countdown:
            self.start_timer()
        elif subone:
            self._update_time(0)
        else:
            self._update_time(60)


class ConsoleOutput:
    def __init__(self, manager: GUIManager):
        self._manager = manager

    def print(self, message: str) -> None:
        self._manager.state.add_log(message)
        self._manager.frontend.log(message)


class ChannelList:
    def __init__(self, manager: GUIManager):
        self._manager = manager
        self._selection: Channel | None = None

    def _bump(self) -> None:
        self._manager.state.channels_rev += 1

    def clear(self) -> None:
        self._selection = None
        self._bump()

    def select(self, channel: Channel) -> None:
        """TUI extension: pin a channel; the core checks it first on switches."""
        self._selection = channel
        self._bump()

    def get_selection(self) -> Channel | None:
        return self._selection

    def clear_selection(self) -> None:
        self._selection = None
        self._bump()

    def set_watching(self, channel: Channel) -> None:
        state = self._manager.state
        state.watching_channel = channel.name
        state.watching_game = channel.game.name if channel.game is not None else ""
        self._bump()
        self._manager.frontend.on_watching(channel)

    def clear_watching(self) -> None:
        state = self._manager.state
        state.watching_channel = ""
        state.watching_game = ""
        self._bump()

    def display(self, channel: Channel, *, add: bool = False) -> None:
        self._bump()

    def remove(self, channel: Channel) -> None:
        if self._selection is channel:
            self._selection = None
        self._bump()


class TrayIcon:
    def __init__(self, manager: GUIManager):
        self._manager = manager

    def change_icon(self, state: str) -> None:
        # Human-readable status is already surfaced via StatusBar; keep quiet.
        pass

    def update_title(self, drop: TimedDrop | None) -> None:
        pass

    def notify(self, message: str, title: str | None = None) -> None:
        self._manager.state.add_log(message, "notify")
        self._manager.frontend.log(message, "notify")

    def stop(self) -> None:
        pass

    def restore(self) -> None:
        pass

    def minimize(self) -> None:
        pass

    def quit(self) -> None:
        pass


class InventoryOverview:
    def __init__(self, manager: GUIManager):
        self._manager = manager

    def _bump(self) -> None:
        self._manager.state.inventory_rev += 1

    async def add_campaign(self, campaign: DropsCampaign) -> None:
        self._bump()

    def clear(self) -> None:
        self._bump()

    def update_drop(self, drop: TimedDrop) -> None:
        self._bump()


class SettingsPanel:
    def __init__(self, manager: GUIManager):
        self._manager = manager

    def set_games(self, games: set[Game]) -> None:
        # Games become available for the TUI games screen via twitch.inventory;
        # nothing to store here.
        pass

    def clear_selection(self) -> None:
        pass


class HelpTab:
    def __init__(self, manager: GUIManager):
        self._manager = manager
        # ``_AuthState`` toggles this button's state; a no-op widget absorbs it.
        self._invalidate_button = _NoopWidget()

    def invalidate_token(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# The manager
# --------------------------------------------------------------------------- #
class GUIManager:
    def __init__(self, twitch: Twitch):
        self._twitch: Twitch = twitch
        self._close_requested = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None

        self.state = MinerState()
        self.process_started_at, self.last_restart_at = _runtime_timestamps()
        self.mode = ACTIVE_MODE
        self.frontend = make_frontend(self.mode, self)
        self._switch_task: asyncio.Task[None] | None = None
        self._engine_update_task: asyncio.Task[None] | None = None
        self._engine_update_result: str | None = None
        # Why the running engine update was started ("reconnect" / "schedule" /
        # None for a user request) — decides what to do when it changes nothing.
        self._engine_update_reason: str | None = None
        self._engine_update_cron = load_engine_update_cron()
        self._schedule_task: asyncio.Task[None] | None = None
        self._restart_requested = False
        self._restart_task: asyncio.Task[None] | None = None
        self._last_connection_failure_at: float | None = None
        self._long_reconnect_failures = 0

        # Components (order-independent; none touch a display)
        self.status = StatusBar(self)
        self.websockets = WebsocketStatus(self)
        self.login = LoginForm(self)
        self.progress = CampaignProgress(self)
        self.output = ConsoleOutput(self)
        self.channels = ChannelList(self)
        self.inv = InventoryOverview(self)
        self.settings = SettingsPanel(self)
        self.tray = TrayIcon(self)
        self.help = HelpTab(self)

        # Mirror the upstream behaviour of surfacing app logs on-screen.
        self._handler = _ConsoleLogHandler(self)
        self._handler.setFormatter(OUTPUT_FORMATTER)
        logger.addHandler(self._handler)

    @property
    def running(self) -> bool:
        return self._poll_task is not None

    @property
    def close_requested(self) -> bool:
        return self._close_requested.is_set()

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    @property
    def engine_update_running(self) -> bool:
        return self._engine_update_task is not None

    @property
    def engine_update_result(self) -> str | None:
        return self._engine_update_result

    @property
    def engine_update_available(self) -> bool:
        """Whether the engine can be refreshed in place.

        Source checkouts and the Docker image update the engine on disk; a
        frozen executable bundles it and must be replaced as a whole.
        """
        return not getattr(sys, "frozen", False)

    @property
    def engine_update_cron(self) -> str:
        """Cron expression driving scheduled engine updates; '' = disabled."""
        return getattr(self, "_engine_update_cron", "")

    @property
    def engine_bootstrap_done(self) -> bool:
        """Whether this data dir already had its one-off first-launch refresh."""
        return schedule_bootstrap_done()

    def set_engine_update_cron(self, expression: str) -> str:
        """Validate, persist and apply a schedule expression ('' turns it off).

        Raises :class:`tdm_cli.schedule.CronError` for bad text and
        :class:`tdm_cli.schedule.ScheduleError` when the sidecar file cannot be
        written — both are ``ValueError`` subclasses, so command handlers can
        report them directly.
        """
        normalized = store_engine_update_cron(expression)
        self._engine_update_cron = normalized
        self._update_log(
            f"Engine update schedule set to {normalized}."
            if normalized
            else "Engine update schedule disabled.",
            "info",
        )
        return normalized

    def next_engine_update_at(self, now: datetime | None = None) -> str | None:
        """Local ISO minute stamp of the next scheduled run, if any."""
        expression = self.engine_update_cron
        if not expression:
            return None
        try:
            upcoming = parse_cron(expression).next_run(now or datetime.now())
        except CronError:
            return None
        return upcoming.isoformat(timespec="minutes") if upcoming else None

    def _maybe_bootstrap_engine(self) -> bool:
        """Refresh the engine once on a fresh deployment.

        A newly deployed image or checkout can ship a pinned engine that is
        already behind Twitch's API; one update on first launch is cheaper than
        waiting for three failed reconnects to notice.
        """
        if not self.engine_update_available or self.engine_bootstrap_done:
            return False
        if self._engine_update_task is not None or self._restart_requested:
            return False
        self._update_log(
            "First start on this data dir — refreshing the engine...", "info"
        )
        return self.request_engine_update(reason="first-run")

    def _mark_engine_bootstrap(self) -> None:
        """Persist the first-launch flag; a read-only data dir must not break mining."""
        try:
            mark_engine_bootstrap_done()
        except ScheduleError as exc:
            logger.warning("Could not record the first engine refresh: %s", exc)

    async def wait_until_closed(self) -> None:
        # Doubles as an interruptible sleep: twitch.py awaits this with a timeout
        # so a close request wakes the mine loop early.
        await self._close_requested.wait()

    async def coro_unless_closed(self, coro: abc.Awaitable[_T]) -> _T:
        started_at = monotonic()
        retry_delay = (
            None
            if self._last_connection_failure_at is None
            else started_at - self._last_connection_failure_at
        )
        tasks: list[asyncio.Task[Any]] = [
            asyncio.ensure_future(coro),
            asyncio.ensure_future(self._close_requested.wait()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if self._close_requested.is_set():
            raise ExitRequest()
        try:
            result = await next(iter(done))
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            self._record_connection_failure(retry_delay)
            raise
        self._reset_connection_failures()
        return result

    def _record_connection_failure(self, retry_delay: float | None) -> None:
        self._last_connection_failure_at = monotonic()
        if (
            retry_delay is None
            or retry_delay < _RECONNECT_RESTART_DELAY - _RECONNECT_DELAY_TOLERANCE
        ):
            self._long_reconnect_failures = 0
            return

        self._long_reconnect_failures += 1
        if self._long_reconnect_failures < _RECONNECT_RESTART_FAILURES:
            return

        self._long_reconnect_failures = 0
        self._recover_from_repeated_failures()

    def _recover_from_repeated_failures(self) -> None:
        """Twitch stayed unreachable across the configured long retries.

        A pinned engine that no longer matches Twitch's API is the usual cause,
        so refresh it first — the update path restarts the process itself. When
        updates are unavailable (frozen build, no Git, update already running)
        fall back to the plain restart this used to do unconditionally.
        """
        message = f"Twitch reconnect failed {_RECONNECT_RESTART_FAILURES} times"
        if (
            self.engine_update_available
            and getattr(self, "_engine_update_task", None) is None
            and self.request_engine_update(reason="reconnect")
        ):
            self._update_log(
                f"{message}; refreshing the engine before the next restart.",
                "warn",
            )
            return
        self.request_restart(f"{message}; restarting TDMConsole...", "error")

    def _reset_connection_failures(self) -> None:
        self._last_connection_failure_at = None
        self._long_reconnect_failures = 0

    def prevent_close(self) -> None:
        self._close_requested.clear()

    def start(self) -> None:
        # No GUI event loop to pump; a parked task keeps ``running`` truthful and
        # gives ``stop()`` something symmetric to cancel.
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll())
        if getattr(self, "_schedule_task", None) is None:
            self._schedule_task = asyncio.create_task(self._engine_schedule_loop())
        self.frontend.start()
        self._maybe_bootstrap_engine()

    async def _poll(self) -> None:
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self._poll_task = None
            raise

    def stop(self) -> None:
        self.progress.stop_timer()
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        if getattr(self, "_schedule_task", None) is not None:
            self._schedule_task.cancel()
            self._schedule_task = None
        self.frontend.stop()

    async def _engine_schedule_loop(self) -> None:
        """Fire scheduled engine updates (cron expression from tdm-cli.json).

        Polling instead of sleeping until the next run keeps the loop honest
        across clock jumps, host suspend and live schedule edits; cron has
        minute resolution, so the added latency is imperceptible.
        """
        last_expression: str | None = None
        last_fired: str | None = None
        try:
            while True:
                await asyncio.sleep(_ENGINE_SCHEDULE_POLL_SECONDS)
                expression = self.engine_update_cron
                if expression != last_expression:
                    # A new schedule may legitimately fire in this same minute.
                    last_expression = expression
                    last_fired = None
                if not expression:
                    continue
                try:
                    cron = parse_cron(expression)
                except CronError:
                    continue
                now = datetime.now()
                minute_key = now.strftime("%Y-%m-%dT%H:%M")
                if minute_key == last_fired or not cron.matches(now):
                    continue
                last_fired = minute_key
                if not self.engine_update_available:
                    continue
                if self._engine_update_task is not None or self._restart_requested:
                    continue
                self._update_log(
                    f"Scheduled engine update ({expression}) starting...", "info"
                )
                self.request_engine_update(reason="schedule")
        except asyncio.CancelledError:
            raise
        finally:
            self._schedule_task = None

    def request_frontend(self, mode: str) -> None:
        """Switch the interface mode live, without interrupting mining.

        Tears the current frontend down and brings the new one up on the same
        loop. Affects only the running process — the choice is not persisted, so
        the next launch again uses --mode / the web default. Safe to call from
        within a frontend (e.g. the REPL's ``/switch-mode``).
        """
        if mode == self.mode or self._switch_task is not None:
            return
        if mode in ("tui", "repl") and not console.INTERACTIVE:
            self.print(
                f"Cannot switch to {mode} mode: no interactive terminal attached."
            )
            return
        self._switch_task = asyncio.create_task(self._switch_frontend(mode))

    def pin_channel(self, channel: Channel) -> None:
        """Pin a channel and switch to it — shared by the TUI and /pin."""
        from constants import State

        self.channels.select(channel)
        self.print(f"Pinned channel: {channel.name} — switching...")
        self._twitch.change_state(State.CHANNEL_SWITCH)

    def unpin_channel(self) -> bool:
        """Resume automatic selection; returns False if nothing was pinned."""
        from constants import State

        if self.channels.get_selection() is None:
            return False
        self.channels.clear_selection()
        self.print("Unpinned — automatic channel selection resumes.")
        self._twitch.change_state(State.CHANNEL_SWITCH)
        return True

    def request_engine_update(self, *, reason: str | None = None) -> bool:
        """Start one non-blocking engine update shared by every CLI frontend.

        ``reason`` records *why* the update was requested so a no-op result can
        be handled sensibly: a reconnect fallback still restarts, while a
        scheduled check simply reports "already up to date".
        """
        if self._engine_update_task is not None or self._restart_requested:
            return False
        self._engine_update_result = None
        self._engine_update_reason = reason
        self._update_log("Checking for engine updates...", "info")
        self._engine_update_task = asyncio.create_task(self._update_engine())
        return True

    def request_restart(
        self,
        message: str = "Restarting TDMConsole...",
        style: str = "notify",
    ) -> bool:
        """Gracefully shut down and replace this process with a fresh one."""
        if self._engine_update_task is not None:
            return False
        return self._schedule_restart(message, style)

    def _schedule_restart(self, message: str, style: str) -> bool:
        if self._restart_requested:
            return False
        self._restart_requested = True
        self._update_log(message, style)
        self._restart_task = asyncio.create_task(self._restart_after_render())
        return True

    async def _restart_after_render(self) -> None:
        # Let terminal and WebSocket frontends render the restart notice before
        # the normal shutdown path saves state and main.py calls os.execv().
        await asyncio.sleep(0.75)
        self.close()

    def _update_log(self, text: str, style: str = "") -> None:
        self.state.add_log(text, style)
        self.frontend.log(text, style)

    async def _update_engine(self) -> None:
        from tdm_cli.updater import EngineUpdateError, update_engine

        reason = getattr(self, "_engine_update_reason", None)
        try:
            result = await asyncio.to_thread(update_engine)
            if not result.changed:
                self._engine_update_result = "up_to_date"
                self._update_log(result.message, "success")
                self._mark_engine_bootstrap()
                if reason == "reconnect":
                    # Nothing to pull, so the previous restart simply wasn't
                    # enough — keep cycling the process rather than stalling.
                    self._schedule_restart(
                        "Engine is already current; restarting TDMConsole...",
                        "notify",
                    )
                return
            self._engine_update_result = "updated"
            self._update_log(result.message, "success")
            # Before the restart below: the replacement process must not repeat
            # the first-launch refresh.
            self._mark_engine_bootstrap()
            self._schedule_restart("Restarting to load the updated engine...", "notify")
        except EngineUpdateError as exc:
            self._engine_update_result = "failed"
            self._update_log(f"Engine update failed: {exc}", "error")
            if reason == "reconnect":
                self._schedule_restart(
                    "Engine update unavailable; restarting TDMConsole...", "warn"
                )
        except Exception as exc:  # defensive: keep the miner alive
            self._engine_update_result = "failed"
            self._update_log(f"Unexpected engine update error: {exc}", "error")
            if reason == "reconnect":
                self._schedule_restart(
                    "Engine update failed unexpectedly; restarting TDMConsole...",
                    "warn",
                )
        finally:
            self._engine_update_task = None
            self._engine_update_reason = None

    async def _switch_frontend(self, mode: str) -> None:
        try:
            old = self.frontend
            # Tear the old frontend down intentionally (won't trip its
            # crash/close-the-miner guard).
            if hasattr(old, "begin_intentional_stop"):
                old.begin_intentional_stop()
            old.stop()
            # Wait for teardown WITHOUT awaiting the old frontend's own task
            # directly: a REPL-initiated switch runs on that very task, so
            # awaiting it would deadlock. Poll a readiness predicate instead,
            # yielding control so the old loop can unwind first.
            if hasattr(old, "is_stopped"):
                for _ in range(200):  # ~10s safety cap
                    if old.is_stopped():
                        break
                    await asyncio.sleep(0.05)
            elif hasattr(old, "wait_stopped"):
                await old.wait_stopped()

            self.mode = mode
            self.frontend = make_frontend(mode, self)
            self.frontend.start()
            self.print(f"Interface mode switched to: {mode}")
            if mode == "headless":
                self.print(
                    "Headless has no interactive input — Ctrl+C exits; "
                    "restart with --mode tui or --mode repl to switch back."
                )
        finally:
            self._switch_task = None

    def close(self, *args: Any) -> int:
        """Request shutdown (quit key / Ctrl+C / SIGTERM / fatal error path)."""
        self._close_requested.set()
        self._twitch.close()
        return 0

    def close_window(self) -> None:
        logger.removeHandler(self._handler)
        self.frontend.close_window()

    def save(self, *, force: bool = False) -> None:
        # No image cache to persist in CLI mode.
        pass

    def grab_attention(self, *, sound: bool = True) -> None:
        if sound:
            self.frontend.attention()

    def request_login_prompt(self) -> bool:
        """User asked to see the device-code login (via /login or the dashboard
        Login action). Returns False if there's nothing pending."""
        if self.state.login_available:
            self.state.login_prompt = True
            return True
        return False

    def set_games(self, games: set[Game]) -> None:
        self.settings.set_games(games)

    def display_drop(
        self, drop: TimedDrop, *, countdown: bool = True, subone: bool = False
    ) -> None:
        self.progress.display(drop, countdown=countdown, subone=subone)
        self.tray.update_title(drop)

    def clear_drop(self) -> None:
        self.progress.display(None)
        self.tray.update_title(None)

    def print(self, message: str) -> None:
        self.output.print(message)
