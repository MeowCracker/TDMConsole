from __future__ import annotations

# Keep PyInstaller/multiprocessing happy if this is ever frozen.
from multiprocessing import freeze_support


if __name__ == "__main__":
    freeze_support()

    import sys
    import signal
    import asyncio
    import logging
    import argparse
    import warnings
    import traceback
    from pathlib import Path

    # Parse args first: -c/--config and --jar must be known before upstream
    # modules are imported (they bind SETTINGS_PATH/COOKIES_PATH at import time).
    #
    # NOTE: Settings.__getattr__ prefers attributes found on this namespace over
    # the settings file, so anything that is NOT meant to shadow a settings key
    # must use an underscored dest (e.g. `_proxy`, applied manually below).
    class ParsedArgs(argparse.Namespace):
        _verbose: int
        _debug_ws: bool
        _debug_gql: bool
        log: bool
        tray: bool
        dump: bool
        command: str | None
        _config: str | None
        _proxy: str | None
        _games: str | None
        _cookie: str | None
        _jar: str | None
        _mode: str | None
        _host: str | None
        _port: int | None
        check_contract: bool

        @property
        def logging_level(self) -> int:
            from constants import LOGGING_LEVELS

            return LOGGING_LEVELS[min(self._verbose, 4)]

        @property
        def debug_ws(self) -> int:
            if self._debug_ws:
                return logging.DEBUG
            elif self._verbose >= 4:
                return logging.INFO
            return logging.NOTSET

        @property
        def debug_gql(self) -> int:
            if self._debug_gql:
                return logging.DEBUG
            elif self._verbose >= 4:
                return logging.INFO
            return logging.NOTSET

    parser = argparse.ArgumentParser(
        "main.py",
        description="Mine timed drops on Twitch — interactive TUI / headless CLI.",
    )
    parser.add_argument(
        "command", nargs="?", choices=("init",), default=None,
        help="init: interactive wizard that generates the settings file",
    )
    parser.add_argument(
        "-c", "--config", dest="_config", metavar="PATH", default=None,
        help="path of the settings file (default: ./settings.json)",
    )
    parser.add_argument(
        "--proxy", dest="_proxy", metavar="URL", default=None,
        help="proxy URL, e.g. http://127.0.0.1:7890 (saved into the settings file)",
    )
    parser.add_argument(
        "--games", dest="_games", metavar="LIST", default=None,
        help='comma-separated priority games, e.g. "Rust,VALORANT" '
             "(overrides the settings file's priority list)",
    )
    parser.add_argument(
        "--cookie", dest="_cookie", metavar="TOKEN", default=None,
        help="Twitch auth token; writes it into the cookie jar before starting "
             "(skips the device-code login)",
    )
    parser.add_argument(
        "--jar", dest="_jar", metavar="PATH", default=None,
        help="path of the cookie jar file (default: ./cookies.jar)",
    )
    parser.add_argument(
        "--mode", dest="_mode",
        choices=("tui", "repl", "web", "gui", "headless"), default=None,
        help="interface mode: tui (full-screen dashboard), repl (slash-command "
             "prompt), web (browser UI for Docker), gui (upstream tkinter "
             "window), headless (plain logs); default: saved preference, else web",
    )
    parser.add_argument(
        "--host", dest="_host", metavar="ADDR", default=None,
        help="web mode bind address (default: $TDM_WEB_HOST or 127.0.0.1; "
             "use 0.0.0.0 in Docker)",
    )
    parser.add_argument(
        "--port", dest="_port", metavar="PORT", type=int, default=None,
        help="web mode port (default: $TDM_WEB_PORT or 8080)",
    )
    parser.add_argument(
        "-v", dest="_verbose", action="count", default=0,
        help="increase verbosity (repeatable, up to -vvvv)",
    )
    parser.add_argument("--log", action="store_true", help="write a log file (log.txt)")
    parser.add_argument(
        "--dump", action="store_true", help="dump some payloads for debugging"
    )
    parser.add_argument(
        "--check-contract", action="store_true",
        help="verify the CLI gui shim matches the upstream submodule, then exit",
    )
    # Undocumented debug args (kept name-compatible with upstream).
    parser.add_argument(
        "--debug-ws", dest="_debug_ws", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--debug-gql", dest="_debug_gql", action="store_true", help=argparse.SUPPRESS
    )
    # --version needs the submodule on sys.path; handled after bootstrap below.
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(namespace=ParsedArgs())
    # 'tray' is a GUI-only concept, but Settings reads it off the args object.
    args.tray = False

    # Two-stage bootstrap. Stage 1 (setup_paths) puts the submodule on sys.path
    # and repoints constants — enough to import `constants`/`version` and read
    # the saved mode preference. Stage 2 (setup) optionally installs the GUI
    # shim: every CLI frontend swaps upstream's tkinter `gui` for our terminal
    # implementation, but `--mode gui` leaves it alone so the native window runs.
    import tdm_cli.bootstrap as bootstrap

    bootstrap.setup_paths(settings_path=args._config, cookies_path=args._jar)

    if args.version:
        from tdm_cli.versioning import version_line

        print(version_line())
        sys.exit(0)

    if args.check_contract:
        bootstrap.setup(settings_path=args._config, cookies_path=args._jar)
        issues = bootstrap.verify_contract()
        if issues:
            print("Interface drift vs. the upstream submodule:", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)
            sys.exit(1)
        print("OK: CLI gui shim matches the upstream submodule interface.")
        sys.exit(0)

    import constants

    if args.command == "init":
        from tdm_cli.wizard import run_wizard

        sys.exit(run_wizard(constants.SETTINGS_PATH))

    import truststore

    truststore.inject_into_ssl()

    from yarl import URL

    # Resolve the interface mode BEFORE importing upstream `twitch` (which runs
    # `from gui import GUIManager` at import time). Stage 2 of the bootstrap then
    # installs the CLI GUI shim for every mode EXCEPT `gui`, where upstream's
    # native tkinter window is used as-is.
    #
    # The mode is NOT persisted: startup honours only `--mode`, else the default
    # (web). This keeps `docker run` / a bare launch on web every time, instead
    # of silently sticking to whatever a `--mode`/`/switch-mode`/wizard run last
    # left behind. `/switch-mode` still swaps the live frontend, just for the
    # current process.
    from tdm_cli import console, prefs

    if args._mode is not None:
        mode = args._mode
    else:
        mode = prefs.DEFAULT_MODE
    if mode in ("tui", "repl") and not console.INTERACTIVE:
        mode = "headless"
    bootstrap.setup(install_gui_shim=(mode != "gui"))

    from translate import _
    from twitch import Twitch
    from settings import Settings
    from exceptions import CaptchaRequired
    from utils import lock_file
    from constants import FILE_FORMATTER, LOG_PATH, LOCK_PATH

    warnings.simplefilter("default", ResourceWarning)

    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or higher is required")

    # Load settings (from the settings file + the args above).
    try:
        settings = Settings(args)
    except Exception:
        print("There was an error while loading the settings file:\n", file=sys.stderr)
        traceback.print_exc()
        sys.exit(4)

    # Apply one-shot CLI overrides (persisted into the settings file on save).
    if args._proxy is not None:
        settings.proxy = URL(args._proxy)
    if args._games is not None:
        settings.priority = [g.strip() for g in args._games.split(",") if g.strip()]

    def write_auth_cookie(token: str, jar_path: Path) -> None:
        """Seed the cookie jar with an auth token, skipping device-code login.

        Must run inside the event loop — aiohttp's CookieJar binds to the running
        loop on construction.
        """
        from http.cookies import SimpleCookie

        import aiohttp
        from constants import ClientType

        client_url = ClientType.ANDROID_APP.CLIENT_URL
        assert client_url.host is not None
        cookie: SimpleCookie = SimpleCookie()
        cookie["auth-token"] = token
        cookie["auth-token"]["domain"] = "." + ".".join(client_url.host.split(".")[-2:])
        cookie["auth-token"]["path"] = "/"
        jar = aiohttp.CookieJar()
        jar.update_cookies(cookie, client_url)
        jar.save(jar_path)

    def _read_valid_cookie_bytes(jar_path: Path) -> bytes | None:
        """Return the raw cookies.jar bytes if it holds real cookies, else None.

        A freshly-cleared aiohttp jar serialises to ``{}`` (2 bytes). We treat
        that — and any unreadable/empty file — as "no valid cookies".
        """
        try:
            raw = jar_path.read_bytes()
        except OSError:
            return None
        if len(raw.strip()) <= 2:  # b"{}" or empty
            return None
        return raw

    def _restore_cookies_if_emptied(jar_path: Path, backup: bytes | None) -> None:
        """Restore a good cookies.jar if shutdown clobbered it with an empty one.

        Upstream's ``get_session`` clears the jar when a load fails, and
        ``shutdown`` then persists that empty jar — silently logging the user
        out. If we captured valid cookies before shutdown and the file is now
        empty, put the good copy back.
        """
        if backup is None:
            return
        if _read_valid_cookie_bytes(jar_path) is None:
            try:
                jar_path.write_bytes(backup)
            except OSError:
                pass

    # `mode` was resolved earlier (before the upstream `twitch` import, so the
    # GUI shim decision could be made). Here we just wire mode-specific config.
    if mode == "web":
        import os as _os

        import tdm_cli.web as web_frontend

        web_frontend.HOST = args._host or _os.environ.get("TDM_WEB_HOST", "127.0.0.1")
        web_frontend.PORT = int(args._port or _os.environ.get("TDM_WEB_PORT", "8080"))

    import tdm_cli.gui as cli_gui

    cli_gui.ACTIVE_MODE = mode

    async def main() -> None:
        # Language
        try:
            _.set_language(settings.language)
        except ValueError:
            pass  # unknown language -> stick to English

        # Logging
        if settings.logging_level > logging.DEBUG:
            logging.getLogger().addHandler(logging.NullHandler())
        logger = logging.getLogger("TwitchDrops")
        logger.setLevel(settings.logging_level)
        if settings.log:
            handler = logging.FileHandler(LOG_PATH)
            handler.setFormatter(FILE_FORMATTER)
            logger.addHandler(handler)
        logging.getLogger("TwitchDrops.gql").setLevel(settings.debug_gql)
        logging.getLogger("TwitchDrops.websocket").setLevel(settings.debug_ws)

        # Seed an auth cookie before the client starts (needs the event loop).
        if args._cookie is not None:
            write_auth_cookie(args._cookie, constants.COOKIES_PATH)
            print(f"Auth cookie written to {constants.COOKIES_PATH}")

        exit_status = 0
        client = Twitch(settings)
        loop = asyncio.get_running_loop()
        # Graceful Ctrl+C / SIGTERM on POSIX (macOS + Linux).
        if sys.platform != "win32":
            loop.add_signal_handler(signal.SIGINT, lambda *_: client.gui.close())
            loop.add_signal_handler(signal.SIGTERM, lambda *_: client.gui.close())
        try:
            await client.run()
        except CaptchaRequired:
            exit_status = 1
            client.prevent_close()
            client.print(_("error", "captcha"))
        except Exception:
            exit_status = 1
            client.prevent_close()
            client.print("Fatal error encountered:\n")
            client.print(traceback.format_exc())
        finally:
            if sys.platform != "win32":
                loop.remove_signal_handler(signal.SIGINT)
                loop.remove_signal_handler(signal.SIGTERM)
            client.print(_("gui", "status", "exiting"))
            # Guard against losing a valid login: upstream's shutdown() writes the
            # session's cookie jar back to disk unconditionally. If the session
            # never loaded cookies (e.g. an offline start, or a jar-load failure
            # cleared them), that write clobbers a good cookies.jar with "{}".
            # Snapshot a valid on-disk jar first, restore it if shutdown emptied it.
            cookie_backup = _read_valid_cookie_bytes(constants.COOKIES_PATH)
            await client.shutdown()
            _restore_cookies_if_emptied(constants.COOKIES_PATH, cookie_backup)
        if mode == "gui":
            # Native tkinter GUI (upstream's own GUIManager). Mirror upstream's
            # teardown: keep the window open until the user closes it, so any
            # final state / error stays visible. `client.gui` here is upstream's
            # manager, which has wait_until_closed()/tray/close_window().
            if not client.gui.close_requested:
                client.gui.tray.change_icon("error")
                client.print(_("status", "terminated"))
                client.gui.status.update(_("gui", "status", "terminated"))
                client.gui.grab_attention(sound=True)
            await client.gui.wait_until_closed()
            client.save(force=True)
            client.gui.stop()
            client.gui.close_window()
            sys.exit(exit_status)
        if not client.gui.close_requested:
            # Terminated by an error rather than a user request.
            client.print(_("status", "terminated"))
            client.gui.status.update(_("gui", "status", "terminated"))
            client.gui.grab_attention(sound=True)
        # No window to keep open — the terminal is the UI, so just wind down.
        client.save(force=True)
        client.gui.stop()
        # Fully tear down the TUI (restores the terminal) before exiting.
        await client.gui.frontend.wait_stopped()
        client.gui.close_window()
        if client.gui.mode in ("tui", "repl") and exit_status != 0:
            # The Textual app has torn down; repeat the tail of the log so the
            # error stays visible in the plain terminal.
            for entry in list(client.gui.state.log_lines)[-15:]:
                print(f"{entry.stamp}: {entry.text}")
        sys.exit(exit_status)

    file = None
    try:
        # Single-instance lock.
        success, file = lock_file(LOCK_PATH)
        if not success:
            print("Another instance is already running.", file=sys.stderr)
            sys.exit(3)
        asyncio.run(main())
    finally:
        if file is not None:
            file.close()
