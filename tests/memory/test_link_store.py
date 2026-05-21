"""
LinkStore — schema v2 三表 CRUD 测试。

运行:
    python3 -m unittest tests.memory.test_link_store -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory import schema  # noqa: E402
from memory.link_store import LinkStore  # noqa: E402


def make_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = make_conn(Path(self.tmp.name) / "t.db")
        schema.init_db(self.conn)
        self.store = LinkStore(self.conn, threading.Lock())

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class TestDeviceLinkReads(_Base):
    def test_initial_state_seeded(self):
        link = self.store.get_device_link()
        uuid.UUID(link.device_id)  # 验证 UUID 格式
        self.assertEqual(link.ektro_endpoint, "https://ektroai.com")
        self.assertFalse(link.is_linked)
        self.assertIsNone(link.linked_user_id)
        self.assertIsNone(link.revoked_at)

    def test_device_id_stable(self):
        id1 = self.store.get_device_link().device_id
        id2 = self.store.get_device_link().device_id
        self.assertEqual(id1, id2)


class TestDeviceLinkWrites(_Base):
    def test_set_label(self):
        self.store.set_device_label("yijie-mbp")
        self.assertEqual(self.store.get_device_link().device_label, "yijie-mbp")

    def test_set_link_then_clear(self):
        # 链接
        self.store.set_link("user-uuid-1234", "@yijie")
        link = self.store.get_device_link()
        self.assertTrue(link.is_linked)
        self.assertEqual(link.linked_user_id, "user-uuid-1234")
        self.assertEqual(link.linked_user_handle, "@yijie")
        self.assertIsNotNone(link.linked_at)
        self.assertIsNone(link.revoked_at)

        # 解绑
        self.store.clear_link()
        link2 = self.store.get_device_link()
        self.assertFalse(link2.is_linked)
        self.assertIsNone(link2.linked_user_id)
        self.assertIsNone(link2.linked_user_handle)
        self.assertIsNotNone(link2.revoked_at)

    def test_set_endpoint(self):
        self.store.set_ektro_endpoint("https://staging.ektroai.com")
        self.assertEqual(self.store.get_device_link().ektro_endpoint, "https://staging.ektroai.com")


class TestSyncCursor(_Base):
    def test_initial_zero(self):
        c = self.store.get_sync_cursor()
        self.assertEqual(c.last_synced_commit_id, 0)
        self.assertEqual(c.total_uploaded, 0)
        self.assertEqual(c.pending_count, 0)
        self.assertIsNone(c.last_sync_at)
        self.assertIsNone(c.last_error)

    def test_advance(self):
        self.store.advance_cursor(100, uploaded_delta=50)
        c = self.store.get_sync_cursor()
        self.assertEqual(c.last_synced_commit_id, 100)
        self.assertEqual(c.total_uploaded, 50)
        self.assertIsNotNone(c.last_sync_at)
        self.assertIsNone(c.last_error)

        # 第二次 advance 累加
        self.store.advance_cursor(150, uploaded_delta=30)
        c2 = self.store.get_sync_cursor()
        self.assertEqual(c2.last_synced_commit_id, 150)
        self.assertEqual(c2.total_uploaded, 80)

    def test_record_failure_does_not_advance(self):
        self.store.advance_cursor(50, uploaded_delta=20)
        self.store.record_sync_failure("network timeout")
        c = self.store.get_sync_cursor()
        # cursor 不动
        self.assertEqual(c.last_synced_commit_id, 50)
        self.assertEqual(c.total_uploaded, 20)
        # 但 error 记录
        self.assertEqual(c.last_error, "network timeout")

    def test_failure_then_success_clears_error(self):
        self.store.record_sync_failure("temp fail")
        self.assertEqual(self.store.get_sync_cursor().last_error, "temp fail")

        self.store.advance_cursor(10, uploaded_delta=5)
        self.assertIsNone(self.store.get_sync_cursor().last_error)

    def test_set_pending_count(self):
        self.store.set_pending_count(42)
        self.assertEqual(self.store.get_sync_cursor().pending_count, 42)

    def test_failure_truncates_long_error(self):
        long_err = "x" * 1000
        self.store.record_sync_failure(long_err)
        c = self.store.get_sync_cursor()
        self.assertEqual(len(c.last_error or ""), 500)


class TestBackfillState(_Base):
    def test_initial_null(self):
        b = self.store.get_backfill_state()
        self.assertIsNone(b.mode)
        self.assertIsNone(b.started_at)
        self.assertIsNone(b.completed_at)
        self.assertEqual(b.total_uploaded, 0)
        self.assertFalse(b.is_completed)

    def test_start_full(self):
        self.store.start_backfill("full", total_to_upload=10000)
        b = self.store.get_backfill_state()
        self.assertEqual(b.mode, "full")
        self.assertEqual(b.total_to_upload, 10000)
        self.assertIsNotNone(b.started_at)
        self.assertIsNone(b.completed_at)  # full 不即时完成

    def test_start_none_immediately_completes(self):
        """mode='none' 即时标记 completed (跳过回填)"""
        self.store.start_backfill("none")
        b = self.store.get_backfill_state()
        self.assertEqual(b.mode, "none")
        self.assertIsNotNone(b.completed_at)
        self.assertTrue(b.is_completed)

    def test_start_aggregate(self):
        self.store.start_backfill("aggregate", total_to_upload=500)
        b = self.store.get_backfill_state()
        self.assertEqual(b.mode, "aggregate")
        self.assertIsNone(b.completed_at)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.store.start_backfill("bogus")

    def test_advance_then_complete(self):
        self.store.start_backfill("full", total_to_upload=100)
        self.store.advance_backfill(last_commit_id=50, uploaded_delta=50)
        b1 = self.store.get_backfill_state()
        self.assertEqual(b1.last_uploaded_commit_id, 50)
        self.assertEqual(b1.total_uploaded, 50)

        self.store.advance_backfill(last_commit_id=100, uploaded_delta=50)
        b2 = self.store.get_backfill_state()
        self.assertEqual(b2.total_uploaded, 100)

        self.store.complete_backfill()
        b3 = self.store.get_backfill_state()
        self.assertTrue(b3.is_completed)
        self.assertIsNone(b3.error)

    def test_record_error(self):
        self.store.start_backfill("full", 100)
        self.store.record_backfill_error("network down")
        b = self.store.get_backfill_state()
        self.assertEqual(b.error, "network down")
        self.assertFalse(b.is_completed)  # 错误不算完成

    def test_complete_clears_error(self):
        self.store.start_backfill("full", 100)
        self.store.record_backfill_error("transient")
        self.store.complete_backfill()
        self.assertIsNone(self.store.get_backfill_state().error)


if __name__ == "__main__":
    unittest.main()
