"""
PredictorClient 单元测试（含 mock HTTP server）。

跑法:
    cd E:\\CLAUDE\\EKTRO输入法
    set PYTHONPATH=src
    python -m unittest tests.predictor.test_client -v
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from predictor.client import (  # noqa: E402
    PredictorClient,
    PredictorConfig,
    PredictionResult,
)
from predictor.trigger import AsyncTrigger, TriggerConfig  # noqa: E402


# ──────────── Mock llama-server ────────────


class MockHandler(BaseHTTPRequestHandler):
    """模拟 llama-server，返回可控的 timings 与 content。"""

    # 类级配置（由测试设置）
    response_text: str = "续写文本"
    response_delay_ms: int = 0
    response_status: int = 200
    last_prompt: str = ""

    def log_message(self, *args, **kwargs):
        pass  # 静默

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/completion":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        req = json.loads(body)
        MockHandler.last_prompt = req.get("prompt", "")
        if self.response_delay_ms:
            time.sleep(self.response_delay_ms / 1000.0)
        if self.response_status != 200:
            self.send_response(self.response_status)
            self.end_headers()
            return
        payload = json.dumps({
            "content": self.response_text,
            "timings": {"prompt_n": 12, "prompt_ms": 50.0},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)


def start_mock_server(port: int = 0) -> tuple[HTTPServer, str, threading.Thread]:
    srv = HTTPServer(("127.0.0.1", port), MockHandler)
    actual_port = srv.server_address[1]
    url = f"http://127.0.0.1:{actual_port}"
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, url, th


class PredictorBase(unittest.TestCase):
    def setUp(self):
        # 每个测试用独立 mock server，干净状态
        MockHandler.response_text = "续写文本"
        MockHandler.response_delay_ms = 0
        MockHandler.response_status = 200
        MockHandler.last_prompt = ""
        self.srv, self.url, self.thread = start_mock_server()
        self.cfg = PredictorConfig(server_url=self.url, timeout_ms=500)
        self.client = PredictorClient(self.cfg)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()


class TestPredictorClient(PredictorBase):
    def test_health_ok(self):
        self.assertTrue(self.client.health())

    def test_health_fails_when_server_down(self):
        self.srv.shutdown()
        c = PredictorClient(PredictorConfig(server_url=self.url, timeout_ms=200))
        self.assertFalse(c.health())

    def test_basic_predict(self):
        r = self.client.predict(prefix="我今天", context="早上喝了")
        self.assertEqual(r.text, "续写文本")
        self.assertIsNone(r.error)
        self.assertGreater(r.total_ms, 0)
        self.assertEqual(r.prompt_tokens, 12)
        self.assertEqual(r.prefill_ms, 50.0)

    def test_prompt_composition(self):
        self.client.predict(prefix="今天", context="昨天")
        self.assertIn("今天", MockHandler.last_prompt)
        self.assertIn("昨天", MockHandler.last_prompt)
        # context 应该在 prefix 前
        self.assertLess(
            MockHandler.last_prompt.index("昨天"),
            MockHandler.last_prompt.index("今天"),
        )

    def test_context_truncation(self):
        cfg = PredictorConfig(server_url=self.url, max_context_chars=10, timeout_ms=500)
        c = PredictorClient(cfg)
        long_ctx = "一二三四五六七八九十甲乙丙丁戊己庚辛"
        c.predict(prefix="x", context=long_ctx)
        # 最多 10 字符的 context + "x"
        self.assertLessEqual(len(MockHandler.last_prompt), 11)

    def test_cache_hit(self):
        r1 = self.client.predict(prefix="abc", context="xyz")
        self.assertFalse(r1.cache_hit)
        r2 = self.client.predict(prefix="abc", context="xyz")
        self.assertTrue(r2.cache_hit)
        self.assertEqual(r2.text, r1.text)
        self.assertEqual(self.client.stats.n_cache_hit, 1)

    def test_cache_capacity_eviction(self):
        cfg = PredictorConfig(server_url=self.url, cache_capacity=2, timeout_ms=500)
        c = PredictorClient(cfg)
        c.predict(prefix="a", context="")
        c.predict(prefix="b", context="")
        c.predict(prefix="c", context="")  # 应淘汰 "a"
        self.assertEqual(c.cache_size(), 2)

    def test_timeout_returns_error(self):
        MockHandler.response_delay_ms = 800  # 服务端慢
        cfg = PredictorConfig(server_url=self.url, timeout_ms=100)  # 客户端等不及
        c = PredictorClient(cfg)
        r = c.predict(prefix="x", context="")
        self.assertEqual(r.text, "")
        self.assertIsNotNone(r.error)
        self.assertEqual(c.stats.n_timeout + c.stats.n_error, 1)

    def test_server_error_returns_error(self):
        MockHandler.response_status = 500
        r = self.client.predict(prefix="x", context="")
        self.assertEqual(r.text, "")
        self.assertIsNotNone(r.error)

    def test_strip_stop_tokens(self):
        MockHandler.response_text = "下一句话。"
        r = self.client.predict(prefix="x", context="")
        self.assertEqual(r.text, "下一句话")

    def test_stats_accumulate(self):
        for i in range(3):
            self.client.predict(prefix=f"prefix{i}", context="")
        self.assertEqual(self.client.stats.n_calls, 3)
        self.assertEqual(self.client.stats.n_success, 3)


class TestAsyncTrigger(PredictorBase):
    def setUp(self):
        super().setUp()
        self.results: list[PredictionResult] = []
        self.trigger = AsyncTrigger(
            self.client,
            on_result=lambda r: self.results.append(r),
            config=TriggerConfig(pause_ms=80, poll_interval_ms=20, debounce_min_chars=2),
        )
        self.trigger.start()

    def tearDown(self):
        self.trigger.stop(timeout=2.0)
        super().tearDown()

    def test_predicts_after_pause(self):
        self.trigger.on_keystroke(prefix="我今天", context="早上")
        # 等够 pause + 一次预测 + worker poll
        time.sleep(0.4)
        self.assertGreaterEqual(len(self.results), 1)
        self.assertEqual(self.results[-1].text, "续写文本")

    def test_too_short_prefix_skips(self):
        self.trigger.on_keystroke(prefix="x", context="")  # 1 字符 < debounce_min_chars
        time.sleep(0.3)
        self.assertEqual(len(self.results), 0)

    def test_continued_typing_cancels(self):
        # 连续按键，期间不该有结果（因为每次按键重置停顿计时）
        for ch in "我今天打开了电脑":
            self.trigger.on_keystroke(prefix=ch * 3, context="")
            time.sleep(0.03)
        # 此时还没停 80ms，应该 0 个结果
        self.assertEqual(len(self.results), 0)
        # 停下来等
        time.sleep(0.3)
        # 应该收到一次结果
        self.assertGreaterEqual(len(self.results), 1)

    def test_cancel_clears_pending(self):
        self.trigger.on_keystroke(prefix="abcd", context="")
        self.trigger.cancel()
        time.sleep(0.3)
        self.assertEqual(len(self.results), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
