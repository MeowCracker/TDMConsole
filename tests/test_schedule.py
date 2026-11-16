"""Tests for the engine-update schedule: cron parsing, storage and wiring."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from tdm_cli import bootstrap

bootstrap.setup()

from tdm_cli.gui import GUIManager
from tdm_cli.schedule import (
    CronError,
    load_engine_update_cron,
    normalize_cron,
    parse_cron,
    store_engine_update_cron,
)

MONDAY = date(2026, 11, 16)  # a Monday
TUESDAY = date(2026, 11, 17)
SUNDAY = date(2026, 11, 15)


class CronParsingTests(unittest.TestCase):
    def test_daily_expression_matches_only_its_minute(self) -> None:
        cron = parse_cron("0 4 * * *")

        self.assertTrue(cron.matches(datetime(2026, 11, 16, 4, 0)))
        self.assertFalse(cron.matches(datetime(2026, 11, 16, 4, 1)))

    def test_next_run_is_strictly_after_the_reference_minute(self) -> None:
        cron = parse_cron("0 4 * * *")

        self.assertEqual(
            cron.next_run(datetime(2026, 11, 16, 3, 59)), datetime(2026, 11, 16, 4, 0)
        )
        self.assertEqual(
            cron.next_run(datetime(2026, 11, 16, 4, 0)), datetime(2026, 11, 17, 4, 0)
        )

    def test_steps_ranges_and_weekday_names(self) -> None:
        cron = parse_cron("*/15 9-17 * * MON-FRI")

        self.assertEqual(sorted(cron.minutes), [0, 15, 30, 45])
        self.assertEqual(sorted(cron.hours), list(range(9, 18)))
        self.assertTrue(cron.matches(datetime(MONDAY.year, MONDAY.month, MONDAY.day, 9, 30)))
        self.assertFalse(
            cron.matches(datetime(SUNDAY.year, SUNDAY.month, SUNDAY.day, 9, 30))
        )

    def test_day_of_month_and_weekday_combine_with_or(self) -> None:
        cron = parse_cron("0 0 1 * MON")

        # First of the month, even though it is a Tuesday.
        self.assertTrue(cron.matches(datetime(2026, 12, 1, 0, 0)))
        # Any Monday, even though it is not the first.
        self.assertTrue(
            cron.matches(datetime(MONDAY.year, MONDAY.month, MONDAY.day, 0, 0))
        )
        # Neither the first nor a Monday.
        self.assertFalse(
            cron.matches(datetime(TUESDAY.year, TUESDAY.month, TUESDAY.day, 0, 0))
        )

    def test_seven_is_accepted_as_sunday(self) -> None:
        self.assertEqual(
            parse_cron("0 0 * * 7").weekdays, parse_cron("0 0 * * 0").weekdays
        )

    def test_month_names_and_step_from_a_value(self) -> None:
        cron = parse_cron("5/20 * * JAN *")

        self.assertEqual(sorted(cron.minutes), [5, 25, 45])
        self.assertEqual(sorted(cron.months), [1])

    def test_next_run_skips_months_without_matches(self) -> None:
        cron = parse_cron("0 0 25 12 *")

        self.assertEqual(
            cron.next_run(datetime(2026, 11, 16, 0, 0)), datetime(2026, 12, 25, 0, 0)
        )

    def test_invalid_expressions_raise(self) -> None:
        for expression in (
            "",
            "0 4 * *",
            "0 4 * * * *",
            "60 * * * *",
            "* 24 * * *",
            "*/0 * * * *",
            "5-1 * * * *",
            "abc * * * *",
            "0 0 0 * *",
        ):
            with self.subTest(expression=expression):
                with self.assertRaises(CronError):
                    parse_cron(expression)


class NormalizeTests(unittest.TestCase):
    def test_whitespace_is_collapsed(self) -> None:
        self.assertEqual(normalize_cron("  0   4  *  *  * "), "0 4 * * *")

    def test_off_words_disable_the_schedule(self) -> None:
        for word in ("", "   ", "off", "OFF", "none", "disable", "clear", "-"):
            with self.subTest(word=word):
                self.assertEqual(normalize_cron(word), "")


class SchedulePersistenceTests(unittest.TestCase):
    def _path(self, tmp: str) -> Path:
        return Path(tmp) / "tdm-cli.json"

    def test_round_trip_and_disable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)

            self.assertEqual(load_engine_update_cron(path), "")
            self.assertEqual(store_engine_update_cron("0 4 * * *", path), "0 4 * * *")
            self.assertEqual(load_engine_update_cron(path), "0 4 * * *")
            self.assertEqual(store_engine_update_cron("off", path), "")
            self.assertEqual(load_engine_update_cron(path), "")

    def test_other_keys_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            path.write_text(json.dumps({"something_else": 7}), encoding="utf-8")

            store_engine_update_cron("30 2 * * *", path)

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["something_else"], 7)
            self.assertEqual(payload["engine_update_cron"], "30 2 * * *")

    def test_corrupt_or_foreign_values_read_as_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            for payload in ('{"engine_update_cron": "not a cron"}', "[1, 2]", "{"):
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding="utf-8")
                    self.assertEqual(load_engine_update_cron(path), "")

    def test_invalid_expression_does_not_touch_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            store_engine_update_cron("0 4 * * *", path)
            before = path.read_text(encoding="utf-8")

            with self.assertRaises(CronError):
                store_engine_update_cron("99 * * * *", path)

            self.assertEqual(path.read_text(encoding="utf-8"), before)


class ManagerScheduleTests(unittest.TestCase):
    def _manager(self) -> GUIManager:
        manager = GUIManager.__new__(GUIManager)
        manager._engine_update_cron = ""
        manager._update_log = Mock()
        return manager

    def test_set_cron_persists_and_exposes_the_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tdm-cli.json"
            manager = self._manager()

            with patch("tdm_cli.schedule.cli_config_path", return_value=path):
                self.assertEqual(
                    manager.set_engine_update_cron("0 4 * * *"), "0 4 * * *"
                )

            self.assertEqual(manager.engine_update_cron, "0 4 * * *")
            self.assertEqual(load_engine_update_cron(path), "0 4 * * *")
            self.assertEqual(
                manager.next_engine_update_at(datetime(2026, 11, 16, 12, 0)),
                "2026-11-17T04:00",
            )

    def test_invalid_expression_leaves_the_previous_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tdm-cli.json"
            manager = self._manager()

            with patch("tdm_cli.schedule.cli_config_path", return_value=path):
                manager.set_engine_update_cron("0 4 * * *")
                with self.assertRaises(CronError):
                    manager.set_engine_update_cron("* * *")

            self.assertEqual(manager.engine_update_cron, "0 4 * * *")
            self.assertEqual(load_engine_update_cron(path), "0 4 * * *")

    def test_disabling_reports_no_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tdm-cli.json"
            manager = self._manager()

            with patch("tdm_cli.schedule.cli_config_path", return_value=path):
                manager.set_engine_update_cron("0 4 * * *")
                manager.set_engine_update_cron("off")

            self.assertEqual(manager.engine_update_cron, "")
            self.assertIsNone(manager.next_engine_update_at())

    def test_engine_updates_are_unavailable_in_a_frozen_build(self) -> None:
        manager = self._manager()

        with patch("tdm_cli.gui.sys.frozen", True, create=True):
            self.assertFalse(manager.engine_update_available)

    def test_next_engine_update_at_tolerates_a_corrupt_schedule(self) -> None:
        manager = self._manager()
        manager._engine_update_cron = "not a cron"

        self.assertIsNone(manager.next_engine_update_at())


class _StopLoop(Exception):
    """Sentinel that unwinds the scheduler after one iteration in tests."""


class SchedulerLoopTests(unittest.IsolatedAsyncioTestCase):
    """The poll loop fires once per matching minute and skips when busy."""

    async def _run_one_tick(self, manager: GUIManager, now: datetime) -> None:
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003 - datetime API shape
                return now

        calls = 0

        async def fake_sleep(_seconds: float) -> None:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise _StopLoop

        with (
            patch("tdm_cli.gui.asyncio.sleep", new=fake_sleep),
            patch("tdm_cli.gui.datetime", FrozenDatetime),
        ):
            with self.assertRaises(_StopLoop):
                # Call the unbound coroutine: a plain Mock attribute is not
                # awaitable, and this is the real loop under test.
                await GUIManager._engine_schedule_loop(manager)

    def _manager(self) -> Mock:
        manager = Mock()
        manager.engine_update_cron = "0 4 * * *"
        manager.engine_update_available = True
        manager._engine_update_task = None
        manager._restart_requested = False
        manager.request_engine_update.return_value = True
        return manager

    async def test_matching_minute_requests_an_update(self) -> None:
        manager = self._manager()

        await self._run_one_tick(manager, datetime(2026, 11, 16, 4, 0, 5))

        manager.request_engine_update.assert_called_once_with(reason="schedule")

    async def test_non_matching_minute_does_nothing(self) -> None:
        manager = self._manager()

        await self._run_one_tick(manager, datetime(2026, 11, 16, 4, 1))

        manager.request_engine_update.assert_not_called()

    async def test_pending_restart_suppresses_the_scheduled_update(self) -> None:
        manager = self._manager()
        manager._restart_requested = True

        await self._run_one_tick(manager, datetime(2026, 11, 16, 4, 0, 5))

        manager.request_engine_update.assert_not_called()

    async def test_disabled_schedule_never_fires(self) -> None:
        manager = self._manager()
        manager.engine_update_cron = ""

        await self._run_one_tick(manager, datetime(2026, 11, 16, 4, 0, 5))

        manager.request_engine_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
