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

import sys
import asyncio
import logging
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, TypeVar, TYPE_CHECKING

from translate import _
from exceptions import ExitRequest
from constants import OUTPUT_FORMATTER

from tdm_cli import console
from tdm_cli.state import MinerState
from tdm_cli.console import Color

if TYPE_CHECKING:
    from collections import abc

    from yarl import URL

    from twitch import Twitch
    from channel import Channel
    from constants import Game
    from inventory import TimedDrop, DropsCampaign

_T = TypeVar("_T")
logger = logging.getLogger("TwitchDrops")


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

    def hide_login(self) -> None:
        pass


# main.py swaps this for TextualFrontend when a TUI should be shown.
FRONTEND_FACTORY: Callable[["GUIManager"], Any] = HeadlessFrontend


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

    def update(self, idx: int, status: str | None = None, topics: int | None = None) -> None:
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
        if user_id is not None and state.login_prompt:
            # Authorized — retire the login prompt.
            state.login_prompt = False
            self._manager.frontend.hide_login()
        self._manager.frontend.on_login(status, user_id)

    def clear(self, login: bool = False, password: bool = False, token: bool = False) -> None:
        # No stored input fields in CLI; nothing to clear.
        pass

    async def ask_enter_code(self, page_url: URL, user_code: str) -> None:
        """Primary (device-code) login. Non-blocking: surface the code + URL and
        return so the upstream OAuth poll loop can wait for activation."""
        self.update(_("gui", "login", "required"), None)
        state = self._manager.state
        state.login_url = str(page_url)
        state.login_code = user_code
        state.login_prompt = True
        self._manager.grab_attention(sound=False)
        self._manager.frontend.show_login(str(page_url), user_code)

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
        self._settings = manager._twitch.settings

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
        self.frontend = FRONTEND_FACTORY(self)

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

    async def wait_until_closed(self) -> None:
        # Doubles as an interruptible sleep: twitch.py awaits this with a timeout
        # so a close request wakes the mine loop early.
        await self._close_requested.wait()

    async def coro_unless_closed(self, coro: abc.Awaitable[_T]) -> _T:
        tasks: list[asyncio.Task[Any]] = [
            asyncio.ensure_future(coro),
            asyncio.ensure_future(self._close_requested.wait()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if self._close_requested.is_set():
            raise ExitRequest()
        return await next(iter(done))

    def prevent_close(self) -> None:
        self._close_requested.clear()

    def start(self) -> None:
        # No GUI event loop to pump; a parked task keeps ``running`` truthful and
        # gives ``stop()`` something symmetric to cancel.
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll())
        self.frontend.start()

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
        self.frontend.stop()

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
