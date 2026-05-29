"""
Backfill 三模式编排测试。

运行:
    python3 -m unittest tests.sync.test_backfill -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from auth.token_manager import LinkInvalidError, TokenManager  # noqa: E402
from memory import schema  # noqa: E402
from memory.link_store import LinkStore  # noqa: E402
from sync.backfill import run_backfill  # noqa: E402
from sync.sync_client import IngestResponse, RateLimitError, SyncClient  # noqa: E402


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(
            str(Path(self.tmp.name) / "t.db"),
            check_same_thread=False, isolation_level=None,
        )
        schema.init_db(self.conn)
        self.lock = threading.Lock()
        self.link_store = LinkStore(self.conn, self.lock)
        self.link_store.set_link("user-1", "@testuser")
        self.device_id = self.link_store.get_device_link().device_id

        self.tm = unittest.mock.Mock(spec=TokenManager)
        self.tm.is_linked.return_value = True

        self.sc = unittest.mock.Mock(spec=SyncClient)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed_commits(self, n: int):
        for i in range(n):
            self.conn.execute(
                "INSERT INTO commit_log (timestamp, input_raw, output, user_picked) "
                "VALUES (?, ?, ?, ?)",
                (1700000000000 + i * 1000, f"in{i}", f"出{i}", 0),
            )
        self.conn.commit()

    def _seed_aggregates(self, n_words: int, n_phrases: int):
        for i in range(n_words):
            self.conn.execute(
                "INSERT INTO word_freq (word, count, last_used) VALUES (?, ?, ?)",
                (f"字{i}", 100 - i, 1700000000000),
            )
        for i in range(n_phrases):
            self.conn.execute(
                "INSERT INTO phrase_pair (prev, curr, count) VALUES (?, ?, ?)",
                (f"前{i}", f"后{i}", 50 - i),
            )
        self.conn.commit()


class TestModeNone(_Base):

    def test_immediately_completes_without_upload(self):
        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="none",
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.mode, "none")
        self.assertEqual(result.total_uploaded, 0)
        self.sc.start_backfill.assert_not_called()
        # backfill_state 已标完成
        bs = self.link_store.get_backfill_state()
        self.assertTrue(bs.is_completed)
        self.assertEqual(bs.mode, "none")


class TestModeFull(_Base):

    def test_full_uploads_all_commits(self):
        self._seed_commits(3)
        self.sc.start_backfill.return_value = {"backfill_id": "bf-1"}
        self.sc.upload_backfill_chunk.return_value = IngestResponse(
            received=3, deduplicated=0, inserted=3, deletion_notices=[],
        )
        self.sc.complete_backfill.return_value = {"status": "completed"}

        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="full",
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.total_pulled, 3)
        self.assertEqual(result.total_uploaded, 3)

        # 服务端 start 调用了带 total_commits
        self.sc.start_backfill.assert_called_once()
        kwargs = self.sc.start_backfill.call_args.kwargs
        self.assertEqual(kwargs["mode"], "full")
        self.assertEqual(kwargs["total_commits"], 3)

        # chunk 上传 1 次 (3 条 < 500 chunk size)
        self.assertEqual(self.sc.upload_backfill_chunk.call_count, 1)

        # complete 调用
        self.sc.complete_backfill.assert_called_once()

        # backfill_state 完成
        bs = self.link_store.get_backfill_state()
        self.assertTrue(bs.is_completed)

    def test_full_resumes_from_cursor(self):
        """模拟前次回填到 commit_id=2,本次从 3 开始"""
        self._seed_commits(5)
        # 模拟前次进度
        self.link_store.start_backfill("full", total_to_upload=5)
        self.link_store.advance_backfill(last_commit_id=2, uploaded_delta=2)

        self.sc.start_backfill.return_value = {"backfill_id": "bf-2"}
        self.sc.upload_backfill_chunk.return_value = IngestResponse(
            received=3, deduplicated=0, inserted=3, deletion_notices=[],
        )
        self.sc.complete_backfill.return_value = {"status": "completed"}

        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="full",
        )
        # 仅上传 3 条 (4/5/6 — 但只 5 条 seed,所以 3/4/5)
        chunk_call = self.sc.upload_backfill_chunk.call_args
        items = chunk_call.kwargs["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(result.total_pulled, 3)

    def test_full_link_invalid_stops(self):
        self._seed_commits(2)
        self.sc.start_backfill.side_effect = LinkInvalidError("device_revoked")

        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="full",
        )
        self.assertFalse(result.completed)
        self.assertEqual(result.error, "link_invalid")
        self.sc.upload_backfill_chunk.assert_not_called()

    def test_full_rate_limit_records_retry_after(self):
        self._seed_commits(1)
        self.sc.start_backfill.return_value = {"backfill_id": "bf-3"}
        self.sc.upload_backfill_chunk.side_effect = RateLimitError("slow", retry_after=60)

        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="full",
        )
        self.assertFalse(result.completed)
        self.assertEqual(result.error, "rate_limit")
        self.assertEqual(result.rate_limited_for, 60)


class TestModeAggregate(_Base):

    def test_aggregate_uploads_words_and_phrases(self):
        self._seed_aggregates(n_words=3, n_phrases=2)
        self.sc.start_backfill.return_value = {"backfill_id": "bf-agg"}
        self.sc.upload_backfill_chunk.return_value = IngestResponse(
            received=3, deduplicated=0, inserted=3, deletion_notices=[],
        )
        self.sc.complete_backfill.return_value = {"status": "completed"}

        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="aggregate",
        )
        self.assertTrue(result.completed)
        # words 1 chunk + phrases 1 chunk = 2 calls
        self.assertEqual(self.sc.upload_backfill_chunk.call_count, 2)

        # start_backfill 调用带 total_words + total_phrases
        kwargs = self.sc.start_backfill.call_args.kwargs
        self.assertEqual(kwargs["total_words"], 3)
        self.assertEqual(kwargs["total_phrases"], 2)

        # 第一次 chunk kind='words'
        first_call = self.sc.upload_backfill_chunk.call_args_list[0].kwargs
        self.assertEqual(first_call["kind"], "words")
        # 第二次 kind='phrases'
        second_call = self.sc.upload_backfill_chunk.call_args_list[1].kwargs
        self.assertEqual(second_call["kind"], "phrases")

    def test_aggregate_empty_skips_chunks(self):
        """没有 word/phrase 数据时也能正常完成"""
        self.sc.start_backfill.return_value = {"backfill_id": "bf-empty"}
        self.sc.complete_backfill.return_value = {"status": "completed"}

        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="aggregate",
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.total_pulled, 0)
        self.sc.upload_backfill_chunk.assert_not_called()
        self.sc.complete_backfill.assert_called_once()


class TestInvalidMode(_Base):

    def test_invalid_mode_rejected(self):
        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="bogus",
        )
        self.assertFalse(result.completed)
        self.assertEqual(result.error, "invalid_mode")
        self.sc.start_backfill.assert_not_called()


class TestNotLinked(_Base):

    def test_not_linked_skips(self):
        self.tm.is_linked.return_value = False
        result = run_backfill(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc, mode="full",
        )
        self.assertFalse(result.completed)
        self.assertEqual(result.error, "not_linked")


if __name__ == "__main__":
    unittest.main()
