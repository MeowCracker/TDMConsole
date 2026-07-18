from __future__ import annotations

import unittest
from unittest.mock import Mock

from tdm_cli.commands import CommandProcessor


class UpdateCommandTests(unittest.TestCase):
    def test_update_dispatches_to_manager(self) -> None:
        manager = Mock()
        manager.request_engine_update.return_value = True
        output = Mock()

        CommandProcessor(manager, output).dispatch("/update")

        manager.request_engine_update.assert_called_once_with()
        output.assert_not_called()

    def test_duplicate_update_reports_warning(self) -> None:
        manager = Mock()
        manager.request_engine_update.return_value = False
        output = Mock()

        CommandProcessor(manager, output).dispatch("/update")

        output.assert_called_once_with("An engine update is already running.", "warn")


if __name__ == "__main__":
    unittest.main()
