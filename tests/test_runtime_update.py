from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "TwitchDropsMiner"))

from tdm_cli import bootstrap

bootstrap.setup_paths()

from tdm_cli.gui import GUIManager
from tdm_cli.updater import UpdateResult


class RuntimeUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_engine_requests_graceful_restart(self) -> None:
        manager = SimpleNamespace(
            _engine_update_task=object(),
            _restart_requested=False,
            _update_log=Mock(),
            close=Mock(),
        )
        result = UpdateResult("old0000", "new0000", True, "github")

        with (
            patch("tdm_cli.updater.update_engine", return_value=result),
            patch("tdm_cli.gui.asyncio.sleep", new=AsyncMock()),
        ):
            await GUIManager._update_engine(manager)

        self.assertTrue(manager._restart_requested)
        manager.close.assert_called_once_with()
        self.assertIsNone(manager._engine_update_task)
        manager._update_log.assert_any_call(result.message, "success")

    async def test_unchanged_engine_keeps_running(self) -> None:
        manager = SimpleNamespace(
            _engine_update_task=object(),
            _restart_requested=False,
            _update_log=Mock(),
            close=Mock(),
        )
        result = UpdateResult("same000", "same000", False, "git")

        with patch("tdm_cli.updater.update_engine", return_value=result):
            await GUIManager._update_engine(manager)

        self.assertFalse(manager._restart_requested)
        manager.close.assert_not_called()
        self.assertIsNone(manager._engine_update_task)
        manager._update_log.assert_called_once_with(result.message, "success")


if __name__ == "__main__":
    unittest.main()
