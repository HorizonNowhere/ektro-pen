"""
上下文构造器：从 MemoryStore 抽取最近输入，拼成 rerank/predictor 用的上下文字符串。

为什么独立模块:
- rerank 和 predictor 都需要"光标前文 + 历史"，逻辑应该共用
- 上下文构造涉及多个决策：拼接策略 / 时间截断 / 应用过滤 / 字数上限
- 单独测试方便

design.md §3 §5 提到的"最近 5 条 commit 拼接，最长 256 token"在这里实现。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from memory.store import EktroMemoryStore


@dataclass
class ContextOptions:
    """上下文构造选项。"""
    max_chars: int = 80          # 总字符数上限（中文字符）
    n_recent_commits: int = 5    # 取最近 N 条 commit
    within_minutes: int = 60     # 仅取这 N 分钟内的 commit（更早的认为"上下文已断"）
    same_app_only: bool = False  # 是否只取当前应用的 commit
    include_pinyin: bool = False # 是否带拼音原文（实验功能）


def build_context(
    store: "EktroMemoryStore",
    *,
    cursor_prefix: str = "",      # 光标前已经输入的字符（应用层提供）
    current_app: Optional[str] = None,
    options: Optional[ContextOptions] = None,
) -> str:
    """
    构造重排/预测用的上下文字符串。

    策略（优先级从高到低）:
      1. cursor_prefix（"刚才正在打的"，最权威）
      2. 最近 N 条 commit（按时间倒排，连接 → 字符截断）
      3. 全部裁切到 max_chars

    Returns:
        单个字符串，准备喂给 reranker.rerank(context=...) 或 predictor。
    """
    opts = options or ContextOptions()
    parts: List[str] = []

    # 头部：cursor_prefix（如果有）
    if cursor_prefix:
        parts.append(cursor_prefix)

    # 尾部：最近 commits
    since_ms = int((time.time() - opts.within_minutes * 60) * 1000)
    records = store.recent_outputs(limit=opts.n_recent_commits, since_ms=since_ms)

    if opts.same_app_only and current_app:
        records = [r for r in records if r.app_name == current_app]

    # records 是 DESC 时间，要"最远→最近"拼接才符合阅读顺序
    historical_outputs = [r.output for r in reversed(records)]

    historical_joined = "".join(historical_outputs)
    if historical_joined:
        # 加分隔符 (后续生成时模型能识别)
        parts.insert(0, historical_joined + " ")

    full = "".join(parts)
    # 从末尾裁切（保留最近的，丢弃最远的）
    if len(full) > opts.max_chars:
        full = full[-opts.max_chars:]

    return full


def build_predictor_prompt(
    store: "EktroMemoryStore",
    *,
    cursor_prefix: str = "",
    current_app: Optional[str] = None,
    max_chars: int = 50,         # Predictor 上下文更严格 (D-004 教训：35 tokens 是边界)
) -> str:
    """
    专为 Qwen3 Predictor 设计的 prompt。

    根据 D-004 决策：50 字符 ≈ 35 tokens ≈ medium_50tok P50 142ms ✅ 边界 PASS

    返回的 prompt 直接喂给 llama-server /completion endpoint。
    """
    opts = ContextOptions(
        max_chars=max_chars,
        n_recent_commits=2,        # 只取最近 1-2 条 commit（D-004 修订）
        within_minutes=10,         # 短时记忆
        same_app_only=False,
    )
    return build_context(
        store,
        cursor_prefix=cursor_prefix,
        current_app=current_app,
        options=opts,
    )
