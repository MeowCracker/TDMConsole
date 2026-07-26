from __future__ import annotations

import unittest
from types import SimpleNamespace

from tdm_cli.state import MinerState
from tdm_cli.web.server import _state_signature


def _make_manager() -> SimpleNamespace:
    settings = SimpleNamespace(
        priority=["Game A"],
        exclude={"Game B"},
        proxy="",
        priority_mode=SimpleNamespace(name="PRIORITY_ONLY"),
    )
    return SimpleNamespace(
        state=MinerState(),
        _twitch=SimpleNamespace(settings=settings),
        mode="web",
        engine_update_running=False,
        engine_update_result=None,
    )


class StateSignatureTests(unittest.TestCase):
    def test_unchanged_state_produces_equal_signatures(self) -> None:
        manager = _make_manager()

        self.assertEqual(_state_signature(manager), _state_signature(manager))

    def test_scalar_change_moves_signature(self) -> None:
        manager = _make_manager()
        before = _state_signature(manager)

        manager.state.drop_progress = 0.5

        self.assertNotEqual(before, _state_signature(manager))

    def test_channels_rev_moves_signature(self) -> None:
        manager = _make_manager()
        before = _state_signature(manager)

        manager.state.channels_rev += 1

        self.assertNotEqual(before, _state_signature(manager))

    def test_inventory_rev_moves_signature(self) -> None:
        manager = _make_manager()
        before = _state_signature(manager)

        manager.state.inventory_rev += 1

        self.assertNotEqual(before, _state_signature(manager))

    def test_settings_change_moves_signature(self) -> None:
        manager = _make_manager()
        before = _state_signature(manager)

        manager._twitch.settings.priority = ["Game A", "Game C"]

        self.assertNotEqual(before, _state_signature(manager))

    def test_websocket_status_moves_signature(self) -> None:
        manager = _make_manager()
        before = _state_signature(manager)

        manager.state.websockets[0] = ("connected", 3)

        self.assertNotEqual(before, _state_signature(manager))


if __name__ == "__main__":
    unittest.main()
