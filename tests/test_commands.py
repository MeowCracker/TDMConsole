from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tdm_cli.commands import COMMANDS, CommandProcessor


def _make_processor(mode: str = "repl") -> tuple[CommandProcessor, SimpleNamespace, list[tuple[str, str]]]:
    manager = SimpleNamespace(mode=mode, request_frontend=Mock())
    out: list[tuple[str, str]] = []
    processor = CommandProcessor(manager, lambda text, style="": out.append((text, style)))
    return processor, manager, out


class SwitchModeTests(unittest.TestCase):
    def test_gui_is_rejected_with_hint(self) -> None:
        processor, manager, out = _make_processor()

        processor.dispatch("/switch-mode gui")

        self.assertTrue(any("restart with --mode gui" in text for text, _ in out))
        manager.request_frontend.assert_not_called()

    def test_valid_mode_requests_frontend(self) -> None:
        processor, manager, _out = _make_processor(mode="repl")

        processor.dispatch("/switch-mode web")

        manager.request_frontend.assert_called_once_with("web")

    def test_same_mode_is_a_noop(self) -> None:
        processor, manager, out = _make_processor(mode="web")

        processor.dispatch("/switch-mode web")

        manager.request_frontend.assert_not_called()
        self.assertTrue(any("Already in web mode" in text for text, _ in out))

    def test_usage_lists_web_but_not_gui(self) -> None:
        processor, manager, out = _make_processor()

        processor.dispatch("/switch-mode")

        usage = next(text for text, _ in out if text.startswith("Usage:"))
        self.assertIn("web", usage)
        self.assertNotIn("gui", usage)
        manager.request_frontend.assert_not_called()

    def test_help_text_mentions_web(self) -> None:
        self.assertIn("web", COMMANDS["/switch-mode"])


if __name__ == "__main__":
    unittest.main()
