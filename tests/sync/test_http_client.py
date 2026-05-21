"""
http_client 测试 — 本地 http.server mock 真实 HTTP 往返。

不引外部库 — 用 stdlib http.server 起测试 server。

运行:
    python3 -m unittest tests.sync.test_http_client -v
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sync.http_client import ApiError, ApiResponse, get, post, request  # noqa: E402


# ─────────── Mock HTTP server ───────────

class _MockHandler(BaseHTTPRequestHandler):
    """通过 path 决定响应:
        /ok        → 200 + {"ok":true,"data":{"x":1}}
        /raw       → 200 + 任意 JSON
        /401       → 401 + {"ok":false,"error":{"code":"invalid_token","message":"expired"}}
        /429       → 429 + Retry-After:30 + JSON 错误
        /500       → 500 + JSON 错误
        /badjson   → 200 + plain text (非 JSON)
        /echo      → 200 + 回显 method+body+headers
        /slow      → 不响应,触发 client timeout
    """
    timeout_marker = False

    def log_message(self, fmt, *args):
        pass  # 静默测试日志

    def _send_json(self, status: int, payload: dict, extra_headers: dict | None = None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        body = b""
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 0:
            body = self.rfile.read(length)

        if self.path == "/ok":
            return self._send_json(200, {"ok": True, "data": {"x": 1}})
        if self.path == "/raw":
            return self._send_json(200, {"foo": "bar"})
        if self.path == "/401":
            return self._send_json(
                401, {"ok": False, "error": {"code": "invalid_token", "message": "expired"}},
            )
        if self.path == "/429":
            return self._send_json(
                429,
                {"ok": False, "error": {"code": "rate_limit", "message": "slow down", "retry_after": 12}},
                extra_headers={"Retry-After": "30"},  # header 优先
            )
        if self.path == "/500":
            return self._send_json(500, {"ok": False, "error": {"code": "server_error", "message": "boom"}})
        if self.path == "/badjson":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"not json at all")
            return
        if self.path == "/echo":
            payload = {
                "method": self.command,
                "auth": self.headers.get("Authorization"),
                "ctype": self.headers.get("Content-Type"),
                "ua": self.headers.get("User-Agent"),
                "body": body.decode("utf-8", errors="replace"),
            }
            return self._send_json(200, payload)
        if self.path == "/slow":
            time.sleep(2)
            return self._send_json(200, {"ok": True, "data": "should not arrive in fast timeout"})

        return self._send_json(404, {"ok": False, "error": {"code": "not_found", "message": "?"}})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_DELETE(self):
        self._handle()


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerFixture:
    """启 mock server 后台线程,测试结束清理。"""

    def __init__(self):
        self.port = _find_free_port()
        self.server = HTTPServer(("127.0.0.1", self.port), _MockHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        # join 后台线程,避免泄漏
        self.thread.join(timeout=2)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# ─────────── Tests ───────────

class TestSuccessfulRequests(unittest.TestCase):

    def setUp(self):
        self.fix = _ServerFixture().__enter__()

    def tearDown(self):
        self.fix.__exit__(None, None, None)

    def test_get_ok_payload_unwraps(self):
        r = get(f"{self.fix.base}/ok")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.payload, {"x": 1})

    def test_get_raw_payload(self):
        """不带 ok/data 包装时,payload 即原 body"""
        r = get(f"{self.fix.base}/raw")
        self.assertEqual(r.payload, {"foo": "bar"})

    def test_post_with_json_body(self):
        r = post(f"{self.fix.base}/echo", body={"hello": "世界"})
        self.assertEqual(r.payload["method"], "POST")
        self.assertEqual(r.payload["ctype"], "application/json")
        self.assertIn("hello", r.payload["body"])
        self.assertIn("世界", r.payload["body"])

    def test_post_includes_bearer(self):
        r = post(f"{self.fix.base}/echo", body={}, bearer_token="ABCD")
        self.assertEqual(r.payload["auth"], "Bearer ABCD")

    def test_user_agent_set(self):
        r = get(f"{self.fix.base}/echo")
        self.assertIn("ektro-pen/0.4", r.payload["ua"])


class TestErrorResponses(unittest.TestCase):

    def setUp(self):
        self.fix = _ServerFixture().__enter__()

    def tearDown(self):
        self.fix.__exit__(None, None, None)

    def test_401_invalid_token(self):
        with self.assertRaises(ApiError) as ctx:
            get(f"{self.fix.base}/401", bearer_token="bad")
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(ctx.exception.code, "invalid_token")
        self.assertEqual(ctx.exception.message, "expired")

    def test_429_retry_after_header_priority(self):
        """Retry-After response header 应覆盖 body 里的 retry_after"""
        with self.assertRaises(ApiError) as ctx:
            get(f"{self.fix.base}/429")
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(ctx.exception.code, "rate_limit")
        self.assertEqual(ctx.exception.retry_after, 30)  # header 30 优先于 body 12

    def test_500_server_error(self):
        with self.assertRaises(ApiError) as ctx:
            get(f"{self.fix.base}/500")
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(ctx.exception.code, "server_error")

    def test_404_not_found(self):
        with self.assertRaises(ApiError) as ctx:
            get(f"{self.fix.base}/no-such-path")
        self.assertEqual(ctx.exception.status, 404)


class TestBoundaryConditions(unittest.TestCase):

    def setUp(self):
        self.fix = _ServerFixture().__enter__()

    def tearDown(self):
        self.fix.__exit__(None, None, None)

    def test_badjson_raises_parse(self):
        with self.assertRaises(ApiError) as ctx:
            get(f"{self.fix.base}/badjson")
        self.assertEqual(ctx.exception.code, "parse")

    def test_timeout(self):
        with self.assertRaises(ApiError) as ctx:
            get(f"{self.fix.base}/slow", timeout=0.5)
        # urllib raise URLError(reason=socket.timeout) → status 0 + code 'network' or 'timeout'
        self.assertEqual(ctx.exception.status, 0)
        self.assertIn(ctx.exception.code, ("network", "timeout"))

    def test_network_unreachable(self):
        """连不到的端口 → status=0 code='network'"""
        with self.assertRaises(ApiError) as ctx:
            get("http://127.0.0.1:1/never", timeout=2)
        self.assertEqual(ctx.exception.status, 0)
        self.assertEqual(ctx.exception.code, "network")


class TestApiErrorRepr(unittest.TestCase):
    def test_str_with_retry_after(self):
        e = ApiError(status=429, code="rate_limit", message="slow", retry_after=15)
        self.assertIn("429", str(e))
        self.assertIn("rate_limit", str(e))
        self.assertIn("retry_after=15s", str(e))

    def test_str_without_retry_after(self):
        e = ApiError(status=500, code="server_error", message="boom")
        self.assertIn("500", str(e))
        self.assertNotIn("retry_after", str(e))


class TestApiResponsePayload(unittest.TestCase):
    def test_unwraps_ok_data(self):
        r = ApiResponse(status=200, data={"ok": True, "data": {"x": 1}}, headers={})
        self.assertEqual(r.payload, {"x": 1})

    def test_returns_raw_when_no_wrapper(self):
        r = ApiResponse(status=200, data={"foo": "bar"}, headers={})
        self.assertEqual(r.payload, {"foo": "bar"})

    def test_returns_raw_when_ok_false(self):
        """{ok:false,...} 不算 ok 包装,返回原 body"""
        body = {"ok": False, "error": {"code": "x"}}
        r = ApiResponse(status=200, data=body, headers={})
        self.assertEqual(r.payload, body)


if __name__ == "__main__":
    unittest.main()
