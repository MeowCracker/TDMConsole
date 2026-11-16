from __future__ import annotations

from types import SimpleNamespace
import unittest

from tdm_cli.state import MinerState
from tdm_cli.web.server import _campaign_snapshot, snapshot


class CampaignSnapshotTests(unittest.TestCase):
    def test_includes_each_drop_reward_and_progress(self) -> None:
        campaign = SimpleNamespace(
            game=SimpleNamespace(name="Example Game"),
            name="Example Campaign",
            claimed_drops=1,
            total_drops=2,
            progress=0.5,
            active=True,
            upcoming=False,
            drops=[
                SimpleNamespace(
                    benefits=[
                        SimpleNamespace(
                            name="Hat", image_url="https://example.test/hat.png"
                        ),
                        SimpleNamespace(
                            name="Emote", image_url="https://example.test/emote.png"
                        ),
                    ],
                    is_claimed=False,
                    progress=0.25,
                    current_minutes=15,
                    required_minutes=60,
                )
            ],
        )

        result = _campaign_snapshot(campaign)

        self.assertEqual(result["game"], "Example Game")
        self.assertEqual(
            result["drops"],
            [
                {
                    "rewards": [
                        {"name": "Hat", "image": "https://example.test/hat.png"},
                        {"name": "Emote", "image": "https://example.test/emote.png"},
                    ],
                    "claimed": False,
                    "progress": 0.25,
                    "currentMinutes": 15,
                    "requiredMinutes": 60,
                }
            ],
        )


class DashboardSnapshotTests(unittest.TestCase):
    def _manager(self) -> SimpleNamespace:
        settings = SimpleNamespace(
            priority=["Game A"],
            exclude={"Game B"},
            proxy="",
            priority_mode=SimpleNamespace(name="PRIORITY_ONLY"),
        )
        return SimpleNamespace(
            state=MinerState(),
            _twitch=SimpleNamespace(settings=settings, channels={}, inventory=[]),
            channels=SimpleNamespace(get_selection=lambda: None),
            mode="web",
            restart_requested=False,
            engine_update_running=False,
            engine_update_result=None,
            engine_update_cron="0 4 * * *",
            engine_update_available=True,
            next_engine_update_at=lambda: "2026-11-17T04:00",
        )

    def test_engine_update_schedule_is_exposed_to_the_browser(self) -> None:
        result = snapshot(self._manager())

        self.assertEqual(
            result["engineUpdate"],
            {
                "cron": "0 4 * * *",
                "nextRun": "2026-11-17T04:00",
                "available": True,
            },
        )

    def test_disabled_schedule_reports_no_next_run(self) -> None:
        manager = self._manager()
        manager.engine_update_cron = ""
        manager.next_engine_update_at = lambda: None

        result = snapshot(manager)

        self.assertEqual(result["engineUpdate"]["cron"], "")
        self.assertIsNone(result["engineUpdate"]["nextRun"])


if __name__ == "__main__":
    unittest.main()
