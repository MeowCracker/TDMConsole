"""Shared, frontend-agnostic miner state.

Sub-components in :mod:`tdm_cli.gui` write here; frontends read from here.
The headless frontend prints deltas as they happen, while the Textual TUI
renders the whole state on a refresh timer — so this is plain data with a
couple of monotonic counters, no callbacks.

Everything runs on one asyncio loop; no locking is needed.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class LogEntry:
    seq: int
    stamp: str  # HH:MM:SS, captured at emit time
    text: str
    style: str = ""  # "", "warn", "error", "notify"


@dataclass
class MinerState:
    # Header
    status: str = ""
    login_status: str = ""
    user_id: int | None = None
    # idx -> (status text, topic count)
    websockets: dict[int, tuple[str, int]] = field(default_factory=dict)

    # Currently watched channel
    watching_channel: str = ""
    watching_game: str = ""

    # Active drop / campaign progress (mirrors upstream CampaignProgress vars)
    drop_rewards: str = ""
    drop_progress: float = 0.0
    drop_remaining: str = "-:--:--"
    campaign_name: str = ""
    campaign_game: str = ""
    campaign_progress: float = 0.0
    campaign_claimed: int = 0
    campaign_total: int = 0
    campaign_remaining: str = "-:--:--"

    # Device-code login. ``login_available`` = there IS a pending device-code
    # login the user can open; ``login_prompt`` = the user has chosen to show
    # the modal. The modal never pops on its own — only on /login or the
    # dashboard's Login action.
    login_available: bool = False
    login_prompt: bool = False
    login_url: str = ""
    login_code: str = ""

    # Table revision counters — frontends rebuild their tables when these move.
    channels_rev: int = 0
    inventory_rev: int = 0

    # Log ring buffer
    log_lines: deque[LogEntry] = field(default_factory=lambda: deque(maxlen=500))
    _log_seq: int = 0

    def add_log(self, text: str, style: str = "") -> LogEntry:
        self._log_seq += 1
        entry = LogEntry(
            seq=self._log_seq,
            stamp=datetime.now().strftime("%X"),
            text=text,
            style=style,
        )
        self.log_lines.append(entry)
        return entry

    def logs_since(self, seq: int) -> list[LogEntry]:
        return [entry for entry in self.log_lines if entry.seq > seq]

    def clear_drop(self) -> None:
        self.drop_rewards = ""
        self.drop_progress = 0.0
        self.drop_remaining = "-:--:--"
        self.campaign_name = ""
        self.campaign_game = ""
        self.campaign_progress = 0.0
        self.campaign_claimed = 0
        self.campaign_total = 0
        self.campaign_remaining = "-:--:--"
