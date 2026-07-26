"""The whole design bets on tdm_cli.gui matching the upstream submodule's
expectations — run bootstrap's own contract check as a test so CI catches an
upstream interface drift on the pinned submodule."""
from __future__ import annotations

import unittest
from pathlib import Path

_SUBMODULE = Path(__file__).resolve().parent.parent / "TwitchDropsMiner"


@unittest.skipUnless(
    (_SUBMODULE / "twitch.py").is_file(),
    "TwitchDropsMiner submodule not initialised",
)
class GuiShimContractTests(unittest.TestCase):
    def test_gui_shim_matches_upstream(self) -> None:
        # bootstrap.setup() is a process-wide one-shot (sys.path + sys.modules);
        # it is idempotent, so coexisting with other tests is safe.
        from tdm_cli import bootstrap

        bootstrap.setup()

        self.assertEqual(bootstrap.verify_contract(), [])


if __name__ == "__main__":
    unittest.main()
