from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from tdm_cli.web.server import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    _AUTH_EXPIRES_AT,
    _MemorySessions,
    WebServer,
    _runtime_payload,
    _login_response,
    _session_auth_middleware,
)


class MemorySessionTests(unittest.TestCase):
    def test_creates_and_validates_session_for_correct_credentials(self) -> None:
        sessions = _MemorySessions("admin", "secret")

        token = sessions.create("admin", "secret")

        self.assertIsNotNone(token)
        self.assertIsNotNone(sessions.validate(token))

    def test_rejects_incorrect_credentials(self) -> None:
        sessions = _MemorySessions("admin", "secret")

        self.assertIsNone(sessions.create("admin", "wrong"))
        self.assertIsNone(sessions.create("wrong", "secret"))

    def test_session_expires_after_three_days(self) -> None:
        sessions = _MemorySessions("admin", "secret")
        with patch("tdm_cli.web.server.time.monotonic", return_value=100.0):
            token = sessions.create("admin", "secret")
            self.assertEqual(sessions.validate(token), 100.0 + SESSION_TTL_SECONDS)
        with patch(
            "tdm_cli.web.server.time.monotonic",
            return_value=100.0 + SESSION_TTL_SECONDS,
        ):
            self.assertIsNone(sessions.validate(token))

    def test_new_process_store_rejects_old_token(self) -> None:
        first_process = _MemorySessions("admin", "secret")
        token = first_process.create("admin", "secret")

        restarted_process = _MemorySessions("admin", "secret")

        self.assertIsNone(restarted_process.validate(token))

    def test_rejects_invalid_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            _MemorySessions("admin", "")
        with self.assertRaisesRegex(ValueError, "cannot contain"):
            _MemorySessions("bad:name", "secret")

    def test_login_cookie_is_http_only_strict_and_three_days(self) -> None:
        response = _login_response("random-token", secure=True)

        cookie = response.cookies[SESSION_COOKIE]
        self.assertEqual(cookie["max-age"], str(SESSION_TTL_SECONDS))
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(cookie["path"], "/")


class SessionMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirects_unauthenticated_index_to_login(self) -> None:
        middleware = _session_auth_middleware(_MemorySessions("admin", "secret"))
        handler = AsyncMock(return_value=web.Response(text="ok"))

        response = await middleware(make_mocked_request("GET", "/"), handler)

        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/login?next=/")
        handler.assert_not_awaited()

    async def test_allows_public_login_page(self) -> None:
        middleware = _session_auth_middleware(_MemorySessions("admin", "secret"))
        request = make_mocked_request("GET", "/login")
        expected = web.Response(text="login")
        handler = AsyncMock(return_value=expected)

        response = await middleware(request, handler)

        self.assertIs(response, expected)
        handler.assert_awaited_once_with(request)

    async def test_allows_public_healthcheck(self) -> None:
        middleware = _session_auth_middleware(_MemorySessions("admin", "secret"))
        request = make_mocked_request("GET", "/healthcheck")
        expected = web.Response(text="ok")
        handler = AsyncMock(return_value=expected)

        response = await middleware(request, handler)

        self.assertIs(response, expected)
        handler.assert_awaited_once_with(request)

    async def test_rejects_unauthenticated_api_and_websocket(self) -> None:
        middleware = _session_auth_middleware(_MemorySessions("admin", "secret"))
        handler = AsyncMock(return_value=web.Response(text="ok"))

        api_response = await middleware(make_mocked_request("GET", "/runtime"), handler)
        ws_response = await middleware(make_mocked_request("GET", "/ws"), handler)

        self.assertEqual(api_response.status, 401)
        self.assertEqual(ws_response.status, 401)
        handler.assert_not_awaited()

    async def test_allows_valid_session_cookie(self) -> None:
        sessions = _MemorySessions("admin", "secret")
        token = sessions.create("admin", "secret")
        middleware = _session_auth_middleware(sessions)
        request = make_mocked_request(
            "GET",
            "/meta",
            headers={"Cookie": f"{SESSION_COOKIE}={token}"},
        )
        expected = web.Response(text="ok")
        handler = AsyncMock(return_value=expected)

        response = await middleware(request, handler)

        self.assertIs(response, expected)
        self.assertIn(_AUTH_EXPIRES_AT, request)
        handler.assert_awaited_once_with(request)


class HealthcheckTests(unittest.IsolatedAsyncioTestCase):
    @patch("tdm_cli.web.server._cache_size_bytes", return_value=768 * 1024 * 1024)
    @patch("tdm_cli.web.server._cgroup_memory_limit", return_value=2 * 1024**3)
    @patch("tdm_cli.web.server._process_rss_bytes", return_value=768 * 1024 * 1024)
    @patch("tdm_cli.web.server._cgroup_vcpu_limit", return_value=0.2)
    @patch("tdm_cli.web.server.time.time", return_value=1_700_003_661.0)
    @patch("tdm_cli.versioning.version_info")
    def test_payload_reports_versions_and_resources(
        self,
        version_info: unittest.mock.Mock,
        *_mocks: unittest.mock.Mock,
    ) -> None:
        version_info.return_value = {
            "app": "1.2.3",
            "engine": "2.4.5",
            "engineCommit": "abc1234",
        }

        payload = _runtime_payload(1_700_000_000.0, 0.125)

        self.assertEqual(payload["uptime"], "1h 1m 1s")
        self.assertEqual(payload["version"], "1.2.3")
        self.assertEqual(payload["engine"], {"version": "2.4.5", "commit": "abc1234"})
        self.assertEqual(payload["cpu"]["usage"], "0.1/0.2 vCPU")
        self.assertEqual(payload["memory"]["usage"], "768.00M/2.00G")
        self.assertEqual(payload["cache"], {"size": "768M", "sizeBytes": 768 * 1024 * 1024})

    async def test_healthcheck_returns_plain_ok(self) -> None:
        server = WebServer.__new__(WebServer)
        response = await server._handle_healthcheck(make_mocked_request("GET", "/healthcheck"))

        self.assertEqual(response.text, "ok")
        self.assertEqual(response.content_type, "text/plain")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_runtime_handler_returns_payload_with_no_store_header(self) -> None:
        server = WebServer.__new__(WebServer)
        server._started_at = 1_700_000_000.0
        with patch("tdm_cli.web.server._process_vcpu_usage", return_value=0.0), patch(
            "tdm_cli.web.server._runtime_payload", return_value={"status": "ok"}
        ):
            response = await server._handle_runtime(make_mocked_request("GET", "/runtime"))

        self.assertEqual(response.text, '{"status": "ok"}')
        self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
