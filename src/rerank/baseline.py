"""
EktroBaselineReranker — 统计方法重排器（无神经网络）。

设计原则:
- **先写 baseline 再上 ML**（Karpathy 派最佳实践）
- 用 MemoryStore 的真实数据驱动：字频 + 二元组 + 上下文匹配
- ≤ 3ms P95（design.md §6 EktroRerankFilter 30ms 预算的 1/10）
- 纯 Python + 标准库，无 torch / numpy 依赖

工作原理:
  给定候选 [c1, c2, ..., cN] 与上下文 (recent commits 拼接):
    score(c) = w1·user_freq(c)        # 用户高频字加分
             + w2·bigram_match(c, context)  # 二元组接续概率
             + w3·context_overlap(c, recent)  # 上下文字符共现
             + w4·rime_rank_inverse(idx)  # 保留 librime 排序作为先验

  返回按 score 降序的候选 + 分数。

与神经 ranker 的接口兼容: 都是 (candidates, context) → ranked_with_scores
未来切换到 ONNX GRU 时调用方零改动。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from shared.protocols import MemoryView


@dataclass(frozen=True)
class RankedCandidate:
    candidate: str
    score: float
    base_rank: int     # 原始 librime 排名（0-based）
    new_rank: int      # 重排后排名（0-based）
    features: dict     # 调试用：各特征贡献


@dataclass
class RerankerConfig:
    """每个特征的权重。默认配置经过 sanity-check，可调。"""
    w_user_freq: float = 1.0        # 用户字频加分
    w_bigram: float = 2.0           # 二元组接续（context 末字 → candidate 首字）
    w_context_overlap: float = 0.5  # candidate 与 recent context 的字符共现
    w_rime_prior: float = 3.0       # 保留 librime 原始排序的强度
    smoothing: float = 1.0          # 防止 log(0)
    max_context_chars: int = 80     # 上下文最大字符数（不让 query 拖慢）


class EktroBaselineReranker:
    """
    使用 EktroMemoryStore 数据对候选重排。

    用法:
        from memory.store import EktroMemoryStore
        from rerank.baseline import EktroBaselineReranker

        store = EktroMemoryStore("user.db")
        reranker = EktroBaselineReranker(store)

        candidates = ["你好世界", "你好十届", "泥嚎诗节"]
        context = "我今天打开电脑想写"
        ranked = reranker.rerank(candidates, context=context)
        # ranked[0].candidate 是最贴合用户的最优解
    """

    def __init__(
        self,
        store: "MemoryView",
        config: Optional[RerankerConfig] = None,
    ):
        # 类型契约: store 只需要满足 MemoryView Protocol (recent_outputs /
        # word_freq_lookup / phrase_pair_lookup)。EktroMemoryStore 实现了这个
        # Protocol，但其他兼容实现（如 mock / 远端代理）也能传入。
        self.store = store
        self.cfg = config or RerankerConfig()

    # ──────────── 主入口 ────────────

    def rerank(
        self,
        candidates: Sequence[str],
        context: str = "",
        recent_outputs: Optional[List[str]] = None,
    ) -> List[RankedCandidate]:
        """
        重排候选。

        参数:
          candidates: librime 返回的候选列表（按其原始打分排序）
          context: 即将被插入的位置之前的文字（"光标前文"）
          recent_outputs: 最近 N 条 commit 的输出（如果调用方已加载就传，否则自动从 store 拉）

        返回: 重排后的 RankedCandidate 列表（按 score 降序）
        """
        if not candidates:
            return []
        if len(candidates) == 1:
            # 单候选时直接返回，避免无谓查询
            return [RankedCandidate(candidates[0], 1.0, 0, 0, {"single_candidate": 1.0})]

        # 限制上下文长度
        context = (context or "")[-self.cfg.max_context_chars:]

        # 拉 recent outputs（如果调用方没给）
        if recent_outputs is None:
            recent_records = self.store.recent_outputs(limit=10)
            recent_outputs = [r.output for r in recent_records]
        recent_text = "".join(recent_outputs)[-self.cfg.max_context_chars:]

        # 收集所有候选用到的字 (批量查 word_freq，省 N 次 round-trip)
        all_chars = {ch for cand in candidates for ch in cand}
        freq_table = self.store.word_freq_lookup(list(all_chars))

        # D-009 P1.3: 二元组也批量查（消 N+1）
        # 每个候选只用 (context末字 → candidate首字) 一组，所以 prev 列表 = {context末字}
        prev_chars: set[str] = set()
        if context:
            prev_chars.add(context[-1])
        pair_table: dict = {}
        if prev_chars and hasattr(self.store, "phrase_pair_batch_lookup"):
            pair_table = self.store.phrase_pair_batch_lookup(prev_chars)
        elif prev_chars:
            # 兜底: 旧 MemoryView 不支持 batch
            for p in prev_chars:
                pair_table[p] = self.store.phrase_pair_lookup(p)

        # 计算每个候选的 score
        scored: List[Tuple[str, float, int, dict]] = []
        for base_rank, cand in enumerate(candidates):
            score, features = self._score(
                cand, base_rank, context, recent_text, freq_table, pair_table
            )
            scored.append((cand, score, base_rank, features))

        # 按 score 降序排
        scored.sort(key=lambda t: t[1], reverse=True)

        return [
            RankedCandidate(
                candidate=cand,
                score=score,
                base_rank=base_rank,
                new_rank=new_rank,
                features=features,
            )
            for new_rank, (cand, score, base_rank, features) in enumerate(scored)
        ]

    # ──────────── 特征计算 ────────────

    def _score(
        self,
        cand: str,
        base_rank: int,
        context: str,
        recent_text: str,
        freq_table: dict,
        pair_table: dict,
    ) -> Tuple[float, dict]:
        """计算单个候选的分数。返回 (score, feature_breakdown)"""
        f = {}

        # F1: 用户字频（log 防爆炸 + smoothing）
        if cand:
            user_freq_sum = sum(
                math.log(freq_table.get(ch, 0) + self.cfg.smoothing) for ch in cand
            ) / len(cand)
        else:
            user_freq_sum = 0.0
        f["user_freq"] = self.cfg.w_user_freq * user_freq_sum

        # F2: 二元组接续 (context 末字 → candidate 首字)，用预查表消 N+1
        if context and cand:
            prev_char = context[-1]
            curr_char = cand[0]
            pairs_for_prev = pair_table.get(prev_char, [])
            pair_count = next((c for cu, c in pairs_for_prev if cu == curr_char), 0)
            f["bigram"] = self.cfg.w_bigram * math.log(pair_count + self.cfg.smoothing)
        else:
            f["bigram"] = 0.0

        # F3: 上下文字符共现 (candidate 中有多少字在 recent_text 出现过)
        if cand and recent_text:
            overlap = sum(1 for ch in cand if ch in recent_text) / len(cand)
            f["context_overlap"] = self.cfg.w_context_overlap * overlap
        else:
            f["context_overlap"] = 0.0

        # F4: librime 原始排序先验 (越靠前的候选基础分越高)
        # 用 exponential decay: rime_prior = exp(-base_rank / 5)
        f["rime_prior"] = self.cfg.w_rime_prior * math.exp(-base_rank / 5.0)

        total = sum(f.values())
        return total, f


def build_default_reranker(store: "MemoryView") -> EktroBaselineReranker:
    """快捷工厂方法。"""
    return EktroBaselineReranker(store)
