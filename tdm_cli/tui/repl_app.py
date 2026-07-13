"""Claude-Code-style command REPL (Textual App).

Layout mirrors Claude Code's own CLI:

    ╭─── TDM-CLI v16 ──────────────────────────────╮
    │   ⛏ logo / welcome   │ Getting started tips  │
    ╰──────────────────────────────────────────────╯

      ... scrolling output (miner log + command results) ...

    ──────────────────────────────────────────────────
    ❯ /status█
    ──────────────────────────────────────────────────
      ⛏ Watching shroud · Rust Skin 62.5%   ● repl · /switch-mode tui

The output area is a border-less RichLog taking all remaining space; the input
zone (rule / ``❯`` prompt / rule / status line) is docked to the bottom. Ghost
auto-complete comes from Textual's suggester (accept with →), and ↑/↓ recall
command history.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.suggester import SuggestFromList
from textual.widgets import Input, RichLog, Rule, Static

from version import __version__

from tdm_cli.commands import COMMANDS, CommandProcessor
from tdm_cli.tui.screens import LoginScreen

if TYPE_CHECKING:
    from tdm_cli.gui import GUIManager

# semantic style (from CommandProcessor / state log) -> rich style
_STYLES = {
    "info": "cyan",
    "success": "green",
    "warn": "yellow",
    "error": "red",
    "notify": "bold yellow",
    "dim": "dim",
    "bold": "bold",
}

_TIPS = [
    ("/help", "list all commands"),
    ("/status", "miner status and drop progress"),
    ("/games", "priority & excluded games"),
    ("/pin <channel>", "pin & switch to a channel"),
    ("/switch-mode tui", "full-screen dashboard"),
]


class CommandInput(Input):
    """Single-line input with ↑/↓ command history."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._hist_idx: int | None = None
        self._draft: str = ""

    def remember(self, line: str) -> None:
        if line and (not self._history or self._history[-1] != line):
            self._history.append(line)
        self._hist_idx = None

    def _on_key(self, event: events.Key) -> None:
        if event.key == "up" and self._history:
            if self._hist_idx is None:
                self._draft = self.value
                self._hist_idx = len(self._history) - 1
            elif self._hist_idx > 0:
                self._hist_idx -= 1
            self.value = self._history[self._hist_idx]
            self.cursor_position = len(self.value)
            event.prevent_default()
            event.stop()
        elif event.key == "down" and self._hist_idx is not None:
            if self._hist_idx < len(self._history) - 1:
                self._hist_idx += 1
                self.value = self._history[self._hist_idx]
            else:
                self._hist_idx = None
                self.value = self._draft
            self.cursor_position = len(self.value)
            event.prevent_default()
            event.stop()


class ReplApp(App[None]):
    TITLE = "TDM-CLI"

    BINDINGS = [
        Binding("ctrl+c", "quit_miner", "Quit", show=False),
    ]

    CSS = """
    #output {
        height: 1fr;
        padding: 0 1;
        background: transparent;
        border: none;
    }
    #input-zone {
        dock: bottom;
        height: auto;
        padding: 0 1;
    }
    #input-zone Rule {
        margin: 0;
        color: $primary-darken-1;
    }
    #prompt-row {
        height: 1;
    }
    #prompt-char {
        width: 2;
        color: $accent;
        text-style: bold;
    }
    #cmd-input, #cmd-input:focus {
        border: none;
        height: 1;
        padding: 0;
        background: transparent;
    }
    #status-line {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self, manager: GUIManager) -> None:
        super().__init__()
        self._m = manager
        self._log_seq = 0
        self._login_screen: LoginScreen | None = None
        self._processor = CommandProcessor(manager, self._command_out)

    # ------------------------------------------------------------------ UI
    def compose(self) -> ComposeResult:
        yield RichLog(id="output", wrap=True, auto_scroll=True)
        with Vertical(id="input-zone"):
            yield Rule()
            with Horizontal(id="prompt-row"):
                yield Static("❯", id="prompt-char")
                yield CommandInput(
                    id="cmd-input",
                    placeholder="Type a /command — /help lists them all",
                    suggester=SuggestFromList(list(COMMANDS), case_sensitive=False),
                )
            yield Rule()
            yield Static("", id="status-line")

    def on_mount(self) -> None:
        self._write_welcome()
        self.query_one("#cmd-input", CommandInput).focus()
        self.set_interval(0.5, self._tick)
        self._tick()

    def _write_welcome(self) -> None:
        grid = Table.grid(padding=(0, 3))
        grid.add_column(justify="center", vertical="middle")
        grid.add_column()
        logo = Text()
        logo.append("⛏ TDM-CLI\n\n", style="bold cyan")
        logo.append("Welcome!\n", style="bold")
        logo.append("Twitch Drops Miner", style="dim")
        tips = Text("Getting started\n", style="bold")
        for cmd, desc in _TIPS:
            tips.append(f"  {cmd:<18}", style="cyan")
            tips.append(f"{desc}\n", style="")
        tips.append("\nMode: ", style="dim")
        tips.append("repl", style="green")
        tips.append(" · switch live with /switch-mode, saved across restarts", style="dim")
        grid.add_row(logo, tips)
        self.query_one("#output", RichLog).write(
            Panel(
                grid,
                title=f"TDM-CLI v{__version__}",
                title_align="left",
                box=ROUNDED,
                border_style="cyan",
                padding=(0, 1),
            )
        )

    # ---------------------------------------------------------------- output
    def _write_line(self, text: str, style: str = "", *, stamp: str | None = None) -> None:
        line = Text()
        if stamp:
            line.append(f"{stamp} ", style="dim")
        line.append(text, _STYLES.get(style, style or ""))
        self.query_one("#output", RichLog).write(line)

    def _command_out(self, text: str, style: str = "") -> None:
        self._write_line(text, style)

    # ---------------------------------------------------------------- tick
    def _tick(self) -> None:
        state = self._m.state

        # Drain miner log lines into the output area.
        for entry in state.logs_since(self._log_seq):
            self._log_seq = entry.seq
            self._write_line(entry.text, entry.style, stamp=entry.stamp)

        # Status line: left = miner state, right = mode hint.
        left = Text()
        left.append("⛏ ", style="cyan")
        left.append(state.status or "starting...", style="")
        if state.watching_channel:
            left.append(f" · {state.watching_channel}", style="bold")
        if state.drop_rewards:
            left.append(
                f" · {state.drop_rewards} {state.drop_progress:.1%} ⏱ {state.drop_remaining}",
                style="dim",
            )
        ws = len(state.websockets)
        if ws:
            left.append(f" · ws {ws}", style="dim")
        right = Text()
        right.append("● ", style="green")
        right.append("repl", style="bold")
        right.append(" · /switch-mode tui", style="dim")
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(left, right)
        self.query_one("#status-line", Static).update(grid)

        # Device-code login modal (same behaviour as the dashboard).
        if state.login_prompt and self._login_screen is None:
            self._login_screen = LoginScreen(self._m)
            self.push_screen(self._login_screen)
        elif not state.login_prompt and self._login_screen is not None:
            screen, self._login_screen = self._login_screen, None
            try:
                if screen in self.screen_stack:
                    screen.dismiss()
            except Exception:
                self._login_screen = screen  # not on top; retry next tick

    # ---------------------------------------------------------------- input
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "cmd-input":
            return
        text = event.value.strip()
        input_widget = self.query_one("#cmd-input", CommandInput)
        input_widget.clear()
        if not text:
            return
        input_widget.remember(text)
        # Echo the command, Claude-Code style.
        echo = Text()
        echo.append("❯ ", style="bold cyan")
        echo.append(text, style="bold")
        self.query_one("#output", RichLog).write(echo)
        self._processor.dispatch(text)

    def action_quit_miner(self) -> None:
        self._m.print("Quit requested, shutting down...")
        self._m.close()
