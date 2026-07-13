"""UI-agnostic slash-command processor for the REPL frontend.

All ``/`` commands live here so the presentation layer stays swappable: output
goes through an injected ``out(text, style)`` callback with *semantic* styles
("info", "success", "warn", "error", "dim", "bold") that each UI maps to its
own colors (rich styles in the Textual REPL, ANSI in a plain terminal).
"""
from __future__ import annotations

import shlex
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from tdm_cli.gui import GUIManager

COMMANDS: dict[str, str] = {
    "/help": "show this help",
    "/status": "current status, watched channel and drop progress",
    "/channels": "list known channels",
    "/campaigns": "list inventory campaigns",
    "/games": "show priority & excluded games",
    "/priority": "/priority add|remove|up|down <game> — edit the priority list",
    "/exclude": "/exclude add|remove <game> — edit the exclusion list",
    "/pin": "/pin <channel> — pin & switch to a channel",
    "/unpin": "resume automatic channel selection",
    "/proxy": "/proxy <url|clear> — set or clear the proxy (reloads)",
    "/reload": "re-fetch inventory and channels",
    "/switch-mode": "/switch-mode tui|repl|headless — change interface mode",
    "/login": "show the pending device-code login prompt again",
    "/quit": "stop mining and exit",
}


class CommandProcessor:
    def __init__(self, manager: GUIManager, out: Callable[[str, str], None]) -> None:
        self._m = manager
        self._out_cb = out

    def _out(self, text: str, style: str = "") -> None:
        self._out_cb(text, style)

    # dispatch ---------------------------------------------------------------
    def dispatch(self, text: str) -> None:
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        if not parts:
            return
        cmd = parts[0].lower()
        args = parts[1:]
        if not cmd.startswith("/"):
            self._out("Commands start with '/'. Try /help.", "warn")
            return
        handler = getattr(self, f"_cmd_{cmd[1:].replace('-', '_')}", None)
        if handler is None:
            self._out(f"Unknown command: {cmd}. Try /help.", "warn")
            return
        try:
            handler(args)
        except Exception as exc:  # keep the UI alive on any command error
            self._out(f"Error in {cmd}: {exc}", "error")

    # commands ----------------------------------------------------------------
    def _cmd_help(self, args: list[str]) -> None:
        self._out("Commands:", "bold")
        for name, desc in COMMANDS.items():
            self._out(f"  {name:<14} {desc}")

    def _cmd_status(self, args: list[str]) -> None:
        s = self._m.state
        self._out(f"Status   : {s.status or '-'}", "info")
        user = f"user {s.user_id}" if s.user_id is not None else (s.login_status or "logged out")
        self._out(f"Login    : {user}")
        self._out(
            f"Watching : {s.watching_channel or '-'}"
            f"{f' ({s.watching_game})' if s.watching_game else ''}"
        )
        if s.drop_rewards:
            self._out(f"Drop     : {s.drop_rewards}  {s.drop_progress:.1%}  ⏱ {s.drop_remaining}")
            self._out(
                f"Campaign : {s.campaign_name}  "
                f"{s.campaign_claimed}/{s.campaign_total}  "
                f"{s.campaign_progress:.1%}  ⏱ {s.campaign_remaining}"
            )
        ws = ", ".join(f"#{i + 1} {st}" for i, (st, _t) in sorted(s.websockets.items())) or "-"
        self._out(f"Websocket: {ws}")

    def _cmd_channels(self, args: list[str]) -> None:
        channels = self._m._twitch.channels
        if not channels:
            self._out("No channels known yet.", "dim")
            return
        watching = self._m.state.watching_channel
        pinned = self._m.channels.get_selection()
        for ch in channels.values():
            mark = "▶" if ch.name == watching else ("📌" if pinned is ch else " ")
            game = ch.game.name if ch.game is not None else "-"
            status = "ONLINE" if ch.online else ("pending" if ch.pending_online else "offline")
            viewers = "-" if ch.viewers is None else str(ch.viewers)
            self._out(f" {mark} {ch.name:<20} {game:<24} {status:<8} {viewers:>7}")

    def _cmd_campaigns(self, args: list[str]) -> None:
        inv = self._m._twitch.inventory
        if not inv:
            self._out("Inventory is empty.", "dim")
            return
        for c in inv:
            state = "active" if c.active else ("upcoming" if c.upcoming else "expired")
            self._out(
                f" {c.game.name:<20} {c.name:<28} "
                f"{c.claimed_drops}/{c.total_drops:<4} {c.progress:4.0%}  {state}"
            )

    def _cmd_games(self, args: list[str]) -> None:
        settings = self._m._twitch.settings
        self._out(f"Priority ({settings.priority_mode.name}):", "bold")
        if settings.priority:
            for i, g in enumerate(settings.priority, 1):
                self._out(f"  {i}. {g}")
        else:
            self._out("  (none)", "dim")
        self._out("Excluded:", "bold")
        self._out("  " + (", ".join(sorted(settings.exclude)) or "(none)"))

    def _cmd_priority(self, args: list[str]) -> None:
        settings = self._m._twitch.settings
        if len(args) < 2:
            self._out("Usage: /priority add|remove|up|down <game>", "warn")
            return
        action = args[0].lower()
        game = " ".join(args[1:]).strip()
        priority = list(settings.priority)
        if action == "add":
            if game and game not in priority:
                priority.append(game)
                settings.exclude.discard(game)
        elif action in ("remove", "rm", "del"):
            if game in priority:
                priority.remove(game)
        elif action in ("up", "down"):
            if game not in priority:
                self._out(f"{game!r} is not in the priority list.", "warn")
                return
            idx = priority.index(game)
            new = idx - 1 if action == "up" else idx + 1
            if 0 <= new < len(priority):
                priority[idx], priority[new] = priority[new], priority[idx]
        else:
            self._out(f"Unknown action: {action}", "warn")
            return
        settings.priority = priority
        settings.save()
        self._m._twitch.change_state(self._state_enum().GAMES_UPDATE)
        self._cmd_games([])

    def _cmd_exclude(self, args: list[str]) -> None:
        settings = self._m._twitch.settings
        if len(args) < 2:
            self._out("Usage: /exclude add|remove <game>", "warn")
            return
        action = args[0].lower()
        game = " ".join(args[1:]).strip()
        exclude = set(settings.exclude)
        if action == "add":
            exclude.add(game)
            if game in settings.priority:
                settings.priority = [g for g in settings.priority if g != game]
        elif action in ("remove", "rm", "del"):
            exclude.discard(game)
        else:
            self._out(f"Unknown action: {action}", "warn")
            return
        settings.exclude = exclude
        settings.save()
        self._m._twitch.change_state(self._state_enum().GAMES_UPDATE)
        self._cmd_games([])

    def _cmd_pin(self, args: list[str]) -> None:
        if not args:
            self._out("Usage: /pin <channel>", "warn")
            return
        name = " ".join(args).strip().lower()
        for ch in self._m._twitch.channels.values():
            if ch.name.lower() == name:
                self._m.channels.select(ch)
                self._m.print(f"Pinned channel: {ch.name} — switching...")
                self._m._twitch.change_state(self._state_enum().CHANNEL_SWITCH)
                return
        self._out(f"No known channel named {name!r}. Try /channels.", "warn")

    def _cmd_unpin(self, args: list[str]) -> None:
        if self._m.channels.get_selection() is not None:
            self._m.channels.clear_selection()
            self._m.print("Unpinned — automatic channel selection resumes.")
            self._m._twitch.change_state(self._state_enum().CHANNEL_SWITCH)
        else:
            self._out("No channel is pinned.", "dim")

    def _cmd_proxy(self, args: list[str]) -> None:
        from yarl import URL

        settings = self._m._twitch.settings
        if not args:
            self._out(f"Proxy: {settings.proxy or '(none)'}")
            return
        value = args[0]
        settings.proxy = URL() if value.lower() in ("clear", "none", "-") else URL(value)
        settings.save()
        self._out(f"Proxy set to {settings.proxy or '(none)'} — reloading...", "info")
        self._m._twitch.change_state(self._state_enum().RESTART)

    def _cmd_reload(self, args: list[str]) -> None:
        self._m.print("Reload requested...")
        self._m._twitch.change_state(self._state_enum().RESTART)

    def _cmd_switch_mode(self, args: list[str]) -> None:
        from tdm_cli.prefs import MODES

        if not args or args[0] not in MODES:
            self._out(f"Usage: /switch-mode {'|'.join(MODES)}", "warn")
            return
        target = args[0]
        if target == self._m.mode:
            self._out(f"Already in {target} mode.", "dim")
            return
        self._out(f"Switching to {target} mode...", "info")
        self._m.request_frontend(target)

    def _cmd_login(self, args: list[str]) -> None:
        s = self._m.state
        if s.login_prompt and s.login_url:
            self._out("Twitch login required", "bold")
            self._out("1. Open this URL in a browser (any device):")
            self._out(f"     {s.login_url}", "info")
            self._out("2. Enter this code:")
            self._out(f"     {s.login_code}", "success")
            self._out("Mining starts automatically once you authorize.")
        else:
            self._out("No pending login.", "dim")

    def _cmd_quit(self, args: list[str]) -> None:
        self._m.print("Quit requested, shutting down...")
        self._m.close()

    _cmd_exit = _cmd_quit

    # helpers ------------------------------------------------------------------
    def _state_enum(self):
        from constants import State

        return State
