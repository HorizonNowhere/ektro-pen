"""
BaselinePredictor — 基于 phrase_pair 二元组链的统计续写器。

设计动机:
- LLM (Qwen3-0.6B) 在 raw mode 下对中文续写质量不稳定
- 用户自己的 phrase_pair 数据已经隐含了"接续概率"
- 当 LLM 失败/超时/无效时，用 baseline 救场
- 是 D-007 "先 baseline 再 ML" 思路的延续

算法 (简单贪心 N-gram 续写):
    输入: cursor_prefix
    步骤:
      1. 从 prefix 末字开始: curr = prefix[-1]
      2. lookup phrase_pair WHERE prev=curr → 按 count DESC
      3. 取 top-1 的 curr_char 作为续写第 1 字
      4. 重复直到 max_chars 或无继续候选

返回 string，长度 ≤ max_chars。空 string 表示无续写。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

if TYPE_CHECKING:
    from shared.protocols import MemoryView

# 用 PredictionResult 让 BaselinePredictor 与 PredictorClient 接口统一
# (D-011 P0.5.6)
from .client import PredictionResult, PredictionErrorKind


class BaselinePredictor:
    """
    用 phrase_pair 二元组贪心生成续写。
    返回 PredictionResult，与 PredictorClient 接口一致 (D-011 P0.5.6)。
    """

    def __init__(self, store: "MemoryView"):
        self.store = store

    def predict_text(
        self,
        cursor_prefix: str,
        *,
        max_chars: int = 8,
        min_count: int = 1,
    ) -> str:
        """
        基于 phrase_pair 链生成最长 max_chars 字符的续写。

        Args:
            cursor_prefix: 光标前已经输入的字符串
            max_chars: 最大续写长度
            min_count: phrase_pair 的最低 count 阈值（过滤极低频）

        Returns:
            续写字符串，无候选时返回 ""
        """
        if not cursor_prefix:
            return ""

        out: list[str] = []
        curr = cursor_prefix[-1]
        visited = set()  # 防止环 ("的"→"的"→"的")

        for _ in range(max_chars):
            pairs = self.store.phrase_pair_lookup(curr)
            # 找 count ≥ min_count 且未访问过的第一个候选
            chosen: Optional[str] = None
            for next_char, count in pairs:
                if count < min_count:
                    break
                if next_char in visited:
                    continue
                chosen = next_char
                break

            if chosen is None:
                break

            out.append(chosen)
            visited.add(chosen)
            curr = chosen

        return "".join(out)

    def predict(
        self,
        cursor_prefix: str,
        *,
        max_chars: int = 8,
        min_count: int = 1,
        **_ignored,
    ) -> PredictionResult:
        """
        统一接口入口 (D-011 P0.5.6 BasePredictor Protocol)。
        包装 predict_text 为 PredictionResult，让调用方代码可替换后端。
        """
        import time
        start = time.perf_counter()
        text = self.predict_text(cursor_prefix, max_chars=max_chars, min_count=min_count)
        elapsed = (time.perf_counter() - start) * 1000
        return PredictionResult(
            text=text,
            total_ms=elapsed,
            error_kind=PredictionErrorKind.OK if text else PredictionErrorKind.EMPTY,
        )
