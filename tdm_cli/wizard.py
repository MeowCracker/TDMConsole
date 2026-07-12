"""Interactive settings.json generator — ``uv run main.py init``.

Plain ``input()`` prompts so it works over SSH and in pipes; writes the file
with the upstream serializer (``utils.json_save``) so the result is exactly
what the pristine core expects to load.

Import this only after :func:`tdm_cli.bootstrap.setup` has run.
"""
from __future__ import annotations

from pathlib import Path


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def _ask_int(prompt: str, default: int, lo: int, hi: int) -> int:
    while True:
        raw = _ask(prompt, str(default))
        try:
            value = int(raw)
        except ValueError:
            print(f"  Please enter a number between {lo} and {hi}.")
            continue
        if lo <= value <= hi:
            return value
        print(f"  Please enter a number between {lo} and {hi}.")


def _ask_list(prompt: str) -> list[str]:
    raw = _ask(prompt)
    return [item.strip() for item in raw.split(",") if item.strip()]


def run_wizard(settings_path: Path) -> int:
    """Interactive Q&A; returns a process exit code."""
    from yarl import URL

    from utils import json_save
    from settings import default_settings
    from constants import LANG_PATH, DEFAULT_LANG, PriorityMode

    print(f"TDM-CLI settings wizard — writes {settings_path}")
    print("Press Enter to accept the [default] on any question.\n")

    if settings_path.exists():
        overwrite = _ask(f"{settings_path.name} already exists. Overwrite? (y/N)", "n")
        if overwrite.lower() not in ("y", "yes"):
            print("Aborted — nothing written.")
            return 1

    # 1. Language
    languages = sorted({DEFAULT_LANG, *(p.stem for p in LANG_PATH.glob("*.json"))})
    print("Available languages:")
    for index, name in enumerate(languages, start=1):
        print(f"  {index:>2}. {name}")
    raw = _ask("Language (number or name)", DEFAULT_LANG)
    if raw.isdigit() and 1 <= int(raw) <= len(languages):
        language = languages[int(raw) - 1]
    elif raw in languages:
        language = raw
    else:
        print(f"  Unknown language {raw!r} — using {DEFAULT_LANG}.")
        language = DEFAULT_LANG

    # 2. Proxy
    proxy_raw = _ask("Proxy URL, e.g. http://127.0.0.1:7890 (blank = none)")

    # 3./4. Games
    priority = _ask_list("Priority games, comma-separated (blank = none)")
    exclude = _ask_list("Excluded games, comma-separated (blank = none)")

    # 5. Priority mode
    print("Priority mode:")
    print("  1. Priority list only (mine only the games above)")
    print("  2. Ending soonest first")
    print("  3. Low availability first")
    mode = {
        1: PriorityMode.PRIORITY_ONLY,
        2: PriorityMode.ENDING_SOONEST,
        3: PriorityMode.LOW_AVBL_FIRST,
    }[_ask_int("Mode", 1, 1, 3)]

    # 6. Connection quality
    quality = _ask_int("Connection quality, 1 (good) - 6 (bad network)", 1, 1, 6)

    data = dict(default_settings)
    data.update(
        language=language,
        proxy=URL(proxy_raw) if proxy_raw else URL(),
        priority=priority,
        exclude=set(exclude),
        priority_mode=mode,
        connection_quality=quality,
    )
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    json_save(settings_path, data, sort=True)

    print(f"\nWritten: {settings_path}")
    print(f"  language           = {language}")
    print(f"  proxy              = {proxy_raw or '(none)'}")
    print(f"  priority           = {priority or '(none)'}")
    print(f"  exclude            = {exclude or '(none)'}")
    print(f"  priority_mode      = {mode.name}")
    print(f"  connection_quality = {quality}")
    print("\nStart mining with:  uv run main.py")
    return 0
