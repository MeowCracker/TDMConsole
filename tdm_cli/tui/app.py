"""The full-screen miner dashboard (Textual App).

Layout (matches the approved design):

    ┌ header: status │ user │ websockets ────────────────┐
    │ Mining: game / channel / drop bar / campaign bar   │
    ├─ Channels table ────────┬─ Campaigns table ────────┤
    ├─ Log ──────────────────────────────────────────────┤
    └ footer: [q]uit [r]eload [g]ames [s]ettings ────────┘

Everything renders from ``manager.state`` (plus the live ``twitch.channels`` /
``twitch.inventory`` domain objects) on a 0.5 s timer. Tables are only rebuilt
when their revision counter moves (or every ~5 s as a safety net).

Interactions:
  q / Ctrl+C  quit          g  game priority & exclusions
  r           reload        s  settings
  Enter (channels table)    pin + switch to the selected channel
  Esc                       unpin (auto-selection resumes)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, RichLog, Static

from constants import State

from tdm_cli.tui.screens import LoginScreen, GamesScreen, SettingsScreen

if TYPE_CHECKING:
    from tdm_cli.gui import GUIManager

TABLE_REBUILD_TICKS = 10  # safety-net rebuild cadence (x 0.5s)

_LOG_STYLES = {"error": "red", "warn": "yellow", "notify": "bold yellow"}


def bar(fraction: float, width: int = 24) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


class MinerApp(App[None]):
    TITLE = "TDMConsole"

    BINDINGS = [
        Binding("q", "quit_miner", "Quit"),
        Binding("ctrl+c", "quit_miner", "Quit", show=False),
        Binding("l", "login", "Login"),
        Binding("r", "reload", "Reload"),
        Binding("g", "games", "Games"),
        Binding("s", "settings", "Settings"),
        Binding("escape", "unpin", "Unpin", show=False),
    ]

    CSS = """
    #header {
        dock: top;
        height: 1;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
    }
    #progress {
        height: 8;
        border: round $primary;
        padding: 0 1;
    }
    #tables {
        height: 1fr;
    }
    #channels {
        width: 3fr;
        border: round $primary;
    }
    #campaigns {
        width: 2fr;
        border: round $primary;
    }
    #log {
        height: 9;
        border: round $primary;
    }
    """

    def __init__(self, manager: GUIManager) -> None:
        super().__init__()
        self._m = manager
        self._log_seq = 0
        self._channels_rev = -1
        self._inventory_rev = -1
        self._ticks = 0
        self._login_screen: LoginScreen | None = None

    # ------------------------------------------------------------------ UI
    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        yield Static("", id="progress")
        with Horizontal(id="tables"):
            yield DataTable(id="channels", cursor_type="row")
            yield DataTable(id="campaigns", cursor_type="row")
        yield RichLog(id="log", wrap=False, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        channels = self.query_one("#channels", DataTable)
        channels.add_columns(" ", "Channel", "Game", "Status", "Viewers", "Drops")
        channels.border_title = "Channels — Enter: pin & switch, Esc: unpin"
        campaigns = self.query_one("#campaigns", DataTable)
        campaigns.add_columns("Game", "Campaign", "Drops", "Progress", "Status")
        campaigns.border_title = "Campaigns"
        self.query_one("#progress", Static).border_title = "Mining"
        self.query_one("#log", RichLog).border_title = "Log"
        self.set_interval(0.5, self._tick)
        self._tick()

    # ---------------------------------------------------------------- tick
    def _tick(self) -> None:
        state = self._m.state
        self._ticks += 1

        # Header
        ws_count = len(state.websockets)
        topics = sum(t for _, t in state.websockets.values())
        if state.user_id is not None:
            user = f"user {state.user_id}"
        elif state.login_available:
            user = "⚠ login required — press l"
        else:
            user = state.login_status or "logged out"
        self.query_one("#header", Static).update(
            f"⛏ TDMConsole │ {state.status or '...'} │ {user} │ ws {ws_count} ({topics} topics)"
        )

        # Progress panel
        self.query_one("#progress", Static).update(self._render_progress())

        # Log (drain new entries)
        log_widget = self.query_one("#log", RichLog)
        for entry in state.logs_since(self._log_seq):
            self._log_seq = entry.seq
            line = Text(f"{entry.stamp} ", style="dim")
            line.append(entry.text, _LOG_STYLES.get(entry.style) or "")
            log_widget.write(line)

        # Device-code login modal
        if state.login_prompt and self._login_screen is None:
            self._login_screen = LoginScreen(self._m)
            self.push_screen(self._login_screen)
        elif not state.login_prompt and self._login_screen is not None:
            screen, self._login_screen = self._login_screen, None
            try:
                if screen in self.screen_stack:
                    screen.dismiss()
            except Exception:
                # Not on top right now — retry on the next tick.
                self._login_screen = screen

        # Tables
        safety = self._ticks % TABLE_REBUILD_TICKS == 0
        if state.channels_rev != self._channels_rev or safety:
            self._channels_rev = state.channels_rev
            self._rebuild_channels()
        if state.inventory_rev != self._inventory_rev or safety:
            self._inventory_rev = state.inventory_rev
            self._rebuild_campaigns()

    def _render_progress(self) -> Text:
        state = self._m.state
        text = Text()
        game = state.watching_game or state.campaign_game or "-"
        text.append("Game      ", "bold")
        text.append(f"{game}\n")
        text.append("Channel   ", "bold")
        text.append(f"{state.watching_channel or '-'}\n")
        if state.drop_rewards:
            text.append("Drop      ", "bold")
            text.append(f"{state.drop_rewards}\n          ")
            text.append(bar(state.drop_progress), "green")
            text.append(f" {state.drop_progress:6.1%}  ⏱ {state.drop_remaining}\n")
            text.append("Campaign  ", "bold")
            text.append(
                f"{state.campaign_name}"
                f" ({state.campaign_claimed}/{state.campaign_total} claimed)\n          "
            )
            text.append(bar(state.campaign_progress), "cyan")
            text.append(f" {state.campaign_progress:6.1%}  ⏱ {state.campaign_remaining}")
        else:
            text.append("Drop      ", "bold")
            text.append("-\n")
            text.append("Campaign  ", "bold")
            text.append("-")
        return text

    def _rebuild_channels(self) -> None:
        table = self.query_one("#channels", DataTable)
        cursor = table.cursor_row
        table.clear()
        state = self._m.state
        pinned = self._m.channels.get_selection()
        for channel in self._m._twitch.channels.values():
            if state.watching_channel == channel.name:
                mark = "▶"
            elif pinned is channel:
                mark = "📌"
            else:
                mark = ""
            game = channel.game.name if channel.game is not None else "-"
            if channel.online:
                status = Text("ONLINE", "green")
            elif channel.pending_online:
                status = Text("pending", "yellow")
            else:
                status = Text("offline", "dim")
            viewers = "-" if channel.viewers is None else str(channel.viewers)
            drops = "✓" if channel.drops_enabled else ""
            table.add_row(
                mark, channel.name, game, status, viewers, drops, key=str(channel.id)
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def _rebuild_campaigns(self) -> None:
        table = self.query_one("#campaigns", DataTable)
        cursor = table.cursor_row
        table.clear()
        for campaign in self._m._twitch.inventory:
            if campaign.active:
                status = Text("Active", "green")
            elif campaign.upcoming:
                status = Text("Upcoming", "yellow")
            else:
                status = Text("Expired", "dim")
            table.add_row(
                campaign.game.name,
                campaign.name,
                f"{campaign.claimed_drops}/{campaign.total_drops}",
                f"{campaign.progress:4.0%}",
                status,
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    # ------------------------------------------------------------- actions
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "channels" or event.row_key is None:
            return
        try:
            channel_id = int(event.row_key.value or "")
        except ValueError:
            return
        channel = self._m._twitch.channels.get(channel_id)
        if channel is None:
            return
        self._m.channels.select(channel)
        self._m.print(f"Pinned channel: {channel.name} — switching...")
        self._m._twitch.change_state(State.CHANNEL_SWITCH)

    def action_unpin(self) -> None:
        if self._m.channels.get_selection() is not None:
            self._m.channels.clear_selection()
            self._m.print("Unpinned — automatic channel selection resumes.")
            self._m._twitch.change_state(State.CHANNEL_SWITCH)

    def action_quit_miner(self) -> None:
        self._m.print("Quit requested, shutting down...")
        self._m.close()

    def action_login(self) -> None:
        if not self._m.request_login_prompt():
            self._m.print("No pending login — you're already logged in.")

    def action_reload(self) -> None:
        self._m.print("Reload requested...")
        self._m._twitch.change_state(State.RESTART)

    def action_games(self) -> None:
        if not isinstance(self.screen, (GamesScreen, SettingsScreen)):
            self.push_screen(GamesScreen(self._m))

    def action_settings(self) -> None:
        if not isinstance(self.screen, (GamesScreen, SettingsScreen)):
            self.push_screen(SettingsScreen(self._m))
