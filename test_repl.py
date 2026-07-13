"""Headless tests for the REPL: UI-agnostic CommandProcessor, the Claude-Code
style ReplApp layout, and the live frontend hot-swap machinery."""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tdm_cli.bootstrap as bootstrap

bootstrap.setup()

from collections import OrderedDict

from yarl import URL
from constants import State, PriorityMode

import tdm_cli.gui as cli_gui
from tdm_cli.commands import CommandProcessor, COMMANDS


class FakeGame:
    def __init__(self, name):
        self.name = name


class FakeChannel:
    def __init__(self, cid, name, game, online=True, viewers=100):
        self.id = cid
        self.name = name
        self.game = FakeGame(game)
        self.online = online
        self.pending_online = False
        self.viewers = viewers
        self.drops_enabled = True


class FakeCampaign:
    def __init__(self, game, name, claimed, total, progress, active=True):
        self.game = FakeGame(game)
        self.name = name
        self.claimed_drops = claimed
        self.total_drops = total
        self.progress = progress
        self.active = active
        self.upcoming = not active
        self.expired = False


class FakeSettings:
    def __init__(self):
        self.priority = ["Rust"]
        self.exclude = {"Fortnite"}
        self.proxy = URL()
        self.language = "English"
        self.priority_mode = PriorityMode.PRIORITY_ONLY
        self.connection_quality = 1
        self.saved = 0

    def alter(self):
        pass

    def save(self, force=False):
        self.saved += 1


class FakeTwitch:
    def __init__(self):
        self.settings = FakeSettings()
        self.channels = OrderedDict()
        self.inventory = []
        self.states = []
        self.closed = False

    def change_state(self, state):
        self.states.append(state)

    def close(self):
        self.closed = True


def _make_manager(repl_frontend: bool = False):
    twitch = FakeTwitch()
    manager = cli_gui.GUIManager(twitch)
    if repl_frontend:
        # Faithful wiring for the ReplApp test: login hints go to state.log
        # (via the Textual frontend) instead of the headless banner.
        from tdm_cli.tui import TextualFrontend

        manager.frontend = TextualFrontend(manager)
        manager.mode = "repl"
    twitch.channels[1] = FakeChannel(1, "shroud", "Rust")
    twitch.channels[2] = FakeChannel(2, "summit1g", "Rust", online=False)
    twitch.inventory = [FakeCampaign("Rust", "Rust Drops", 3, 10, 0.3)]
    return manager, twitch


def test_command_processor() -> None:
    manager, twitch = _make_manager()
    out: list[tuple[str, str]] = []
    proc = CommandProcessor(manager, lambda text, style="": out.append((text, style)))

    for cmd in ("/help", "/status", "/channels", "/campaigns", "/games", "/login"):
        out.clear()
        proc.dispatch(cmd)
        assert out, f"{cmd} produced no output"
    print("PASS command output: help/status/channels/campaigns/games/login")

    proc.dispatch("/priority add Apex Legends")
    assert "Apex Legends" in twitch.settings.priority, twitch.settings.priority
    assert State.GAMES_UPDATE in twitch.states
    proc.dispatch("/priority up Apex Legends")
    assert twitch.settings.priority[0] == "Apex Legends"
    proc.dispatch("/priority remove Apex Legends")
    assert "Apex Legends" not in twitch.settings.priority
    print("PASS /priority add/up/remove")

    proc.dispatch("/exclude add Rust")
    assert "Rust" in twitch.settings.exclude and "Rust" not in twitch.settings.priority
    proc.dispatch("/exclude remove Rust")
    assert "Rust" not in twitch.settings.exclude
    print("PASS /exclude add/remove with priority sync")

    proc.dispatch("/pin SHROUD")
    assert manager.channels.get_selection() is twitch.channels[1]
    assert State.CHANNEL_SWITCH in twitch.states
    proc.dispatch("/unpin")
    assert manager.channels.get_selection() is None
    print("PASS /pin (case-insensitive) + /unpin")

    proc.dispatch("/proxy http://127.0.0.1:7890")
    assert str(twitch.settings.proxy) == "http://127.0.0.1:7890"
    assert State.RESTART in twitch.states
    proc.dispatch("/proxy clear")
    assert str(twitch.settings.proxy) == ""
    print("PASS /proxy set/clear + RESTART")

    out.clear()
    proc.dispatch("/switch-mode")
    proc.dispatch("/switch-mode bogus")
    proc.dispatch("/switch-mode headless")  # same as current -> no-op
    assert manager._switch_task is None
    print("PASS /switch-mode argument validation")

    # robustness
    proc.dispatch("/definitely-not-a-command")
    proc.dispatch("hello world")
    proc.dispatch('/pin "unclosed quote')
    print("PASS robustness: unknown/non-slash/bad-quote input")

    proc.dispatch("/quit")
    assert twitch.closed and manager.close_requested
    print("PASS /quit")


async def test_repl_app() -> None:
    from textual.widgets import RichLog, Input, Static

    from tdm_cli.tui.repl_app import ReplApp, CommandInput

    manager, twitch = _make_manager(repl_frontend=True)
    app = ReplApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.3)
        # welcome panel + prompt + status line present
        app.query_one("#output", RichLog)
        assert app.query_one("#prompt-char", Static)
        app.query_one("#status-line", Static)
        cmd = app.query_one("#cmd-input", CommandInput)
        print("PASS layout: output + ❯ prompt + status line compose")

        # miner log drains into the output area
        manager.print("hello from miner")
        await pilot.pause(0.7)
        assert app._log_seq >= 1, "miner log not drained into output"
        print("PASS miner log streams into output area")

        # type a command + submit -> dispatched
        cmd.focus()
        cmd.value = "/priority add Valorant"
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert "Valorant" in twitch.settings.priority, twitch.settings.priority
        assert cmd.value == "", "input not cleared after submit"
        print("PASS command submit via Input dispatches + clears")

        # history recall with ↑
        cmd.focus()
        await pilot.press("up")
        await pilot.pause(0.1)
        assert cmd.value == "/priority add Valorant", f"history recall: {cmd.value!r}"
        await pilot.press("down")
        await pilot.pause(0.1)
        print("PASS ↑/↓ command history")

        # status line reflects state
        manager.state.status = "Watching shroud"
        manager.state.watching_channel = "shroud"
        await pilot.pause(0.7)
        assert app.query_one("#status-line", Static).content is not None
        print("PASS status line renders miner state")

        # login modal opens only on request, never on ask_enter_code
        await manager.login.ask_enter_code(
            URL("https://twitch.tv/activate?device-code=ABCD"), "ABCD1234"
        )
        await pilot.pause(0.8)
        from tdm_cli.tui.screens import LoginScreen
        assert not isinstance(app.screen, LoginScreen), "modal popped without /login!"
        assert manager.state.login_available and not manager.state.login_prompt
        assert any("login required" in e.text.lower() for e in manager.state.log_lines), "no hint"
        # code rotation stays silent
        await manager.login.ask_enter_code(
            URL("https://twitch.tv/activate?device-code=ROT2"), "ROT22222"
        )
        await pilot.pause(0.4)
        assert not isinstance(app.screen, LoginScreen) and manager.state.login_code == "ROT22222"
        # /login command reveals it
        cmd.focus()
        cmd.value = "/login"
        await pilot.press("enter")
        await pilot.pause(0.8)
        assert isinstance(app.screen, LoginScreen), f"screen={type(app.screen)}"
        # Esc hides without cancelling
        await pilot.press("escape")
        await pilot.pause(0.6)
        assert not isinstance(app.screen, LoginScreen) and manager.state.login_available
        # success clears it
        manager.login.update("Logged in", 999)
        await pilot.pause(0.6)
        assert not manager.state.login_available and manager.state.user_id == 999
        print("PASS login: no auto-pop, /login reveals, Esc hides, success clears")

        # ctrl+c quits
        await pilot.press("ctrl+c")
        await pilot.pause(0.2)
        assert twitch.closed and manager.close_requested
        print("PASS ctrl+c quits the miner")


class DummyFrontend:
    calls: list[str] = []

    def __init__(self, manager, label="?"):
        self._manager = manager
        self.label = label

    def _rec(self, what):
        DummyFrontend.calls.append(f"{self.label}:{what}")

    def start(self):
        self._rec("start")

    def stop(self):
        self._rec("stop")

    def begin_intentional_stop(self):
        self._rec("intentional")

    def is_stopped(self):
        return True

    async def wait_stopped(self):
        self._rec("wait_stopped")

    def close_window(self):
        pass

    def attention(self):
        pass

    def log(self, text, style=""):
        self._rec(f"log:{text[:30]}")

    def on_status(self, text):
        pass

    def on_login(self, status, user_id):
        pass

    def on_websocket(self, idx, status):
        pass

    def on_watching(self, channel):
        pass

    def on_drop(self, line):
        pass

    def show_login(self, url, code):
        pass

    def hide_login(self):
        pass


async def test_hot_swap() -> None:
    manager, twitch = _make_manager()
    DummyFrontend.calls = []
    cli_gui.register_frontend("dummy-a", lambda m: DummyFrontend(m, "A"))
    cli_gui.register_frontend("dummy-b", lambda m: DummyFrontend(m, "B"))
    manager.frontend = DummyFrontend(manager, "A")
    manager.mode = "dummy-a"

    manager.request_frontend("dummy-b")
    assert manager._switch_task is not None
    await manager._switch_task
    await asyncio.sleep(0)

    assert manager.mode == "dummy-b" and manager.frontend.label == "B"
    a_calls = [c for c in DummyFrontend.calls if c.startswith("A:")]
    assert a_calls[:2] == ["A:intentional", "A:stop"], a_calls
    assert DummyFrontend.calls.index("B:start") > DummyFrontend.calls.index("A:stop")
    assert any(c.startswith("B:log:Interface mode switched") for c in DummyFrontend.calls)
    manager.request_frontend("dummy-b")  # same mode -> no-op
    assert manager._switch_task is None
    print("PASS hot-swap: ordering, mode update, log routing, same-mode no-op")


async def main() -> None:
    test_command_processor()
    await test_repl_app()
    await test_hot_swap()
    print("ALL REPL TESTS PASSED")


asyncio.run(main())
