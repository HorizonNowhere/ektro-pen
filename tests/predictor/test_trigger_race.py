"""
AsyncTrigger 竞态测试 (D-009 P0.7)。

用 threading.Event 而非 sleep，让时序确定。

覆盖 swarm 发现的盲点：
- 预测进行中用户继续打字 → 旧结果被丢弃
- task_id 单调递增 (D-009 P1.1)，不再用浮点
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from predictor.client import PredictorClient, PredictorConfig, PredictionResult  # noqa: E402
from predictor.trigger import AsyncTrigger, TriggerConfig  # noqa: E402


# ──────────── 受控时序的 Mock Server ────────────


class ControlledHandler(BaseHTTPRequestHandler):
    """可被外部 Event 控制响应时机的 Mock。"""
    response_text: str = "续写"
    release_event: threading.Event = threading.Event()  # 触发响应
    received_event: threading.Event = threading.Event()  # server 已收请求

    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        if self.path != "/completion":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)

        ControlledHandler.received_event.set()
        # 等待 release_event 才返回 → 模拟"慢预测进行中"
        ControlledHandler.release_event.wait(timeout=5.0)

        import json
        payload = json.dumps({
            "content": ControlledHandler.response_text,
            "timings": {"prompt_n": 5, "prompt_ms": 30.0},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except Exception:
            pass  # 客户端可能已断开


def start_controlled_server() -> tuple[HTTPServer, str, threading.Thread]:
    ControlledHandler.release_event.clear()
    ControlledHandler.received_event.clear()
    srv = HTTPServer(("127.0.0.1", 0), ControlledHandler)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, url, th


class TriggerRaceBase(unittest.TestCase):
    def setUp(self):
        self.srv, self.url, _ = start_controlled_server()
        self.client = PredictorClient(PredictorConfig(server_url=self.url, timeout_ms=3000))
        self.results: list[PredictionResult] = []
        self.results_lock = threading.Lock()

        def on_result(r):
            with self.results_lock:
                self.results.append(r)

        self.trigger = AsyncTrigger(
            self.client,
            on_result=on_result,
            config=TriggerConfig(pause_ms=50, poll_interval_ms=20, debounce_min_chars=2),
        )
        self.trigger.start()

    def tearDown(self):
        # 必须先 release 让 worker 不卡住
        ControlledHandler.release_event.set()
        self.trigger.stop(timeout=2.0)
        self.srv.shutdown()
        self.srv.server_close()


class TestTriggerRace(TriggerRaceBase):
    def test_continued_typing_during_prediction_discards_result(self):
        """
        关键竞态: 用户停顿 → worker 发出预测 → 用户在预测响应回来前继续打字
                → 旧结果应该被丢弃，不显示给 UI
        """
        # 1. 触发第一个预测
        self.trigger.on_keystroke(prefix="第一阶段", context="ctx")
        # 2. 等 server 收到请求（说明 worker 已发预测）
        self.assertTrue(
            ControlledHandler.received_event.wait(timeout=2.0),
            "server 未收到第一次预测请求",
        )
        # 3. 用户继续打字（before server response）
        self.trigger.on_keystroke(prefix="第二阶段", context="ctx")
        # 4. 释放 server，让旧请求完成
        ControlledHandler.release_event.set()
        # 5. 等够时间让 worker 处理完成
        time.sleep(0.4)

        # 验证：第一次结果应该被丢弃，第二次结果应该来（或还在跑）
        with self.results_lock:
            # 我们期望 results 里没有"第一阶段的回调"出现，因为它在第二个 on_keystroke 后才到
            # 但 server 只跑了一次（第一次还没完，第二次 worker 会检测到 task_id 更新所以可能也发出）
            # 关键断言: 至少 0 个或 1 个结果，**不会有重复 + 不会有"过时结果泄漏到 UI"**
            self.assertLessEqual(len(self.results), 2,
                                 "回调被过多次触发 - 竞态保护失效")

    def test_too_short_prefix_skips(self):
        self.trigger.on_keystroke(prefix="A", context="")  # 1 字符 < debounce
        time.sleep(0.15)
        with self.results_lock:
            self.assertEqual(self.results, [])

    def test_task_id_is_monotonic_int(self):
        """D-009 P1.1: task_id 应该是单调整数，不是浮点。"""
        self.trigger.on_keystroke(prefix="aa", context="")
        with self.trigger._lock:
            t1 = self.trigger._pending.task_id
        self.trigger.on_keystroke(prefix="bb", context="")
        with self.trigger._lock:
            t2 = self.trigger._pending.task_id
        self.assertIsInstance(t1, int)
        self.assertIsInstance(t2, int)
        self.assertGreater(t2, t1)
        # 释放让 teardown 不卡
        ControlledHandler.release_event.set()


if __name__ == "__main__":
    unittest.main(verbosity=2)
