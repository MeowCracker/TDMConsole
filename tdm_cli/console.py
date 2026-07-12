"""TTY-aware terminal output helpers.

A single :func:`emit` printer mirrors the upstream ``ConsoleOutput`` line
format (``HH:MM:SS: message``) so on-screen output feels familiar. ANSI colour
and the bell are only used when stdout is an interactive terminal, so piping to
a file / journald / ``| cat`` yields clean, plain log lines.
"""
from __future__ import annotations

import sys
from datetime import datetime

# Whether stdout is an interactive terminal. Drives colour + bell + browser-open.
INTERACTIVE: bool = sys.stdout.isatty()


class Color:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def paint(text: str, color: str) -> str:
    """Wrap *text* in an ANSI colour when interactive, otherwise return as-is."""
    if INTERACTIVE and color:
        return f"{color}{text}{Color.RESET}"
    return text


def emit(message: str, *, color: str | None = None) -> None:
    """Print a timestamped line, matching the upstream ``ConsoleOutput`` format."""
    stamp = datetime.now().strftime("%X")
    if "\n" in message:
        message = message.replace("\n", f"\n{stamp}: ")
    line = f"{stamp}: {message}"
    if color:
        line = paint(line, color)
    print(line, flush=True)


def banner(lines: list[str], *, color: str | None = Color.BOLD) -> None:
    """Print a boxed, attention-grabbing block (used for the login prompt)."""
    width = max((len(ln) for ln in lines), default=0)
    rule = "=" * (width + 4)
    block = [rule, *(f"  {ln.ljust(width)}  " for ln in lines), rule]
    text = "\n".join(block)
    print(paint(text, color or "") if color else text, flush=True)


def bell() -> None:
    """Ring the terminal bell when interactive."""
    if INTERACTIVE:
        sys.stdout.write("\a")
        sys.stdout.flush()
