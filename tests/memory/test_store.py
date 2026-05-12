"""
EktroMemoryStore 单元测试。

运行：
    cd E:\\CLAUDE\\EKTRO输入法
    set PYTHONPATH=src
    python -m pytest tests/memory/ -v

或：
    python -m unittest tests.memory.test_store -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.store import EktroMemoryStore, LogResult  # noqa: E402
from memory.schema import CURRENT_SCHEMA_VERSION  # noqa: E402


class StoreBaseTest(unittest.TestCase):
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


class TestSchema(StoreBaseTest):
    def test_init_creates_tables(self):
        stats = self.store.stats()
        self.assertEqual(stats["total_commits"], 0)

    def test_idempotent_init(self):
        # 第二次打开同一 DB 不报错
        self.store.close()
        s2 = EktroMemoryStore(self.tmp.name)
        s2.close()
        self.store = EktroMemoryStore(self.tmp.name)  # 重新打开供 tearDown 关

    def test_schema_version(self):
        cur = self.store._conn.execute("PRAGMA user_version")
        self.assertEqual(cur.fetchone()[0], CURRENT_SCHEMA_VERSION)

    def test_default_config_seeded(self):
        cfg = self.store.all_config()
        self.assertIn("enable_rerank", cfg)
        self.assertIn("predictor_delay_ms", cfg)
        self.assertEqual(cfg["theme"], "auto")


class TestLogCommit(StoreBaseTest):
    def test_basic_commit(self):
        outcome = self.store.log_commit("nihao", "你好", app_name="Code.exe")
        self.assertEqual(outcome.result, LogResult.COMMITTED)
        self.assertIsInstance(outcome.row_id, int)
        recent = self.store.recent_outputs(limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].output, "你好")
        self.assertEqual(recent[0].app_name, "Code.exe")

    def test_word_freq_updated(self):
        self.store.log_commit("nihao", "你好", app_name="A")
        self.store.log_commit("nihaoshijie", "你好世界", app_name="A")
        wf = self.store.word_freq_lookup(["你", "好", "世", "界", "不存在的字"])
        self.assertEqual(wf["你"], 2)
        self.assertEqual(wf["好"], 2)
        self.assertEqual(wf["世"], 1)
        self.assertEqual(wf["界"], 1)
        self.assertEqual(wf["不存在的字"], 0)

    def test_phrase_pair_updated(self):
        self.store.log_commit("nihao", "你好")
        pairs = self.store.phrase_pair_lookup("你")
        self.assertEqual(pairs, [("好", 1)])
        self.store.log_commit("nihao", "你好")  # 第二次
        pairs = self.store.phrase_pair_lookup("你")
        self.assertEqual(pairs, [("好", 2)])

    def test_user_picked_flag(self):
        o1 = self.store.log_commit("a", "啊", user_picked=False)
        o2 = self.store.log_commit("b", "吧", user_picked=True)
        recent = self.store.recent_outputs(limit=10)
        recents = {r.id: r.user_picked for r in recent}
        self.assertFalse(recents[o1.row_id])
        self.assertTrue(recents[o2.row_id])


class TestPrivacy(StoreBaseTest):
    """
    D-009 P0.1 后的隐私拦截行为：
    - is_password_field=True 是唯一权威 (来自 TSF IS_PASSWORD)
    - input_raw 检测银行卡/身份证/email (不是 output)
    - 不再检测 output 上的"密码样式"——那个正则在中文 commit 上永远无效
    """

    def test_password_field_rejected(self):
        outcome = self.store.log_commit("pwd", "MySecret123", is_password_field=True)
        self.assertEqual(outcome.result, LogResult.SKIPPED_PASSWORD)
        self.assertIsNone(outcome.row_id)
        self.assertEqual(self.store.stats()["total_commits"], 0)

    def test_bankcard_in_input_raw_rejected(self):
        # input_raw 含连续 16-19 位数字 → 银行卡
        outcome = self.store.log_commit("6222021234567890123", "卡号", app_name="A")
        self.assertEqual(outcome.result, LogResult.SKIPPED_SENSITIVE)

    def test_idcard_in_input_raw_rejected(self):
        outcome = self.store.log_commit("110101199001011234", "身份证", app_name="A")
        self.assertEqual(outcome.result, LogResult.SKIPPED_SENSITIVE)

    def test_email_in_input_raw_rejected(self):
        outcome = self.store.log_commit("test@example.com", "邮箱", app_name="A")
        self.assertEqual(outcome.result, LogResult.SKIPPED_SENSITIVE)

    def test_chinese_output_with_numbers_NOT_rejected(self):
        """关键回归: 用户输入 'V60 滤杯' / 编号 / 价格 等不该被误杀。"""
        outcome = self.store.log_commit("v60lvbei", "V60 滤杯", app_name="A")
        self.assertEqual(outcome.result, LogResult.COMMITTED)

    def test_excluded_app_rejected(self):
        self.store.add_excluded_app("BankApp.exe", "财务软件")
        outcome = self.store.log_commit("nihao", "你好", app_name="BankApp.exe")
        self.assertEqual(outcome.result, LogResult.SKIPPED_APP)
        # 其他应用正常
        outcome2 = self.store.log_commit("nihao", "你好", app_name="Notepad.exe")
        self.assertEqual(outcome2.result, LogResult.COMMITTED)

    def test_excluded_app_list(self):
        self.store.add_excluded_app("App1.exe", "r1")
        self.store.add_excluded_app("App2.exe", "r2")
        lst = self.store.list_excluded_apps()
        names = {row[0] for row in lst}
        self.assertEqual(names, {"App1.exe", "App2.exe"})

    def test_remove_excluded(self):
        self.store.add_excluded_app("App1.exe")
        self.store.remove_excluded_app("App1.exe")
        self.assertEqual(self.store.list_excluded_apps(), [])

    def test_normal_chinese_not_rejected(self):
        outcome = self.store.log_commit("nihao", "你好世界，今天天气真好")
        self.assertEqual(outcome.result, LogResult.COMMITTED)

    def test_is_committed_helper(self):
        outcome = self.store.log_commit("nihao", "你好")
        self.assertTrue(outcome.result.is_committed)
        self.assertFalse(outcome.result.is_skipped)


class TestQueries(StoreBaseTest):
    def test_recent_order(self):
        now = int(time.time() * 1000)
        self.store.log_commit("a", "甲", timestamp=now - 3000)
        self.store.log_commit("b", "乙", timestamp=now - 2000)
        self.store.log_commit("c", "丙", timestamp=now - 1000)
        recent = self.store.recent_outputs(limit=10)
        self.assertEqual([r.output for r in recent], ["丙", "乙", "甲"])

    def test_recent_limit(self):
        for i in range(20):
            self.store.log_commit(f"i{i}", f"字{i}")
        self.assertEqual(len(self.store.recent_outputs(limit=5)), 5)

    def test_recent_since(self):
        cutoff = int(time.time() * 1000)
        self.store.log_commit("old", "旧", timestamp=cutoff - 1000)
        self.store.log_commit("new", "新", timestamp=cutoff + 1000)
        recent = self.store.recent_outputs(limit=10, since_ms=cutoff)
        self.assertEqual([r.output for r in recent], ["新"])

    def test_top_words(self):
        for _ in range(5):
            self.store.log_commit("nihao", "你好")
        for _ in range(2):
            self.store.log_commit("xie", "谢")
        top = self.store.top_words(limit=10)
        # 你=5, 好=5, 谢=2
        d = {w.word: w.count for w in top}
        self.assertEqual(d["你"], 5)
        self.assertEqual(d["好"], 5)
        self.assertEqual(d["谢"], 2)


class TestExportImport(StoreBaseTest):
    def test_export_structure(self):
        self.store.log_commit("nihao", "你好", app_name="Test")
        export = self.store.export_all()
        self.assertIn("commits", export)
        self.assertIn("word_freq", export)
        self.assertIn("phrase_pair", export)
        self.assertIn("config", export)
        self.assertIn("privacy_exclude", export)
        self.assertEqual(len(export["commits"]), 1)
        self.assertEqual(export["schema_version"], CURRENT_SCHEMA_VERSION)

    def test_clear_requires_confirm(self):
        self.store.log_commit("a", "甲")
        with self.assertRaises(ValueError):
            self.store.clear_all()
        # 数据还在
        self.assertEqual(self.store.stats()["total_commits"], 1)

    def test_clear_with_confirm(self):
        self.store.log_commit("a", "甲")
        self.store.add_excluded_app("X.exe")
        self.store.clear_all(confirm=True)
        self.assertEqual(self.store.stats()["total_commits"], 0)
        # 隐私设置保留
        self.assertEqual(len(self.store.list_excluded_apps()), 1)

    def test_delete_range(self):
        now = int(time.time() * 1000)
        self.store.log_commit("a", "甲", timestamp=now - 5000)
        self.store.log_commit("b", "乙", timestamp=now - 3000)
        self.store.log_commit("c", "丙", timestamp=now - 1000)
        deleted = self.store.delete_range(now - 4000, now - 2000)
        self.assertEqual(deleted, 1)
        recent = self.store.recent_outputs(limit=10)
        self.assertEqual({r.output for r in recent}, {"甲", "丙"})


class TestConfig(StoreBaseTest):
    def test_get_default(self):
        self.assertEqual(self.store.get_config("theme"), "auto")

    def test_set_and_get(self):
        self.store.set_config("theme", "dark")
        self.assertEqual(self.store.get_config("theme"), "dark")

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get_config("nonexistent_key"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
