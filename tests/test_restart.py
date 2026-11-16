from __future__ import annotations

import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from tdm_cli import bootstrap

bootstrap.setup()

from tdm_cli.gui import GUIManager, _runtime_timestamps, mark_process_restart
from tdm_cli.updater import EngineUpdateError


def make_manager() -> GUIManager:
    """A minimally-wired GUIManager (no frontend, no event loop tasks yet)."""
    manager = GUIManager.__new__(GUIManager)
    manager._engine_update_task = None
    manager._engine_update_result = None
    manager._engine_update_reason = None
    manager._engine_update_cron = ""
    manager._restart_requested = False
    manager._restart_task = None
    manager._last_connection_failure_at = None
    manager._long_reconnect_failures = 0
    manager._close_requested = asyncio.Event()
    manager._update_log = Mock()
    manager.close = Mock()
    # Writing the first-launch marker touches the data dir; keep tests offline.
    manager._mark_engine_bootstrap = Mock()
    # Engine refreshes are covered by EngineRefreshTests; default to "not
    # available" so the plain restart path stays observable here.
    manager.request_engine_update = Mock(return_value=False)
    return manager


class RestartTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self) -> GUIManager:
        return make_manager()

    @staticmethod
    async def fail_to_connect() -> None:
        raise aiohttp.ClientConnectionError()

    @staticmethod
    async def connect() -> str:
        return "connected"

    async def test_restart_uses_graceful_shutdown_path(self) -> None:
        manager = self.make_manager()

        with patch("tdm_cli.gui.asyncio.sleep", new=AsyncMock()) as sleep:
            self.assertTrue(manager.request_restart())
            await manager._restart_task

        self.assertTrue(manager.restart_requested)
        manager._update_log.assert_called_once_with(
            "Restarting TDMConsole...", "notify"
        )
        sleep.assert_awaited_once_with(0.75)
        manager.close.assert_called_once_with()

    async def test_restart_is_rejected_while_engine_update_runs(self) -> None:
        manager = self.make_manager()
        manager._engine_update_task = Mock()

        self.assertFalse(manager.request_restart())

        manager._update_log.assert_not_called()
        self.assertIsNone(manager._restart_task)

    async def test_duplicate_restart_is_rejected(self) -> None:
        manager = self.make_manager()

        with patch("tdm_cli.gui.asyncio.sleep", new=AsyncMock()):
            self.assertTrue(manager.request_restart())
            self.assertFalse(manager.request_restart())
            await manager._restart_task

        manager._update_log.assert_called_once()

    async def test_engine_update_uses_the_same_restart_scheduler(self) -> None:
        manager = self.make_manager()
        manager._engine_update_result = None
        manager._schedule_restart = Mock(return_value=True)
        result = SimpleNamespace(changed=True, message="Engine updated")

        with patch("tdm_cli.gui.asyncio.to_thread", new=AsyncMock(return_value=result)):
            await manager._update_engine()

        self.assertEqual(manager._engine_update_result, "updated")
        manager._schedule_restart.assert_called_once_with(
            "Restarting to load the updated engine...", "notify"
        )

    async def test_three_failed_180_second_reconnects_restart(self) -> None:
        manager = self.make_manager()
        manager.request_restart = Mock(return_value=True)
        clock = iter((0.0, 0.1, 180.1, 180.2, 360.2, 360.3, 540.3, 540.4))

        with patch("tdm_cli.gui.monotonic", side_effect=lambda: next(clock)):
            for _ in range(4):
                with self.assertRaises(aiohttp.ClientConnectionError):
                    await manager.coro_unless_closed(self.fail_to_connect())

        manager.request_restart.assert_called_once_with(
            "Twitch reconnect failed 3 times; restarting TDMConsole...",
            "error",
        )

    async def test_short_retry_resets_long_reconnect_failures(self) -> None:
        manager = self.make_manager()
        manager.request_restart = Mock(return_value=True)
        clock = iter(
            (
                0.0,
                0.1,
                180.1,
                180.2,
                360.2,
                360.3,
                370.3,
                370.4,
                550.4,
                550.5,
                730.5,
                730.6,
            )
        )

        with patch("tdm_cli.gui.monotonic", side_effect=lambda: next(clock)):
            for _ in range(6):
                with self.assertRaises(aiohttp.ClientConnectionError):
                    await manager.coro_unless_closed(self.fail_to_connect())

        manager.request_restart.assert_not_called()

    async def test_success_resets_reconnect_failures(self) -> None:
        manager = self.make_manager()
        manager._last_connection_failure_at = 100.0
        manager._long_reconnect_failures = 2

        with patch("tdm_cli.gui.monotonic", return_value=280.0):
            self.assertEqual(
                await manager.coro_unless_closed(self.connect()),
                "connected",
            )

        self.assertIsNone(manager._last_connection_failure_at)
        self.assertEqual(manager._long_reconnect_failures, 0)

    def test_restart_time_is_recorded_without_replacing_start_time(self) -> None:
        with (
            patch.dict("tdm_cli.gui.os.environ", {}, clear=True),
            patch("tdm_cli.gui.time", side_effect=(1_000.0, 2_000.0)),
        ):
            self.assertEqual(_runtime_timestamps(), (1_000.0, None))

            mark_process_restart()

            self.assertEqual(_runtime_timestamps(), (1_000.0, 2_000.0))


class EngineRefreshTests(unittest.IsolatedAsyncioTestCase):
    """Repeated reconnect failures refresh the engine before restarting."""

    async def _fail_long_reconnects(self, manager: GUIManager) -> None:
        clock = iter((0.0, 0.1, 180.1, 180.2, 360.2, 360.3, 540.3, 540.4))
        with patch("tdm_cli.gui.monotonic", side_effect=lambda: next(clock)):
            for _ in range(4):
                with self.assertRaises(aiohttp.ClientConnectionError):
                    await manager.coro_unless_closed(RestartTests.fail_to_connect())

    async def test_repeated_failures_refresh_the_engine_instead_of_restarting(
        self,
    ) -> None:
        manager = make_manager()
        manager.request_engine_update = Mock(return_value=True)
        manager.request_restart = Mock(return_value=True)

        await self._fail_long_reconnects(manager)

        manager.request_engine_update.assert_called_once_with(reason="reconnect")
        manager.request_restart.assert_not_called()

    async def test_repeated_failures_restart_when_updates_are_unavailable(self) -> None:
        manager = make_manager()
        manager.request_engine_update = Mock(return_value=False)
        manager.request_restart = Mock(return_value=True)

        await self._fail_long_reconnects(manager)

        manager.request_restart.assert_called_once_with(
            "Twitch reconnect failed 3 times; restarting TDMConsole...", "error"
        )

    async def test_current_engine_is_followed_by_a_restart(self) -> None:
        manager = make_manager()
        manager._engine_update_reason = "reconnect"
        manager._schedule_restart = Mock(return_value=True)
        result = SimpleNamespace(changed=False, message="Engine is already up to date")

        with patch("tdm_cli.gui.asyncio.to_thread", new=AsyncMock(return_value=result)):
            await manager._update_engine()

        self.assertEqual(manager._engine_update_result, "up_to_date")
        manager._schedule_restart.assert_called_once_with(
            "Engine is already current; restarting TDMConsole...", "notify"
        )
        self.assertIsNone(manager._engine_update_reason)

    async def test_failed_engine_update_still_restarts_after_reconnect(self) -> None:
        manager = make_manager()
        manager._engine_update_reason = "reconnect"
        manager._schedule_restart = Mock(return_value=True)

        with patch(
            "tdm_cli.gui.asyncio.to_thread",
            new=AsyncMock(side_effect=EngineUpdateError("no git metadata")),
        ):
            await manager._update_engine()

        self.assertEqual(manager._engine_update_result, "failed")
        manager._schedule_restart.assert_called_once_with(
            "Engine update unavailable; restarting TDMConsole...", "warn"
        )

    async def test_successful_update_records_the_first_launch_flag(self) -> None:
        manager = make_manager()
        manager._schedule_restart = Mock(return_value=True)
        result = SimpleNamespace(changed=True, message="Engine updated")

        with patch("tdm_cli.gui.asyncio.to_thread", new=AsyncMock(return_value=result)):
            await manager._update_engine()

        manager._mark_engine_bootstrap.assert_called_once_with()

    async def test_up_to_date_update_records_the_first_launch_flag(self) -> None:
        manager = make_manager()
        manager._schedule_restart = Mock(return_value=True)
        result = SimpleNamespace(changed=False, message="Engine is already up to date")

        with patch("tdm_cli.gui.asyncio.to_thread", new=AsyncMock(return_value=result)):
            await manager._update_engine()

        manager._mark_engine_bootstrap.assert_called_once_with()

    async def test_scheduled_update_does_not_restart_when_engine_is_current(
        self,
    ) -> None:
        manager = make_manager()
        manager._engine_update_reason = "schedule"
        manager._schedule_restart = Mock(return_value=True)
        result = SimpleNamespace(changed=False, message="Engine is already up to date")

        with patch("tdm_cli.gui.asyncio.to_thread", new=AsyncMock(return_value=result)):
            await manager._update_engine()

        manager._schedule_restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
