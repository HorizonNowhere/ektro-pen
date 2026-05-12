"""
EKTRO 显式 Protocols (D-009 P1.5)

让"接口稳定，实现可替换"从营销变成代码契约。
- Reranker:        baseline 与未来 ONNX GRU 实现同接口
- MemoryView:      reranker / predictor 只暴露最小读权限给 store
- BasePredictor:   PredictorClient + BaselinePredictor 同接口
- ContextSource:   构造上下文字符串的源（store 提供）

这些 Protocol 在 C++ 移植时映射为纯虚类 / interface。
"""
"""
EKTRO 显式 Protocols (D-009 P1.5 + D-011 P0.5.5 参数化重写)

C++ 移植规约 — 每个 Protocol 都映射成纯虚类 / interface:
- 返回类型完全参数化（List[CommitRecord] 而非裸 List）
- BasePredictor 统一返回 PredictionResult（让两种实现真正可替换）
- phrase_pair_batch_lookup 标 Optional（不实现也能用 fallback）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

if TYPE_CHECKING:
    # 仅类型检查时引入具体类型，运行时无循环依赖风险
    from memory.store import CommitRecord
    from rerank.baseline import RankedCandidate
    from predictor.client import PredictionResult


# ──────────────────────────────────────────────────────────────────────
# MemoryView — reranker / predictor 看到的最小 store 视图
#
# 这个 Protocol 不暴露 clear_all / log_commit 等破坏性方法
# 完美符合接口隔离原则 (ISP)
# ──────────────────────────────────────────────────────────────────────


@runtime_checkable
class MemoryView(Protocol):
    """只读视图。任何提供这些方法的类都能被 reranker/predictor 使用。"""

    def recent_outputs(
        self, limit: int = 20, since_ms: Optional[int] = None
    ) -> List["CommitRecord"]:
        """返回最近 N 条 commit 记录 (按时间倒排)。"""
        ...

    def word_freq_lookup(self, words: Iterable[str]) -> Dict[str, int]:
        """批量查字频。未出现的字 count=0。"""
        ...

    def phrase_pair_lookup(self, prev: str) -> List[Tuple[str, int]]:
        """给定 prev 字，返回所有 (curr, count) 按 count 倒排。"""
        ...


@runtime_checkable
class BatchMemoryView(MemoryView, Protocol):
    """
    可选的批量查询扩展 (D-011 P0.5.5)。
    Reranker 通过 hasattr 检测，无此能力会 fallback 到单次查询。
    C++ 端实现为 IMemoryView 的子接口（IBatchMemoryView : IMemoryView）。
    """

    def phrase_pair_batch_lookup(
        self, prev_chars: Iterable[str]
    ) -> Dict[str, List[Tuple[str, int]]]:
        """批量查多个 prev 字的二元组。"""
        ...


# ──────────────────────────────────────────────────────────────────────
# Reranker — 候选重排器接口
# ──────────────────────────────────────────────────────────────────────


@runtime_checkable
class Reranker(Protocol):
    def rerank(
        self,
        candidates,
        context: str = "",
        recent_outputs: Optional[List[str]] = None,
    ) -> List["RankedCandidate"]:
        """重排候选。返回 RankedCandidate (按 score 降序) 的 list。"""
        ...


# ──────────────────────────────────────────────────────────────────────
# BasePredictor — 下一句预测器接口
# D-011 P0.5.6: 统一返回 PredictionResult，让两种实现真正可替换
# ──────────────────────────────────────────────────────────────────────


@runtime_checkable
class BasePredictor(Protocol):
    def predict(self, cursor_prefix: str, **kwargs) -> "PredictionResult":
        """
        给定光标前文，返回 PredictionResult。
        - LLM 实现 (PredictorClient): 调远端，可能 timeout/error
        - 统计实现 (BaselinePredictor): 基于 phrase_pair 链，零延迟
        - 未来 ONNX 实现: 端侧神经网络

        调用方代码: if result.is_ok: render(result.text) — 不必关心后端类型
        """
        ...
