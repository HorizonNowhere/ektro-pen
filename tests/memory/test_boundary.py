"""
边界条件测试 (D-009 P1.6)。

覆盖 swarm 验收发现的盲点：
- emoji / 组合字符（带音调）
- 零长度 / 超长输入
- 纯空白
- 注入字符（SQL/控制字符）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.store import EktroMemoryStore, LogResult  # noqa: E402


class BoundaryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self.store = EktroMemoryStore(self.tmp.name)

    def tearDown(self):
        self.store.close()
        for ext in ("", "-wal", "-shm"):
            p = self.tmp.name + ext
            if os.path.exists(p):
                os.unlink(p)


class TestEmojiAndCJK(BoundaryBase):
    """Unicode 边界：emoji、组合字符、扩展平面。"""

    def test_basic_emoji_commit(self):
        outcome = self.store.log_commit("biaoqing", "😀好心情")
        self.assertEqual(outcome.result, LogResult.COMMITTED)
        recent = self.store.recent_outputs(limit=5)
        self.assertEqual(recent[0].output, "😀好心情")

    def test_emoji_in_word_freq(self):
        self.store.log_commit("a", "😀😀好")
        wf = self.store.word_freq_lookup(["😀", "好"])
        # 4 字符 emoji（surrogate pair）应该计为单字符
        self.assertEqual(wf["😀"], 2)
        self.assertEqual(wf["好"], 1)

    def test_combining_chars(self):
        # 带音调的拉丁拼音（ā = a + 组合字符或预组合字符）
        outcome = self.store.log_commit("a", "māma")
        self.assertEqual(outcome.result, LogResult.COMMITTED)

    def test_traditional_chinese(self):
        outcome = self.store.log_commit("zhongwen", "繁體中文")
        self.assertEqual(outcome.result, LogResult.COMMITTED)
        wf = self.store.word_freq_lookup(["繁", "體"])
        self.assertEqual(wf["繁"], 1)
        self.assertEqual(wf["體"], 1)


class TestZeroAndOverLong(BoundaryBase):
    """零长度 / 超长输入。"""

    def test_empty_output_committed(self):
        # 空 output 应该被允许（用户可能 commit 空格然后立即删）
        outcome = self.store.log_commit("a", "")
        self.assertEqual(outcome.result, LogResult.COMMITTED)
        # 但不会更新 word_freq / phrase_pair
        self.assertEqual(self.store.stats()["unique_chars"], 0)

    def test_empty_input_raw_committed(self):
        # 极少见：output 有内容但 input_raw 空
        outcome = self.store.log_commit("", "你好")
        self.assertEqual(outcome.result, LogResult.COMMITTED)

    def test_super_long_output(self):
        # 100KB 中文（用户粘贴长文本）
        long_text = "测试" * 25000  # 50000 char
        outcome = self.store.log_commit("input", long_text)
        self.assertEqual(outcome.result, LogResult.COMMITTED)
        # word_freq 应该只有 2 个 unique chars
        self.assertEqual(self.store.stats()["unique_chars"], 2)
        wf = self.store.word_freq_lookup(["测", "试"])
        self.assertEqual(wf["测"], 25000)
        self.assertEqual(wf["试"], 25000)

    def test_whitespace_only_output(self):
        outcome = self.store.log_commit("space", "     ")
        self.assertEqual(outcome.result, LogResult.COMMITTED)


class TestInjectionResistance(BoundaryBase):
    """SQL / 控制字符注入抵抗（sqlite3 参数化应当全防住）。"""

    def test_sql_injection_in_output(self):
        evil = "你好'; DROP TABLE commit_log; --"
        outcome = self.store.log_commit("x", evil)
        self.assertEqual(outcome.result, LogResult.COMMITTED)
        # 表还在
        self.assertEqual(self.store.stats()["total_commits"], 1)

    def test_null_byte_in_output(self):
        # NULL 字节应该不会破坏 SQLite TEXT 字段
        outcome = self.store.log_commit("x", "hello\x00world")
        self.assertEqual(outcome.result, LogResult.COMMITTED)

    def test_app_name_with_quotes_excluded(self):
        # 排除应用名含引号
        self.store.add_excluded_app("Bad'App.exe", "with apostrophe")
        outcome = self.store.log_commit("a", "x", app_name="Bad'App.exe")
        self.assertEqual(outcome.result, LogResult.SKIPPED_APP)


class TestSensitiveBoundary(BoundaryBase):
    """敏感字段检测的边界（避免误杀和漏放）。"""

    def test_phone_number_NOT_rejected(self):
        # 11 位手机号不属于银行卡 (16-19 位) 范围
        outcome = self.store.log_commit("13800138000", "手机号", app_name="A")
        self.assertEqual(outcome.result, LogResult.COMMITTED)

    def test_15_digits_NOT_rejected(self):
        # 15 位数字不到银行卡门槛
        outcome = self.store.log_commit("123456789012345", "x", app_name="A")
        self.assertEqual(outcome.result, LogResult.COMMITTED)

    def test_16_digits_IS_rejected(self):
        outcome = self.store.log_commit("1234567890123456", "x", app_name="A")
        self.assertEqual(outcome.result, LogResult.SKIPPED_SENSITIVE)

    def test_idcard_with_X_rejected(self):
        # 末位 X 的身份证
        outcome = self.store.log_commit("11010119900101123X", "x", app_name="A")
        self.assertEqual(outcome.result, LogResult.SKIPPED_SENSITIVE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
