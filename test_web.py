"""Headless HTTP + WebSocket end-to-end tests for the web frontend."""
import sys
import json
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tdm_cli.bootstrap as bootstrap

bootstrap.setup()

from collections import OrderedDict

from yarl import URL
from constants import State, PriorityMode

import tdm_cli.gui as cli_gui


class FakeGame:
    def __init__(self, name):
        self.name = name


class FakeChannel:
    def __init__(self, cid, name, game, online=True, viewers=123):
        self.id = cid
        self.name = name
        self.game = FakeGame(game)
        self.online = online
        self.pending_online = False
        self.viewers = viewers
        self.drops_enabled = True


class FakeCampaign:
    def __init__(self, game, name, claimed, total, progress, active=True):
        self.game = FakeGame(game)
        self.name = name
        self.claimed_drops = claimed
        self.total_drops = total
        self.progress = progress
        self.active = active
        self.upcoming = not active
        self.expired = False


class FakeSettings:
    def __init__(self):
        self.priority = ["Rust"]
        self.exclude = {"Fortnite"}
        self.proxy = URL()
        self.language = "English"
        self.priority_mode = PriorityMode.PRIORITY_ONLY
        self.connection_quality = 1
        self.saved = 0

    def alter(self):
        pass

    def save(self, force=False):
        self.saved += 1


class FakeTwitch:
    def __init__(self):
        self.settings = FakeSettings()
        self.channels = OrderedDict()
        self.inventory = []
        self.states = []
        self.closed = False

    def change_state(self, state):
        self.states.append(state)

    def close(self):
        self.closed = True


async def main() -> None:
    import aiohttp

    from tdm_cli.web.server import WebServer, snapshot

    tw = FakeTwitch()
    m = cli_gui.GUIManager(tw)
    tw.channels[1] = FakeChannel(1, "shroud", "Rust")
    tw.channels[2] = FakeChannel(2, "summit1g", "Rust", online=False)
    tw.inventory = [
        FakeCampaign("Rust", "Rust Drops", 3, 10, 0.3),
        FakeCampaign("VALORANT", "V Drops", 0, 5, 0.0, active=False),
    ]
    m.status.update("Watching shroud")
    m.state.watching_channel = "shroud"
    m.state.watching_game = "Rust"
    m.print("hello world log line")

    # snapshot serialisation must be JSON-safe + correct
    snap = snapshot(m)
    json.dumps(snap)
    assert snap["status"] == "Watching shroud"
    assert len(snap["channels"]) == 2 and snap["channels"][0]["name"] == "shroud"
    assert snap["channels"][0]["watching"] is True
    # `locked` mirrors the pinned selection (clearer term for the web UI)
    assert "locked" in snap["channels"][0]
    assert len(snap["campaigns"]) == 2
    # campaigns sorted by progress, highest first (Rust 0.3 before V Drops 0.0)
    progresses = [c["progress"] for c in snap["campaigns"]]
    assert progresses == sorted(progresses, reverse=True), progresses
    assert snap["campaigns"][0]["name"] == "Rust Drops"
    assert snap["settings"]["priority"] == ["Rust"]
    print("PASS snapshot: JSON-serialisable, correct fields, campaigns progress-sorted")

    srv = WebServer(m, "127.0.0.1", 8199)
    await srv.start()
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get("http://127.0.0.1:8199/") as r:
                assert r.status == 200
                html = await r.text()
                assert "TDMConsole" in html and "app.js" in html
                assert 'id="modal-root"' in html
                # SVG icon sprite + <use> references replace the emoji buttons
                for sym in ("i-reload", "i-games", "i-login", "i-online", "i-settings"):
                    assert f'id="{sym}"' in html, f"missing icon symbol {sym}"
                assert 'href="#i-reload"' in html and 'href="#i-settings"' in html
                assert "🎮" not in html and "⚙" not in html and "🔑" not in html
                # channel switch button uses the explicit switch-arrow icon
                assert 'id="i-switch"' in html
            async with sess.get("http://127.0.0.1:8199/static/app.css") as r:
                assert r.status == 200
                # no-store so a stale stylesheet can't leave the overlay up
                assert r.headers.get("Cache-Control") == "no-store"
                css = await r.text()
                # Liquid Glass material: frosted backdrop + translucent fill
                assert "backdrop-filter" in css and "--glass-fill" in css
                # accent colour is themeable via --accent-r/g/b channels
                assert "--accent-r" in css and ".swatch" in css
                # overlay is hidden by default and only shown via .open
                assert ".modal-root.open" in css
                idx_base = css.index(".modal-root {")
                idx_open = css.index(".modal-root.open")
                assert "display: none" in css[idx_base:idx_open], "overlay not default-hidden"
            async with sess.get("http://127.0.0.1:8199/static/app.js?v=3") as r:
                js = await r.text()
                assert r.status == 200 and "WebSocket" in js
                # Games modal must live-refresh so an added game shows up.
                assert "refreshGamesModal" in js
                # Login control swaps to the ✓ Online badge once authorized.
                assert "i-online" in js and "btn-online" in js
                # theme picker: presets + persisted accent applied to CSS vars
                assert "PRESET_THEMES" in js and "applyAccent" in js
                # Settings Save must only send a command when the field changed,
                # so opening Settings never clears the proxy / restarts the miner.
                assert "!== s.priorityMode" in js and "!== origProxy" in js
                # Channel rows use an explicit switch button (arrow icon), not a
                # whole-row click, so switching channels is unambiguous.
                assert "i-switch" in js and "c.locked" in js
                assert "pin-btn" not in js  # old ambiguous pin button is gone
            print("PASS HTTP: assets + no-store + overlay/icons/games/theme guards")

            async with sess.ws_connect("http://127.0.0.1:8199/ws") as ws:
                got_state = got_log = False
                for _ in range(4):
                    msg = await asyncio.wait_for(ws.receive(), timeout=3)
                    data = json.loads(msg.data)
                    if data["type"] == "state":
                        got_state = True
                        assert data["data"]["status"] == "Watching shroud"
                    elif data["type"] == "log":
                        got_log = got_log or any("hello world" in l["text"] for l in data["lines"])
                    if got_state and got_log:
                        break
                assert got_state and got_log, (got_state, got_log)
                print("PASS WS: initial snapshot + log backlog pushed")

                await ws.send_json({"type": "command", "text": "/pin summit1g"})
                await asyncio.sleep(0.3)
                assert m.channels.get_selection() is tw.channels[2]
                assert State.CHANNEL_SWITCH in tw.states
                print("PASS WS: /pin command routed through CommandProcessor")

                m.state.login_available = True
                m.state.login_prompt = True
                await ws.send_json({"type": "action", "name": "login-hide"})
                await asyncio.sleep(0.3)
                assert m.state.login_prompt is False and m.state.login_available is True
                print("PASS WS: login-hide action clears prompt, keeps availability")

                m.status.update("Idle now")
                pushed = False
                for _ in range(6):
                    msg = await asyncio.wait_for(ws.receive(), timeout=2)
                    d = json.loads(msg.data)
                    if d["type"] == "state" and d["data"]["status"] == "Idle now":
                        pushed = True
                        break
                assert pushed
                print("PASS WS: broadcast loop pushes state changes")
    finally:
        srv.request_stop()
        await srv.cleanup()

    # lifecycle: WebFrontend start/stop via the manager
    from tdm_cli import web as webmod

    webmod.PORT = 8198
    webmod.HOST = "127.0.0.1"
    m2 = cli_gui.GUIManager(FakeTwitch())
    m2.mode = "web"
    m2.frontend = webmod.WebFrontend(m2)
    m2.frontend.start()
    await asyncio.sleep(0.6)
    assert not m2.frontend.is_stopped()
    async with aiohttp.ClientSession() as sess:
        async with sess.get("http://127.0.0.1:8198/") as r:
            assert r.status == 200
    await m2.frontend.wait_stopped()
    assert m2.frontend.is_stopped()
    print("PASS WebFrontend: start serves, wait_stopped tears down")

    # Regression: main.py must guard cookies.jar across shutdown, so an
    # offline restart / jar-load failure can't silently log the user out.
    import pathlib
    main_src = pathlib.Path(__file__).with_name("main.py").read_text(encoding="utf8")
    assert "_read_valid_cookie_bytes" in main_src and "_restore_cookies_if_emptied" in main_src, \
        "cookie-loss guard missing from main.py shutdown path"
    print("PASS regression: cookie-loss guard present in shutdown path")

    print("ALL WEB TESTS PASSED")


asyncio.run(main())
