from __future__ import annotations

from types import SimpleNamespace
import unittest

from tdm_cli.web.server import _campaign_snapshot


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
                    benefits=[SimpleNamespace(name="Hat"), SimpleNamespace(name="Emote")],
                    is_claimed=False,
                    progress=0.25,
                    current_minutes=15,
                    required_minutes=60,
                )
            ],
        )

        result = _campaign_snapshot(campaign)

        self.assertEqual(result["game"], "Example Game")
        self.assertEqual(result["drops"], [{
            "rewards": ["Hat", "Emote"],
            "claimed": False,
            "progress": 0.25,
            "currentMinutes": 15,
            "requiredMinutes": 60,
        }])


if __name__ == "__main__":
    unittest.main()
