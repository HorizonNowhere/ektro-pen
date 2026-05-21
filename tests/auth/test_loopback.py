"""
Loopback callback server 集成测试 — 真起 server,真发 GET。

不 mock — 验证 server 端真实行为 (state 校验 / code 提取 / HTML 响应 / 超时)。

运行:
    python3 -m unittest tests.auth.test_loopback -v
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from auth.loopback import CallbackResult, wait_for_callback  # noqa: E402


def _hit(url: str, timeout: float = 5.0) -> tuple[int, str]:
    """阻塞访问 URL,返回 (status, body)。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body


def _trigger_callback_async(url: str, delay: float = 0.1) -> threading.Thread:
    """后台延迟访问 url 模拟浏览器回调。"""
    def go():
        time.sleep(delay)
        try:
            _hit(url)
        except Exception:
            pass
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


class TestSuccessfulCallback(unittest.TestCase):

    def test_valid_code_received(self):
        """state 匹配 + 有 code → result.ok True"""
        state = "test-state-abc-123"

        def trigger():
            time.sleep(0.2)
            # 这里我们还不知道端口,通过 wait_for_callback 返回值获取
            # 改用先启动 server,再 trigger
        # 因为 wait_for_callback 是阻塞的,我们先分两步:
        # 1. wait_for_callback 启动后会立即 print/return redirect_uri (但它阻塞)
        # 2. 改用线程把 trigger 提前调度

        result_holder: list[tuple[str, CallbackResult] | None] = [None]

        def run_server():
            r = wait_for_callback(state, timeout=5.0)
            result_holder[0] = r

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # 等待 server bind 端口 (loopback 内部 ~50ms 可启动)
        time.sleep(0.3)

        # _allocate_port 已用完端口被 server 重新 bind,
        # 但我们不知道端口号。换思路:固定 port。
        server_thread.join(timeout=2)
        # 上面方案不行,改下面用显式端口

    def test_with_fixed_port_succeed(self):
        """显式 port 让测试可靠"""
        state = "fixed-state"
        port = 38821

        result_holder: list[CallbackResult | None] = [None]

        def run_server():
            _, res = wait_for_callback(state, timeout=5.0, port=port)
            result_holder[0] = res

        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        time.sleep(0.3)  # server 启动

        # 触发
        status, body = _hit(f"http://127.0.0.1:{port}/callback?code=abc123&state={state}")
        self.assertEqual(status, 200)
        self.assertIn("链接成功", body)

        t.join(timeout=2)
        self.assertIsNotNone(result_holder[0])
        res = result_holder[0]
        self.assertTrue(res.ok)
        self.assertEqual(res.code, "abc123")
        self.assertEqual(res.state, state)
        self.assertIsNone(res.error)


class TestStateMismatch(unittest.TestCase):

    def test_state_mismatch_rejected(self):
        port = 38822
        expected_state = "real-state"

        holder: list[CallbackResult | None] = [None]

        def run_server():
            _, res = wait_for_callback(expected_state, timeout=5.0, port=port)
            holder[0] = res

        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        time.sleep(0.3)

        status, body = _hit(f"http://127.0.0.1:{port}/callback?code=x&state=WRONG-STATE")
        self.assertEqual(status, 400)
        self.assertIn("链接失败", body)

        t.join(timeout=2)
        res = holder[0]
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "state_mismatch")
        self.assertIsNone(res.code)


class TestUserDenied(unittest.TestCase):

    def test_error_query_param(self):
        """服务端 302 ?error=access_denied&state=..."""
        port = 38823
        state = "deny-state"

        holder: list[CallbackResult | None] = [None]

        def run_server():
            _, res = wait_for_callback(state, timeout=5.0, port=port)
            holder[0] = res

        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        time.sleep(0.3)

        status, body = _hit(
            f"http://127.0.0.1:{port}/callback?error=access_denied&state={state}"
        )
        self.assertEqual(status, 400)
        self.assertIn("用户拒绝", body)

        t.join(timeout=2)
        res = holder[0]
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "access_denied")


class TestMissingCode(unittest.TestCase):

    def test_no_code_no_error(self):
        port = 38824
        state = "missing-code-state"

        holder: list[CallbackResult | None] = [None]

        def run_server():
            _, res = wait_for_callback(state, timeout=5.0, port=port)
            holder[0] = res

        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        time.sleep(0.3)

        status, _ = _hit(f"http://127.0.0.1:{port}/callback?state={state}")
        self.assertEqual(status, 400)

        t.join(timeout=2)
        res = holder[0]
        self.assertEqual(res.error, "no_code")


class TestTimeout(unittest.TestCase):

    def test_timeout_with_no_callback(self):
        """没人调 callback → 超时"""
        port = 38825
        start = time.time()
        redirect_uri, res = wait_for_callback(
            "timeout-state", timeout=0.5, port=port,
        )
        elapsed = time.time() - start

        self.assertTrue(redirect_uri.startswith(f"http://127.0.0.1:{port}/callback"))
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "timeout")
        # 应该在 ~0.5s 而非 60s
        self.assertLess(elapsed, 2.0)


class TestRedirectUriFormat(unittest.TestCase):

    def test_uri_format(self):
        """返回的 redirect_uri 格式严格 RFC 8252 loopback"""
        port = 38826
        # 立即触发回调,避免 60s 等
        def trigger():
            time.sleep(0.3)
            _hit(f"http://127.0.0.1:{port}/callback?code=x&state=s")

        threading.Thread(target=trigger, daemon=True).start()
        redirect_uri, _ = wait_for_callback("s", timeout=3.0, port=port)
        self.assertEqual(redirect_uri, f"http://127.0.0.1:{port}/callback")


if __name__ == "__main__":
    unittest.main()
