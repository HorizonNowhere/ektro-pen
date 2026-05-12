"""
AsyncTrigger — 异步预测触发器。

职责（design.md §4 + D-004）:
- 监听用户按键事件（"打字"）
- 检测停顿（最后按键 >300ms 无新输入 → 触发预测）
- 后台 worker 线程跑 Predictor.predict()，**不阻塞 IME 主路径**
- 完成时回调 (text, latency) → IME UI 层显示淡灰文本
- 用户继续打字 → 取消未完成的预测请求

线程模型:
    IME 主线程         worker 线程
      │                  │
      ├ on_keystroke ───→│
      │  (更新待处理任务)  │
      │                  │ 循环:
      │                  │   sleep 50ms
      │                  │   if (now - last_key) > delay:
      │                  │      task = pending_task
      │                  │      result = client.predict(task)
      │                  │      callback(result)
      │                  │
      └ stop ──────────→ shutdown
"""
from __future__ import annotations

import itertools
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .client import PredictorClient, PredictionResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

# 单调任务 ID 生成器（D-009 P1.1：替代浮点 perf_counter）
_task_id_counter = itertools.count(1)


@dataclass
class TriggerConfig:
    pause_ms: int = 300                # 停顿阈值（D-007: design 默认）
    poll_interval_ms: int = 30         # worker 检测周期
    debounce_min_chars: int = 2        # 至少 N 个字符才触发（避免过早预测）


@dataclass
class PendingTask:
    prefix: str
    context: str
    task_id: int                       # 单调计数器 (D-009 P1.1)
    submitted_at: float                # perf_counter，仅用于停顿计时


class AsyncTrigger:
    """
    线程安全的异步预测包装器。

    用法（在 IME 中）:
        client = PredictorClient(config)
        trigger = AsyncTrigger(client, on_result=lambda r: render_gray(r.text))
        trigger.start()

        # 每次按键
        trigger.on_keystroke(prefix="我今天", context="早上喝了一杯咖啡")

        # 关闭时
        trigger.stop()
    """

    def __init__(
        self,
        client: PredictorClient,
        on_result: Callable[[PredictionResult], None],
        config: Optional[TriggerConfig] = None,
    ):
        self._client = client
        self._on_result = on_result
        self._cfg = config or TriggerConfig()

        self._lock = threading.Lock()
        self._pending: Optional[PendingTask] = None
        self._last_completed_task_id: Optional[int] = None
        self._last_keystroke: float = 0.0
        self._latest_task_id: int = 0           # 最新 on_keystroke 派的 id
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

    # ──────── 生命周期 ────────

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._loop, name="EktroPredictor", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=timeout)
            self._worker = None

    # ──────── 公开 API（IME 主线程调用）────────

    def on_keystroke(self, prefix: str, context: str = "") -> None:
        """每次用户按键调用。后台 worker 会检测停顿并发预测。"""
        now = time.perf_counter()
        with self._lock:
            self._last_keystroke = now
            if len(prefix) < self._cfg.debounce_min_chars:
                self._pending = None
                return
            self._latest_task_id = next(_task_id_counter)
            self._pending = PendingTask(
                prefix=prefix, context=context,
                task_id=self._latest_task_id,
                submitted_at=now,
            )

    def cancel(self) -> None:
        """显式取消（例如用户接受了候选）。"""
        with self._lock:
            self._pending = None

    # ──────── 后台 worker ────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self._cfg.poll_interval_ms / 1000.0)
            with self._lock:
                if self._pending is None:
                    continue
                idle_ms = (time.perf_counter() - self._last_keystroke) * 1000
                if idle_ms < self._cfg.pause_ms:
                    continue
                if self._last_completed_task_id == self._pending.task_id:
                    continue
                task = self._pending
                self._last_completed_task_id = task.task_id

            # 跑预测（释放锁后），可能花 50-200 ms
            result = self._client.predict(prefix=task.prefix, context=task.context)

            # 完成时再次检查：用户是否又打字了？
            with self._lock:
                if task.task_id < self._latest_task_id:
                    # 用户已经又打字（提交了更新的 task），丢弃这次结果
                    continue
                if self._pending is None:
                    continue

            # 派发结果 (D-011 P0.5.1: 只派发"成功且非空"的结果到 UI)
            # error / 空文本结果只记日志，不打扰用户视线（公理 ①）
            if not result.is_ok:
                logger.debug("trigger suppress non-ok result: kind=%s",
                             result.error_kind.value if hasattr(result, 'error_kind') else 'unknown')
                continue
            try:
                self._on_result(result)
            except Exception:  # noqa: BLE001
                logger.exception("AsyncTrigger on_result callback failed")
