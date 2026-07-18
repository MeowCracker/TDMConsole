from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tdm_cli import updater


def _write_archive(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(f"TwitchDropsMiner-test/{name}")
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))


class SnapshotUpdateTests(unittest.TestCase):
    def test_installs_snapshot_and_skips_same_commit(self) -> None:
        commit = "abcdef0123456789abcdef0123456789abcdef01"
        required = {
            "twitch.py": b"# twitch\n",
            "constants.py": b"# constants\n",
            "settings.py": b"# settings\n",
            "gui.py": b"# gui\n",
            # Upstream generates English.json at runtime; GitHub source
            # archives contain only the translated catalogues.
            "lang/Deutsch.json": b"{}\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "TwitchDropsMiner"

            def fake_download(_commit: str, destination: Path) -> None:
                _write_archive(destination, required)

            with (
                patch.object(updater, "_latest_commit", return_value=commit),
                patch.object(updater, "_download_archive", side_effect=fake_download) as download,
            ):
                first = updater._update_snapshot(target)
                second = updater._update_snapshot(target)

            self.assertTrue(first.changed)
            self.assertEqual(first.current, "abcdef0")
            self.assertFalse(second.changed)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(
                (target / ".tdm-engine-version").read_text(encoding="ascii").strip(),
                commit,
            )
            self.assertTrue((target / "lang" / "Deutsch.json").is_file())

    def test_rejects_snapshot_without_language_catalogues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp)
            for name in ("twitch.py", "constants.py", "settings.py", "gui.py"):
                (candidate / name).write_text("# test\n", encoding="ascii")

            with self.assertRaisesRegex(updater.EngineUpdateError, r"lang/\*\.json"):
                updater._validate_engine(candidate)

    def test_rejects_archive_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "bad.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                content = b"bad"
                info = tarfile.TarInfo("repo/../escape.txt")
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            with self.assertRaises(updater.EngineUpdateError):
                updater._extract_archive(archive_path, Path(tmp) / "output")

    def test_frozen_build_cannot_update_embedded_engine(self) -> None:
        with patch.object(updater.sys, "frozen", True, create=True):
            with self.assertRaisesRegex(updater.EngineUpdateError, "source and Docker"):
                updater.update_engine()


if __name__ == "__main__":
    unittest.main()
