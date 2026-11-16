"""Engine-update schedule: a small 5-field cron parser + the CLI sidecar config.

The upstream ``settings.json`` belongs to the pristine submodule — its
``Settings.save()`` writes only the keys it knows about, so any field we add
there would be silently dropped on the next save. Scheduling therefore lives in
``tdm-cli.json`` in the same working directory, which this project owns
outright (``TDM_DATA_DIR`` relocates it alongside the rest of the runtime
state).

Cron dialect: standard 5 fields (``minute hour day-of-month month
day-of-week``), supporting ``*``, ``?``, integers, month/weekday names,
ranges (``a-b``), lists (``a,b,c``) and steps (``*/n``, ``a-b/n``, ``a/n``).
Day-of-month and day-of-week combine with OR when both are restricted, which
is what cron implementations do. Matching uses the process' local clock;
seconds are ignored.
"""

from __future__ import annotations

import json
import os
import tempfile
from bisect import bisect_left
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from functools import lru_cache
from pathlib import Path

CLI_CONFIG_FILENAME = "tdm-cli.json"
ENGINE_UPDATE_CRON_KEY = "engine_update_cron"

# Values accepted instead of an expression to switch the schedule off.
DISABLED_WORDS = frozenset({"off", "none", "disabled", "disable", "clear", "-"})

_MONTH_NAMES = {
    name: index
    for index, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
         "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
        start=1,
    )
}
_WEEKDAY_NAMES = {
    name: index
    for index, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))
}

# (label, min, max, names) — one entry per cron field, in order.
_FIELDS = (
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day of month", 1, 31, None),
    ("month", 1, 12, _MONTH_NAMES),
    ("day of week", 0, 7, _WEEKDAY_NAMES),
)

# How far ahead next_run() searches before giving up (e.g. Feb 29 gaps).
_MAX_SEARCH_DAYS = 366 * 8


class ScheduleError(ValueError):
    """Base error for an unusable schedule (bad cron text or unwritable config)."""


class CronError(ScheduleError):
    """Raised when a cron expression cannot be parsed."""


def _parse_number(text: str, label: str, names: dict[str, int] | None) -> int:
    token = text.strip()
    if not token:
        raise CronError(f"empty value in the {label} field")
    if names is not None:
        named = names.get(token.upper())
        if named is not None:
            return named
    try:
        return int(token)
    except ValueError:
        raise CronError(f"{token!r} is not a number in the {label} field") from None


def _expand_field(
    spec: str, label: str, low: int, high: int, names: dict[str, int] | None
) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"empty list item in the {label} field")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            try:
                step = int(step_text)
            except ValueError:
                raise CronError(
                    f"{step_text!r} is not a valid step in the {label} field"
                ) from None
            if step <= 0:
                raise CronError(f"step must be positive in the {label} field")
        base = base.strip()
        if base in ("*", "?"):
            start, end = low, high
        elif "-" in base:
            start_text, _, end_text = base.partition("-")
            start = _parse_number(start_text, label, names)
            end = _parse_number(end_text, label, names)
        else:
            start = _parse_number(base, label, names)
            # "5/15" means "from 5 onwards, every 15": a bare value followed by
            # a step is a range start, without one it is a single value.
            end = high if step_text else start
        if start > end:
            raise CronError(f"range {start}-{end} is reversed in the {label} field")
        if start < low or end > high:
            raise CronError(f"{label} value out of range {low}-{high} in {part!r}")
        values.update(range(start, end + 1, step))
    return values


@dataclass(frozen=True)
class CronExpression:
    """A parsed cron expression, ready to match or to project the next run."""

    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_restricted: bool
    weekday_restricted: bool
    times: tuple[tuple[int, int], ...]

    @staticmethod
    def _weekday_of(day: date) -> int:
        # cron: 0 = Sunday; date.weekday(): 0 = Monday.
        return (day.weekday() + 1) % 7

    def _date_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        day_ok = day.day in self.days
        weekday_ok = self._weekday_of(day) in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return day_ok or weekday_ok
        return day_ok and weekday_ok

    def matches(self, moment: datetime) -> bool:
        """Whether *moment* (to the minute) is a firing time."""
        return (
            moment.minute in self.minutes
            and moment.hour in self.hours
            and self._date_matches(moment.date())
        )

    def next_run(self, after: datetime) -> datetime | None:
        """First firing time strictly after *after*, or None if unreachable."""
        start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        start_key = (start.hour, start.minute)
        times = self.times
        for offset in range(_MAX_SEARCH_DAYS):
            current = start.date() + timedelta(days=offset)
            if not self._date_matches(current):
                continue
            # On the first day everything before *start* is already spent.
            begin = bisect_left(times, start_key) if offset == 0 else 0
            if begin < len(times):
                hour, minute = times[begin]
                return datetime.combine(current, dtime(hour, minute))
        return None


@lru_cache(maxsize=16)
def parse_cron(expression: str) -> CronExpression:
    """Parse a 5-field cron expression; raises :class:`CronError` on bad input."""
    text = " ".join(str(expression or "").split())
    if not text:
        raise CronError("the expression is empty")
    fields = text.split(" ")
    if len(fields) != 5:
        raise CronError(
            f"expected 5 fields (minute hour day month weekday), got {len(fields)}"
        )

    minutes, hours, days, months, weekdays = (
        _expand_field(spec, label, low, high, names)
        for spec, (label, low, high, names) in zip(fields, _FIELDS)
    )
    # Both 0 and 7 mean Sunday.
    weekdays = {0 if value == 7 else value for value in weekdays}

    return CronExpression(
        expression=text,
        minutes=frozenset(minutes),
        hours=frozenset(hours),
        days=frozenset(days),
        months=frozenset(months),
        weekdays=frozenset(weekdays),
        day_restricted=days != set(range(1, 32)),
        weekday_restricted=weekdays != set(range(0, 7)),
        times=tuple(
            sorted((hour, minute) for hour in hours for minute in minutes)
        ),
    )


def normalize_cron(expression: str) -> str:
    """Return the canonical expression, or ``""`` when the schedule is off.

    Raises :class:`CronError` if the text is neither empty/``off`` nor a valid
    cron expression — callers surface that to the user instead of storing junk.
    """
    text = " ".join(str(expression or "").split())
    if not text or text.lower() in DISABLED_WORDS:
        return ""
    return parse_cron(text).expression


# --------------------------------------------------------------------------- #
# Sidecar configuration (owned by this project, unlike upstream settings.json)
# --------------------------------------------------------------------------- #
def cli_config_path() -> Path:
    """Where ``tdm-cli.json`` lives — the same dir as settings.json/cookies.jar."""
    from constants import WORKING_DIR

    return Path(WORKING_DIR) / CLI_CONFIG_FILENAME


def load_cli_config(path: Path | str | None = None) -> dict[str, object]:
    """Read the sidecar config; a missing or malformed file reads as empty."""
    target = Path(path) if path is not None else cli_config_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cli_config(data: dict[str, object], path: Path | str | None = None) -> None:
    """Atomically replace the sidecar config (unknown keys are preserved)."""
    target = Path(path) if path is not None else cli_config_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=".tdm-cli-", suffix=".tmp"
        )
    except OSError as exc:
        raise ScheduleError(f"could not write {target}: {exc}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, target)
    except OSError as exc:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise ScheduleError(f"could not write {target}: {exc}") from exc


def load_engine_update_cron(path: Path | str | None = None) -> str:
    """Stored cron expression, or ``""`` when scheduling is off."""
    value = load_cli_config(path).get(ENGINE_UPDATE_CRON_KEY, "")
    if not isinstance(value, str):
        return ""
    try:
        return normalize_cron(value)
    except CronError:
        # A hand-edited/corrupt value must not crash startup; treat it as off.
        return ""


def store_engine_update_cron(expression: str, path: Path | str | None = None) -> str:
    """Validate + persist the expression; returns the canonical form."""
    normalized = normalize_cron(expression)
    config = load_cli_config(path)
    if normalized:
        config[ENGINE_UPDATE_CRON_KEY] = normalized
    else:
        config.pop(ENGINE_UPDATE_CRON_KEY, None)
    save_cli_config(config, path)
    return normalized
