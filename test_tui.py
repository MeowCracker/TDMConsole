"""Headless end-to-end test of the TUI (run via Textual's test driver)."""
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


async def main() -> None:
    from textual.widgets import DataTable, Input

    from tdm_cli.tui.app import MinerApp
    from tdm_cli.tui.screens import LoginScreen, GamesScreen, SettingsScreen

    twitch = FakeTwitch()
    manager = cli_gui.GUIManager(twitch)
    twitch.channels[1] = FakeChannel(1, "shroud", "Rust")
    twitch.channels[2] = FakeChannel(2, "summit1g", "Rust", online=False)
    twitch.inventory = [
        FakeCampaign("Rust", "Rust Drops", 3, 10, 0.3),
        FakeCampaign("VALORANT", "V Drops", 0, 5, 0.0, active=False),
    ]

    app = MinerApp(manager)
    async with app.run_test(size=(120, 40)) as pilot:
        # --- dashboard renders miner state -------------------------------
        manager.status.update("Watching shroud")
        st = manager.state
        st.watching_channel, st.watching_game = "shroud", "Rust"
        st.drop_rewards, st.drop_progress = "Rust Skin", 0.625
        st.campaign_name, st.campaign_progress = "Rust Drops", 0.3
        st.campaign_claimed, st.campaign_total = 3, 10
        manager.print("hello from miner")
        st.channels_rev += 1
        st.inventory_rev += 1
        await pilot.pause(1.2)

        channels_table = app.query_one("#channels", DataTable)
        campaigns_table = app.query_one("#campaigns", DataTable)
        assert channels_table.row_count == 2, f"channels rows: {channels_table.row_count}"
        assert campaigns_table.row_count == 2, f"campaigns rows: {campaigns_table.row_count}"
        assert app._log_seq >= 1, "log not drained"
        print("PASS dashboard: tables + log render from state")

        # --- login modal only opens on request, never on its own ----------
        import asyncio as _aio
        _aio.get_event_loop()
        await manager.login.ask_enter_code(
            __import__("yarl").URL("https://www.twitch.tv/activate?device-code=ABCD1234"),
            "ABCD1234",
        )
        await pilot.pause(0.8)
        assert not isinstance(app.screen, LoginScreen), "modal popped without a request!"
        assert st.login_available and not st.login_prompt
        # user triggers it via the Login action / 'l'
        assert manager.request_login_prompt() is True
        await pilot.pause(0.8)
        assert isinstance(app.screen, LoginScreen), f"screen: {type(app.screen)}"
        # success dismisses it
        manager.login.update("Logged in", 12345678)
        await pilot.pause(0.8)
        assert not isinstance(app.screen, LoginScreen), "login screen did not close"
        assert not st.login_available and st.user_id == 12345678
        print("PASS login modal: opens only on request, closes on success")

        # --- games screen: add via input, save on esc ---------------------
        await pilot.press("g")
        await pilot.pause(0.5)
        assert isinstance(app.screen, GamesScreen), f"screen: {type(app.screen)}"
        add_input = app.screen.query_one("#games-add", Input)
        add_input.focus()
        add_input.value = "Apex Legends"
        await pilot.press("enter")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert "Apex Legends" in twitch.settings.priority, twitch.settings.priority
        assert twitch.settings.saved >= 1, "settings not saved"
        assert State.GAMES_UPDATE in twitch.states, twitch.states
        print("PASS games screen: input add + esc save + GAMES_UPDATE")

        # --- settings screen: esc saves -----------------------------------
        saved_before = twitch.settings.saved
        await pilot.press("s")
        await pilot.pause(0.2)
        assert isinstance(app.screen, SettingsScreen), f"screen: {type(app.screen)}"
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert twitch.settings.saved > saved_before, "settings screen did not save"
        print("PASS settings screen: esc save")

        # --- channel pin via Enter on table --------------------------------
        channels_table.focus()
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert manager.channels.get_selection() is not None, "no channel pinned"
        assert State.CHANNEL_SWITCH in twitch.states, twitch.states
        print("PASS channels table: Enter pins + CHANNEL_SWITCH")

        # --- unpin via Esc --------------------------------------------------
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert manager.channels.get_selection() is None, "channel still pinned"
        print("PASS unpin via Esc")

        # --- quit -----------------------------------------------------------
        await pilot.press("q")
        await pilot.pause(0.2)
        assert twitch.closed and manager.close_requested, "quit did not close miner"
        print("PASS quit: q closes the miner")

    print("ALL TUI TESTS PASSED")


asyncio.run(main())
