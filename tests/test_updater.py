from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tdm_cli.updater import EngineUpdateError, _extract_archive


def _write_member(bundle: tarfile.TarFile, name: str, content: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    bundle.addfile(member, io.BytesIO(content))


class ArchiveExtractionTests(unittest.TestCase):
    def test_extracts_a_normal_single_root_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "engine.tar.gz"
            destination = root / "engine"
            with tarfile.open(archive, "w:gz") as bundle:
                _write_member(bundle, "upstream-123/twitch.py", b"engine")

            _extract_archive(archive, destination)

            self.assertEqual((destination / "twitch.py").read_bytes(), b"engine")

    def test_rejects_archive_larger_than_extraction_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "engine.tar.gz"
            destination = root / "engine"
            with tarfile.open(archive, "w:gz") as bundle:
                _write_member(bundle, "upstream-123/large-file", b"01234567890")

            with patch("tdm_cli.updater._MAX_EXTRACTED_BYTES", 10):
                with self.assertRaisesRegex(EngineUpdateError, "extraction limit"):
                    _extract_archive(archive, destination)

            self.assertEqual(list(destination.iterdir()), [])

    def test_rejects_archive_with_too_many_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "engine.tar.gz"
            destination = root / "engine"
            with tarfile.open(archive, "w:gz") as bundle:
                _write_member(bundle, "upstream-123/first", b"1")
                _write_member(bundle, "upstream-123/second", b"2")

            with patch("tdm_cli.updater._MAX_ARCHIVE_MEMBERS", 1):
                with self.assertRaisesRegex(EngineUpdateError, "too many entries"):
                    _extract_archive(archive, destination)

            self.assertEqual(list(destination.iterdir()), [])
