"""Interactive screens for the TUI: device-code login, game priority, settings."""
from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING

from yarl import URL
from textual.screen import Screen, ModalScreen
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, OptionList, Select, Static

from constants import State, PriorityMode, LANG_PATH, DEFAULT_LANG

if TYPE_CHECKING:
    from tdm_cli.gui import GUIManager


class LoginScreen(ModalScreen[None]):
    """Device-code login prompt. Pushed/popped automatically by the app's tick
    based on ``state.login_prompt`` — it never dismisses itself."""

    DEFAULT_CSS = """
    LoginScreen {
        align: center middle;
    }
    #login-box {
        width: 76;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #login-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #login-code {
        text-style: bold;
        color: $success;
    }
    #login-open {
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "hide", "Hide"),
        Binding("q", "app.quit_miner", "Quit"),
    ]

    def __init__(self, manager: GUIManager) -> None:
        super().__init__()
        self._m = manager

    def action_hide(self) -> None:
        # Close the modal but keep the login poll running in the background.
        self._m.state.login_prompt = False

    def compose(self):
        with Vertical(id="login-box"):
            yield Static("Twitch login required", id="login-title")
            yield Static("", id="login-url")
            yield Static("", id="login-code")
            yield Button("Open browser", id="login-open", variant="primary")
            yield Static("Waiting for authorization — mining starts automatically.")

    def on_mount(self) -> None:
        self._refresh()
        # The device code rotates when it expires; keep the display current.
        self.set_interval(1.0, self._refresh)

    def _refresh(self) -> None:
        state = self._m.state
        self.query_one("#login-url", Static).update(
            f"1. Open this URL in a browser (any device):\n   {state.login_url}"
        )
        self.query_one("#login-code", Static).update(
            f"2. Enter this code:  {state.login_code}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login-open" and self._m.state.login_url:
            try:
                webbrowser.open(self._m.state.login_url)
            except Exception:
                self._m.print("Could not open a browser on this machine.")


class GamesScreen(Screen[None]):
    """Game priority & exclusions editor. Mutations are kept locally and saved
    to settings.json when the screen closes (Esc)."""

    DEFAULT_CSS = """
    GamesScreen {
        padding: 1 2;
    }
    #games-title {
        text-style: bold;
        height: 1;
    }
    #games-help {
        color: $text-muted;
        height: 2;
    }
    #games-add {
        margin-bottom: 1;
    }
    #games-lists {
        height: 1fr;
    }
    #games-lists Vertical {
        width: 1fr;
        margin-right: 1;
    }
    #games-lists Label {
        text-style: bold;
    }
    #games-lists OptionList {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("escape", "save_close", "Save & back"),
        Binding("x", "exclude", "Exclude"),
        Binding("u", "move_up", "Move up"),
        Binding("d", "move_down", "Move down"),
        Binding("delete", "remove", "Remove", show=False),
        Binding("backspace", "remove", "Remove", show=False),
    ]

    def __init__(self, manager: GUIManager) -> None:
        super().__init__()
        self._m = manager
        settings = manager._twitch.settings
        self._priority: list[str] = list(settings.priority)
        self._exclude: list[str] = sorted(settings.exclude)

    def compose(self):
        yield Static("Game priority & exclusions", id="games-title")
        yield Static(
            "Enter: Available→add to priority · Priority/Excluded→remove   "
            "x: exclude   u/d: reorder priority   Esc: save & back",
            id="games-help",
        )
        yield Input(
            placeholder="Type a game name and press Enter to add it to the priority list",
            id="games-add",
        )
        with Horizontal(id="games-lists"):
            with Vertical():
                yield Label("Available (from inventory)")
                yield OptionList(id="games-available")
            with Vertical():
                yield Label("Priority (ordered)")
                yield OptionList(id="games-priority")
            with Vertical():
                yield Label("Excluded")
                yield OptionList(id="games-excluded")

    def on_mount(self) -> None:
        self._refresh()

    # ------------------------------------------------------------- helpers
    def _available_games(self) -> list[str]:
        names = {c.game.name for c in self._m._twitch.inventory}
        names.difference_update(self._priority, self._exclude)
        return sorted(names)

    def _refresh(self) -> None:
        fills = {
            "#games-available": self._available_games(),
            "#games-priority": self._priority,
            "#games-excluded": self._exclude,
        }
        for selector, values in fills.items():
            option_list = self.query_one(selector, OptionList)
            highlighted = option_list.highlighted
            option_list.clear_options()
            if values:
                option_list.add_options(values)
                if highlighted is not None:
                    option_list.highlighted = min(highlighted, len(values) - 1)

    def _highlighted_value(self, option_list: OptionList) -> str | None:
        index = option_list.highlighted
        if index is None or option_list.option_count == 0:
            return None
        return str(option_list.get_option_at_index(index).prompt)

    def _add_priority(self, name: str) -> None:
        if name and name not in self._priority:
            if name in self._exclude:
                self._exclude.remove(name)
            self._priority.append(name)

    # ------------------------------------------------------------- events
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "games-add":
            self._add_priority(event.value.strip())
            event.input.clear()
            self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        name = str(event.option.prompt)
        source = event.option_list.id
        if source == "games-available":
            self._add_priority(name)
        elif source == "games-priority":
            self._priority.remove(name)
        elif source == "games-excluded":
            self._exclude.remove(name)
        self._refresh()

    # ------------------------------------------------------------- actions
    def action_exclude(self) -> None:
        focused = self.focused
        if isinstance(focused, OptionList) and focused.id in (
            "games-available", "games-priority"
        ):
            name = self._highlighted_value(focused)
            if name:
                if name in self._priority:
                    self._priority.remove(name)
                if name not in self._exclude:
                    self._exclude.append(name)
                    self._exclude.sort()
                self._refresh()

    def _move(self, delta: int) -> None:
        focused = self.focused
        if not (isinstance(focused, OptionList) and focused.id == "games-priority"):
            return
        name = self._highlighted_value(focused)
        if name is None:
            return
        index = self._priority.index(name)
        new_index = index + delta
        if 0 <= new_index < len(self._priority):
            self._priority[index], self._priority[new_index] = (
                self._priority[new_index], self._priority[index],
            )
            self._refresh()
            focused.highlighted = new_index

    def action_move_up(self) -> None:
        self._move(-1)

    def action_move_down(self) -> None:
        self._move(1)

    def action_remove(self) -> None:
        focused = self.focused
        if isinstance(focused, OptionList) and focused.id in (
            "games-priority", "games-excluded"
        ):
            name = self._highlighted_value(focused)
            if name is None:
                return
            if focused.id == "games-priority":
                self._priority.remove(name)
            else:
                self._exclude.remove(name)
            self._refresh()

    def action_save_close(self) -> None:
        settings = self._m._twitch.settings
        settings.priority = self._priority
        settings.exclude = set(self._exclude)
        settings.save()
        self._m.print("Game priority/exclusions saved — refreshing wanted games...")
        self._m._twitch.change_state(State.GAMES_UPDATE)
        self.dismiss()


class SettingsScreen(Screen[None]):
    """Runtime settings editor — writes settings.json on close (Esc)."""

    DEFAULT_CSS = """
    SettingsScreen {
        padding: 1 2;
    }
    #settings-title {
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    SettingsScreen Horizontal {
        height: 3;
    }
    SettingsScreen Label {
        width: 24;
        padding-top: 1;
    }
    SettingsScreen Input, SettingsScreen Select {
        width: 1fr;
    }
    #settings-note {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [Binding("escape", "save_close", "Save & back")]

    PRIORITY_MODES = [
        ("Priority list only", PriorityMode.PRIORITY_ONLY),
        ("Ending soonest first", PriorityMode.ENDING_SOONEST),
        ("Low availability first", PriorityMode.LOW_AVBL_FIRST),
    ]

    def __init__(self, manager: GUIManager) -> None:
        super().__init__()
        self._m = manager

    def compose(self):
        settings = self._m._twitch.settings
        languages = sorted({DEFAULT_LANG, *(p.stem for p in LANG_PATH.glob("*.json"))})
        yield Static("Settings", id="settings-title")
        with Horizontal():
            yield Label("Interface mode")
            yield Select(
                [("Full-screen dashboard (TUI)", "tui"),
                 ("Command REPL (Claude-Code style)", "repl"),
                 ("Web UI (browser, for Docker)", "web"),
                 ("Plain log (headless)", "headless")],
                value=self._m.mode,
                allow_blank=False,
                id="set-mode",
            )
        with Horizontal():
            yield Label("Proxy URL")
            yield Input(value=str(settings.proxy), placeholder="(none)", id="set-proxy")
        with Horizontal():
            yield Label("Language")
            yield Select(
                [(lang, lang) for lang in languages],
                value=settings.language if settings.language in languages else DEFAULT_LANG,
                allow_blank=False,
                id="set-language",
            )
        with Horizontal():
            yield Label("Priority mode")
            yield Select(
                self.PRIORITY_MODES,
                value=settings.priority_mode,
                allow_blank=False,
                id="set-priority-mode",
            )
        with Horizontal():
            yield Label("Connection quality")
            yield Select(
                [(str(i), i) for i in range(1, 7)],
                value=max(1, min(6, settings.connection_quality)),
                allow_blank=False,
                id="set-quality",
            )
        yield Static(
            "Esc saves to settings.json. Proxy changes trigger a reload; "
            "a language change fully applies after the next reload (r).",
            id="settings-note",
        )

    def action_save_close(self) -> None:
        settings = self._m._twitch.settings
        new_mode = str(self.query_one("#set-mode", Select).value)
        proxy_text = self.query_one("#set-proxy", Input).value.strip()
        new_proxy = URL(proxy_text) if proxy_text else URL()
        proxy_changed = str(new_proxy) != str(settings.proxy)
        settings.proxy = new_proxy
        settings.language = str(self.query_one("#set-language", Select).value)
        settings.priority_mode = self.query_one("#set-priority-mode", Select).value
        settings.connection_quality = int(
            self.query_one("#set-quality", Select).value  # type: ignore[arg-type]
        )
        settings.save()
        self._m.print("Settings saved.")
        if proxy_changed:
            self._m.print("Proxy changed — reloading...")
            self._m._twitch.change_state(State.RESTART)
        self.dismiss()
        if new_mode != self._m.mode:
            # Hot-swap the interface after this screen has closed.
            self._m.request_frontend(new_mode)
