"""Update the pristine TwitchDropsMiner engine for source and Docker runs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ENGINE_REPOSITORY = "DevilXD/TwitchDropsMiner"
ENGINE_BRANCH = "master"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_ENGINE_DIR = _PROJECT_ROOT / "TwitchDropsMiner"
_VERSION_MARKER = ".tdm-engine-version"
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_EXTRACTED_BYTES = 512 * 1024 * 1024


class EngineUpdateError(RuntimeError):
    """Raised when the engine cannot be safely updated."""


@dataclass(frozen=True)
class UpdateResult:
    previous: str
    current: str
    changed: bool
    source: str

    @property
    def message(self) -> str:
        if self.changed:
            return f"Engine updated: {self.previous} -> {self.current} ({self.source})"
        return f"Engine is already up to date: {self.current} ({self.source})"


def update_engine() -> UpdateResult:
    """Update the engine selected for the current runtime."""
    if getattr(sys, "frozen", False):
        raise EngineUpdateError(
            "Engine updates are available only in source and Docker runs; "
            "frozen executables must be replaced as a whole."
        )

    external_dir = os.environ.get("TDM_ENGINE_DIR")
    if external_dir:
        return _update_snapshot(Path(external_dir).expanduser())
    return _update_git_submodule()


def _update_git_submodule() -> UpdateResult:
    if not (_PROJECT_ROOT / ".git").exists():
        raise EngineUpdateError(
            "This source tree has no Git metadata. Set TDM_ENGINE_DIR for "
            "snapshot-based updates."
        )

    previous = _git_commit(_BUNDLED_ENGINE_DIR) or "missing"
    command = [
        "git",
        "-C",
        str(_PROJECT_ROOT),
        "submodule",
        "update",
        "--init",
        "--remote",
        "--recursive",
        "--",
        "TwitchDropsMiner",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise EngineUpdateError("Git is required to update the source submodule.") from exc
    except subprocess.TimeoutExpired as exc:
        raise EngineUpdateError("Timed out while updating the engine submodule.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise EngineUpdateError(f"Git failed to update the engine: {detail}") from exc

    current = _git_commit(_BUNDLED_ENGINE_DIR)
    if current is None:
        detail = (completed.stderr or completed.stdout).strip()
        raise EngineUpdateError(f"Updated submodule has no readable commit: {detail}")
    return UpdateResult(previous, current, previous != current, "git")


def _git_commit(directory: Path) -> str | None:
    if not (directory / "twitch.py").is_file():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _update_snapshot(target: Path) -> UpdateResult:
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = _snapshot_commit(target) or "bundled"
    commit = _latest_commit()
    current = commit[:7]
    if previous == current or previous == commit:
        return UpdateResult(previous, current, False, "github")

    with tempfile.TemporaryDirectory(prefix="tdm-engine-", dir=target.parent) as tmp:
        temp_root = Path(tmp)
        archive = temp_root / "engine.tar.gz"
        candidate = temp_root / "engine"
        _download_archive(commit, archive)
        _extract_archive(archive, candidate)
        _validate_engine(candidate)
        (candidate / _VERSION_MARKER).write_text(commit + "\n", encoding="ascii")
        _install_candidate(candidate, target)

    return UpdateResult(previous, current, True, "github")


def _snapshot_commit(target: Path) -> str | None:
    marker = target / _VERSION_MARKER
    if not marker.is_file() or not (target / "twitch.py").is_file():
        return None
    try:
        value = marker.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return value[:7] if value else None


def _latest_commit() -> str:
    url = f"https://api.github.com/repos/{ENGINE_REPOSITORY}/commits/{ENGINE_BRANCH}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "TDMConsole-engine-updater",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise EngineUpdateError(f"Could not query the latest engine commit: {exc}") from exc
    commit = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(commit, str) or len(commit) < 7:
        raise EngineUpdateError("GitHub returned an invalid engine commit.")
    return commit


def _download_archive(commit: str, destination: Path) -> None:
    url = f"https://github.com/{ENGINE_REPOSITORY}/archive/{commit}.tar.gz"
    request = urllib.request.Request(url, headers={"User-Agent": "TDMConsole-engine-updater"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_ARCHIVE_BYTES:
                    raise EngineUpdateError("Engine archive exceeds the 128 MiB safety limit.")
                out.write(chunk)
    except EngineUpdateError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise EngineUpdateError(f"Could not download the engine archive: {exc}") from exc


def _extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise EngineUpdateError("Engine archive contains too many entries.")
            extracted_bytes = sum(member.size for member in members if member.isfile())
            if extracted_bytes > _MAX_EXTRACTED_BYTES:
                raise EngineUpdateError("Engine archive exceeds the 512 MiB extraction limit.")
            roots = {
                PurePosixPath(member.name).parts[0]
                for member in members
                if PurePosixPath(member.name).parts
            }
            if len(roots) != 1:
                raise EngineUpdateError("Engine archive has an unexpected directory layout.")
            root = next(iter(roots))
            for member in members:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[0] != root or len(parts) == 1:
                    continue
                relative_parts = parts[1:]
                if any(part in ("", ".", "..") for part in relative_parts):
                    raise EngineUpdateError("Engine archive contains an unsafe path.")
                output = destination.joinpath(*relative_parts)
                output.resolve().relative_to(destination.resolve())
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    output.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    if source is None:
                        raise EngineUpdateError(f"Could not read {member.name} from archive.")
                    with source, output.open("wb") as target_file:
                        shutil.copyfileobj(source, target_file)
                    output.chmod(member.mode & 0o777)
                else:
                    raise EngineUpdateError("Engine archive contains unsupported links or devices.")
    except EngineUpdateError:
        raise
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise EngineUpdateError(f"Could not extract the engine archive: {exc}") from exc


def _validate_engine(directory: Path) -> None:
    required = (
        directory / "twitch.py",
        directory / "constants.py",
        directory / "settings.py",
        directory / "gui.py",
    )
    missing = [str(path.relative_to(directory)) for path in required if not path.is_file()]
    lang_dir = directory / "lang"
    if not lang_dir.is_dir() or not any(lang_dir.glob("*.json")):
        missing.append("lang/*.json")
    if missing:
        raise EngineUpdateError("Engine archive is missing: " + ", ".join(missing))


def _install_candidate(candidate: Path, target: Path) -> None:
    if target.is_symlink():
        raise EngineUpdateError(f"Refusing to replace symlinked engine directory: {target}")
    backup = target.with_name(f".{target.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    try:
        if target.exists():
            target.rename(backup)
        candidate.rename(target)
    except OSError as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if backup.exists():
            backup.rename(target)
        raise EngineUpdateError(f"Could not install the updated engine: {exc}") from exc
    else:
        if backup.exists():
            shutil.rmtree(backup)
