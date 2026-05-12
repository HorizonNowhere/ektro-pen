"""
EktroPredictor — 下一句预测 HTTP 客户端。

后端：llama-server (b9102+) 的 /completion endpoint
模型：Qwen3-0.6B Q4_K_M (Day 1 Spike 验证)

设计决策来自:
- D-004: server 模式 + 上下文 ≤50 字符 → P50 ~142ms 边界 PASS
- D-006: Predictor 异步非阻塞，超时静默降级
- D-007: 复用 rerank/context_builder.py，不另写

接口契约（与未来 ONNX 路径兼容）:
    predictor.predict(prefix: str, context: str = "") -> Optional[str]
"""
from __future__ import annotations

import dataclasses
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.logging import get_logger  # noqa: E402

logger = get_logger(__name__)


class PredictionErrorKind(Enum):
    """
    Predictor 失败原因分类 (D-009 P0.5)。
    调用方据此决定: 重试 / 降级 baseline / 告警用户。
    """
    OK = "ok"
    EMPTY = "empty"                # 模型有响应但内容空（或被质量门拦）
    TIMEOUT = "timeout"            # 客户端超时（socket.timeout）
    SERVER_DOWN = "server_down"    # 连不上 server (ConnectionRefused etc.)
    HTTP_ERROR = "http_error"      # server 返 4xx/5xx
    PARSE_ERROR = "parse_error"    # JSON 解析失败
    UNKNOWN = "unknown"            # 其他

    @property
    def is_ok(self) -> bool:
        return self == PredictionErrorKind.OK

    @property
    def is_retryable(self) -> bool:
        return self in (PredictionErrorKind.TIMEOUT, PredictionErrorKind.SERVER_DOWN)


# ──────────── 默认配置（与 design.md §6 + D-004 对齐） ────────────

DEFAULT_SERVER_URL = "http://127.0.0.1:8088"
DEFAULT_TIMEOUT_MS = 200          # design.md SLO 上限
DEFAULT_MAX_CONTEXT_CHARS = 50    # D-004 实测边界
DEFAULT_N_PREDICT = 8             # 一次最多续写 8 个字符（淡灰显示足够）


@dataclass
class PredictorConfig:
    server_url: str = DEFAULT_SERVER_URL
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS
    n_predict: int = DEFAULT_N_PREDICT
    # D-009 prompt-engineering 微调：temperature=0 + top_k=1 在 raw /completion
    # 模式下会让 Qwen3 退化到 "!!!!" 这种重复字符。0.3 + top_k=40 给出多样性
    # 同时仍可缓存（缓存键含完整 prompt，相同 prompt 第二次直接命中无 LLM 调用）
    temperature: float = 0.3
    top_k: int = 40
    cache_capacity: int = 128


@dataclass
class PredictionResult:
    """
    Predictor 调用结果 (D-009 P0.5)。

    使用模式:
        if result.is_ok:
            display(result.text)
        elif result.error_kind.is_retryable:
            schedule_retry()
        else:
            log_and_fallback(result.error_kind, result.error_detail)
    """
    text: str
    prompt_tokens: Optional[int] = None
    prefill_ms: Optional[float] = None
    total_ms: float = 0.0
    cache_hit: bool = False
    error_kind: PredictionErrorKind = PredictionErrorKind.OK
    error_detail: Optional[str] = None

    @property
    def is_ok(self) -> bool:
        """成功且有非空文本。"""
        return self.error_kind == PredictionErrorKind.OK and bool(self.text)

    @property
    def error(self) -> Optional[str]:
        """向后兼容旧 .error 字段；新代码请用 .error_kind / .error_detail。"""
        if self.error_kind == PredictionErrorKind.OK:
            return None
        return self.error_detail or self.error_kind.value


@dataclass
class PredictorStats:
    """运行时统计，用于用户面板和调试。"""
    n_calls: int = 0
    n_success: int = 0
    n_timeout: int = 0
    n_error: int = 0
    n_cache_hit: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.n_success, 1)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self) | {"avg_latency_ms": self.avg_latency_ms}


# ──────────── 简易 LRU 缓存 ────────────


class _LruCache:
    """Insertion-order dict 做 LRU。同 prompt 不重复发请求。"""

    def __init__(self, capacity: int):
        self._capacity = capacity
        self._data: dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        if key not in self._data:
            return None
        # move to end
        value = self._data.pop(key)
        self._data[key] = value
        return value

    def put(self, key: str, value: str) -> None:
        if key in self._data:
            self._data.pop(key)
        self._data[key] = value
        while len(self._data) > self._capacity:
            self._data.pop(next(iter(self._data)))

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()


# ──────────── 主客户端 ────────────


class PredictorClient:
    """
    同步 HTTP 客户端到 llama-server /completion。

    异步触发（停顿检测、worker 线程）由 AsyncTrigger 包装。
    本类只负责"一次请求 → 一个结果"。
    """

    def __init__(self, config: Optional[PredictorConfig] = None):
        self.cfg = config or PredictorConfig()
        self._cache = _LruCache(self.cfg.cache_capacity)
        self.stats = PredictorStats()

    # ──────── 健康检查 ────────

    def health(self) -> bool:
        """检测 server 是否可用。返回 True/False，不抛异常。"""
        try:
            with urllib.request.urlopen(
                f"{self.cfg.server_url}/health",
                timeout=max(self.cfg.timeout_ms / 1000.0, 1.0),
            ) as resp:
                return resp.status == 200
        except Exception as e:  # noqa: BLE001
            logger.debug("health probe failed: %r", e)
            return False

    # ──────── 主入口 ────────

    def predict(self, prefix: str, context: str = "") -> PredictionResult:
        """
        给定光标前文 + 历史上下文，预测后续 N 字符。

        Args:
            prefix: 用户当前光标前的文字（"我今天早上喝了"）
            context: 历史上下文（最近 commits 拼接），通过 build_predictor_prompt 生成

        Returns:
            PredictionResult，含预测文本或 error 字段
        """
        self.stats.n_calls += 1
        start = time.perf_counter()

        # 拼接 prompt（context 在前，prefix 在后，模型续写 prefix 之后的内容）
        prompt = self._compose_prompt(prefix, context)

        # 缓存命中
        cached = self._cache.get(prompt)
        if cached is not None:
            self.stats.n_cache_hit += 1
            elapsed = (time.perf_counter() - start) * 1000
            return PredictionResult(
                text=cached, total_ms=elapsed, cache_hit=True,
                error_kind=PredictionErrorKind.OK if cached else PredictionErrorKind.EMPTY,
            )

        # D-009 prompt engineering: 用 raw /completion 让模型纯文本续写
        # （chat 模式 Qwen3-0.6B 对中文指令理解能力有限）
        # 简单文本流续写让模型按上下文自然往下写。
        body = json.dumps({
            "prompt": prompt,
            "n_predict": self.cfg.n_predict,
            "cache_prompt": False,
            "temperature": self.cfg.temperature,
            "top_k": self.cfg.top_k,
            "stop": ["\n", "。", "！", "？", "!", "?", " ", ",", "，"],
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.cfg.server_url}/completion",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                req, timeout=self.cfg.timeout_ms / 1000.0
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            elapsed = (time.perf_counter() - start) * 1000
            self.stats.n_error += 1
            logger.warning("predict HTTP %d: %r", e.code, e)
            return PredictionResult(
                text="", total_ms=elapsed,
                error_kind=PredictionErrorKind.HTTP_ERROR,
                error_detail=f"HTTP {e.code}",
            )
        except urllib.error.URLError as e:
            elapsed = (time.perf_counter() - start) * 1000
            # D-009 P1.2: isinstance 检测 timeout（不靠字符串匹配）
            if isinstance(e.reason, socket.timeout) or isinstance(e.reason, TimeoutError):
                self.stats.n_timeout += 1
                logger.debug("predict timeout after %.0fms", elapsed)
                return PredictionResult(
                    text="", total_ms=elapsed,
                    error_kind=PredictionErrorKind.TIMEOUT,
                )
            self.stats.n_error += 1
            logger.warning("predict URLError: %r", e)
            return PredictionResult(
                text="", total_ms=elapsed,
                error_kind=PredictionErrorKind.SERVER_DOWN,
                error_detail=repr(e),
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            elapsed = (time.perf_counter() - start) * 1000
            self.stats.n_error += 1
            logger.warning("predict parse error: %r", e)
            return PredictionResult(
                text="", total_ms=elapsed,
                error_kind=PredictionErrorKind.PARSE_ERROR,
                error_detail=repr(e),
            )
        except TimeoutError as e:  # urllib 可能直接抛 socket.timeout / TimeoutError
            elapsed = (time.perf_counter() - start) * 1000
            self.stats.n_timeout += 1
            logger.debug("predict TimeoutError after %.0fms", elapsed)
            return PredictionResult(
                text="", total_ms=elapsed,
                error_kind=PredictionErrorKind.TIMEOUT,
            )
        except Exception as e:  # noqa: BLE001 — 任何异常都降级，不挂掉调用方
            elapsed = (time.perf_counter() - start) * 1000
            self.stats.n_error += 1
            logger.warning("predict unexpected error: %r", e)
            return PredictionResult(
                text="", total_ms=elapsed,
                error_kind=PredictionErrorKind.UNKNOWN,
                error_detail=repr(e),
            )

        elapsed = (time.perf_counter() - start) * 1000
        text = (data.get("content") or "").strip()
        timings = data.get("timings", {})
        prefill_ms_val = timings.get("prompt_ms")
        prompt_tokens_count = timings.get("prompt_n")

        # 后处理：去掉 stop tokens 残留
        for stop_ch in ("\n", "。", "！", "？", "!", "?", " ", ",", "，"):
            while text.endswith(stop_ch):
                text = text[:-1]

        # 退化检测 (D-009 质量门 — 中文输入法续写的合理性)
        if text:
            # 1. 全部相同字符（"!!!!"、"AAA"）
            if len(text) >= 3 and len(set(text)) == 1:
                logger.debug("predict degenerate (repeat) output, discarding")
                text = ""
            # 2. 含 ASCII 标点/符号（"E3"、"30517&7"、"5C" 含 5）
            elif any(0x21 <= ord(c) <= 0x2F or 0x3A <= ord(c) <= 0x40
                     or 0x5B <= ord(c) <= 0x60 or 0x7B <= ord(c) <= 0x7E
                     or 0x30 <= ord(c) <= 0x39  # 数字
                     for c in text):
                logger.debug("predict non-CJK output, low quality, discarding")
                text = ""
            # 3. 纯 ASCII 字母（"abc"）也算低质量（中文场景）
            elif all(ord(c) < 128 for c in text):
                logger.debug("predict pure-ASCII output, discarding")
                text = ""
            # 4. 太短 (< 2 字符) 算无效预测
            elif len(text) < 2:
                text = ""

        result = PredictionResult(
            text=text,
            prompt_tokens=prompt_tokens_count,
            prefill_ms=prefill_ms_val,
            total_ms=elapsed,
            error_kind=PredictionErrorKind.OK if text else PredictionErrorKind.EMPTY,
        )

        # 仅缓存"成功且非空"的预测
        if text:
            self._cache.put(prompt, text)
            self.stats.n_success += 1
            self.stats.total_latency_ms += elapsed

        return result

    # ──────── 工具 ────────

    def _compose_prompt(self, prefix: str, context: str) -> str:
        """
        把 context + prefix 组装成一个 prompt。

        策略：context 截到 max_context_chars 后接 prefix，让模型续写 prefix 之后的内容。
        """
        ctx = (context or "")[-self.cfg.max_context_chars :]
        return f"{ctx}{prefix}"

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_size(self) -> int:
        return len(self._cache)
