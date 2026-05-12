"""
EktroBaselineReranker 单元测试。

包含:
- 基础重排正确性
- 特征独立测试
- 退化场景（空候选 / 单候选 / 空上下文）
- 与 MemoryStore 集成
- ContextBuilder 行为
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.store import EktroMemoryStore  # noqa: E402
from rerank.baseline import EktroBaselineReranker, RerankerConfig  # noqa: E402
from shared.context_builder import build_context, build_predictor_prompt, ContextOptions  # noqa: E402


class RerankBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self.store = EktroMemoryStore(self.tmp.name)
        self.reranker = EktroBaselineReranker(self.store)

    def tearDown(self):
        self.store.close()
        for ext in ("", "-wal", "-shm"):
            p = self.tmp.name + ext
            if os.path.exists(p):
                os.unlink(p)


class TestBasic(RerankBase):
    def test_empty_candidates(self):
        self.assertEqual(self.reranker.rerank([]), [])

    def test_single_candidate(self):
        r = self.reranker.rerank(["你好"])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].candidate, "你好")
        self.assertEqual(r[0].new_rank, 0)

    def test_rime_prior_preserved_when_no_user_data(self):
        # 没有任何用户数据时，重排后顺序应该保持 librime 原序
        cands = ["甲", "乙", "丙", "丁"]
        r = self.reranker.rerank(cands)
        self.assertEqual([x.candidate for x in r], cands)

    def test_returns_ranked_metadata(self):
        r = self.reranker.rerank(["你好", "尼浩"])
        for x in r:
            self.assertGreaterEqual(x.new_rank, 0)
            self.assertGreaterEqual(x.base_rank, 0)
            self.assertIsInstance(x.features, dict)
            self.assertIn("user_freq", x.features)
            self.assertIn("bigram", x.features)
            self.assertIn("rime_prior", x.features)


class TestUserFreqBoost(RerankBase):
    def test_high_freq_chars_get_boosted(self):
        # 让用户大量输入"你好"
        for _ in range(50):
            self.store.log_commit("nihao", "你好")
        # 给两个候选：一个常见，一个用户没用过
        r = self.reranker.rerank(["你好", "尼浩"])
        # "你好" 因为字频高应该排第一
        self.assertEqual(r[0].candidate, "你好")
        # 验证 user_freq 特征确实给了 "你好" 更高分
        you_hao_uf = next(x for x in r if x.candidate == "你好").features["user_freq"]
        ni_hao_uf = next(x for x in r if x.candidate == "尼浩").features["user_freq"]
        self.assertGreater(you_hao_uf, ni_hao_uf)


class TestBigramBoost(RerankBase):
    def test_bigram_continuation(self):
        # 训练数据: 用户经常打 "咖啡" 后接 "豆"
        for _ in range(20):
            self.store.log_commit("kafeidou", "咖啡豆")
        # context 末字是 "啡"，候选有 "豆" 开头的和不是的
        r = self.reranker.rerank(
            ["豆子", "斗争", "都市"],
            context="我要买咖啡",
        )
        # "豆子"开头是"豆"，应该被 bigram 加分排第一
        self.assertEqual(r[0].candidate, "豆子")


class TestContextOverlap(RerankBase):
    def test_context_overlap_helps(self):
        # 最近 commit 里有"咖啡"
        self.store.log_commit("kafei", "咖啡")
        # 候选都没在用户库出现过，但其中一个与上下文有字符重叠
        r = self.reranker.rerank(
            ["咖啡店", "图书馆"],   # "咖啡店" 与 recent "咖啡" 有 2 字符重叠
            context="",
        )
        # 至少 context_overlap 给了 "咖啡店" 更高
        kfd = next(x for x in r if x.candidate == "咖啡店").features["context_overlap"]
        tsg = next(x for x in r if x.candidate == "图书馆").features["context_overlap"]
        self.assertGreater(kfd, tsg)


class TestRimePriorPreserved(RerankBase):
    def test_top_rime_candidate_wins_close_calls(self):
        # 没有用户数据时，base_rank 0 的候选应该排第一
        r = self.reranker.rerank(["位置0", "位置1", "位置2"])
        self.assertEqual(r[0].candidate, "位置0")


class TestContextBuilder(RerankBase):
    def test_build_context_uses_recent(self):
        for output in ["你好", "世界", "今天"]:
            self.store.log_commit("x", output)
        ctx = build_context(self.store, cursor_prefix="hello")
        # 至少含 cursor_prefix
        self.assertIn("hello", ctx)
        # 至少有一个 recent 字符出现
        self.assertTrue(any(c in ctx for c in "你好世界今天"))

    def test_build_context_respects_max_chars(self):
        for i in range(20):
            self.store.log_commit("x", f"输出{i}")
        ctx = build_context(
            self.store,
            cursor_prefix="",
            options=ContextOptions(max_chars=20),
        )
        self.assertLessEqual(len(ctx), 20)

    def test_predictor_prompt_short(self):
        for output in ["第一句", "第二句", "第三句", "第四句"]:
            self.store.log_commit("x", output)
        p = build_predictor_prompt(self.store, max_chars=30)
        self.assertLessEqual(len(p), 30)


class TestIntegration(RerankBase):
    """
    D-009 P1.7 强断言：验证 rerank 的真实行为（不是任何返回都通过）。
    """

    def test_rime_prior_holds_when_no_user_data(self):
        """空 store + 普通候选 → 完全保持 librime 原序。"""
        cands = ["甲乙", "丙丁", "戊己"]
        r = self.reranker.rerank(cands, context="无关上下文")
        self.assertEqual([c.candidate for c in r], cands,
                         "空 store 无 context 共振时，rerank 必须等同 librime 原序")

    def test_user_freq_alone_can_reorder(self):
        """用户字频已经足以改变排序（无需 bigram）。"""
        # 用户重度写 "啡"
        for _ in range(15):
            self.store.log_commit("kafei", "咖啡")

        r = self.reranker.rerank(["非常", "啡"], context="")  # 无 context
        self.assertEqual(r[0].candidate, "啡",
                         "用户字频 15 应当让 '啡' 反超 '非常'（user_freq 特征生效）")
        # 验证不是其他特征意外造成（context 为空 → bigram=0）
        f = next(c for c in r if c.candidate == "啡").features
        self.assertEqual(f["bigram"], 0)
        self.assertGreater(f["user_freq"], 0)

    def test_bigram_alone_reorders_when_freq_equal(self):
        """字频持平时，bigram 决定 top-1。"""
        # 双向训练：让 "啡" 和 "常" 字频持平
        for _ in range(5):
            self.store.log_commit("kafei", "咖啡")
            self.store.log_commit("ping", "平常")  # 让"常"字频也 +5
        # 现在 啡=5, 常=5

        r = self.reranker.rerank(["非常", "啡"], context="我要买咖")
        # bigram 咖→啡 = 5; 咖→非 = 0 → "啡" top-1
        self.assertEqual(r[0].candidate, "啡")
        fei_features = next(c for c in r if c.candidate == "啡").features
        self.assertGreater(fei_features["bigram"], 0)

    def test_score_gap_meaningful(self):
        """top-1 与 top-2 的 score 差距应该是"明显"，不是浮点噪声。"""
        for _ in range(20):
            self.store.log_commit("kafei", "咖啡")
        r = self.reranker.rerank(["非常", "啡", "废铁"], context="我要买咖")
        gap = r[0].score - r[1].score
        self.assertGreater(gap, 0.3,
                           f"top-1/top-2 score 差距 {gap:.3f} 太小，可能是浮点噪声")


if __name__ == "__main__":
    unittest.main(verbosity=2)
