"""
Uploader 测试 — sync_once / heartbeat_once / UploaderDaemon 生命周期。

策略:
- 用真 SQLite + LinkStore (无 mock)
- mock SyncClient (避免真网络)
- mock TokenManager 必要时

运行:
    python3 -m unittest tests.sync.test_uploader -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from auth.token_manager import LinkInvalidError, NotLinkedError, TokenManager  # noqa: E402
from memory import schema  # noqa: E402
from memory.link_store import LinkStore  # noqa: E402
from sync import hasher  # noqa: E402
from sync.sync_client import IngestResponse, RateLimitError, SyncClient  # noqa: E402
from sync.uploader import (  # noqa: E402
    SYNC_BATCH_MAX_SIZE,
    SyncOutcome,
    UploaderDaemon,
    _build_payload,
    _fetch_unprocessed,
    heartbeat_once,
    sync_once,
)


def _seed_commits(conn: sqlite3.Connection, n: int, start_ts: int = 1700000000000) -> None:
    """往 commit_log 插 n 行假数据。"""
    for i in range(n):
        conn.execute(
            "INSERT INTO commit_log (timestamp, input_raw, output, user_picked, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (start_ts + i * 1000, f"input{i}", f"输出{i}", 0, 300),
        )
    conn.commit()


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
        # 模拟链接
        self.link_store.set_link("user-1", "@testuser")
        self.device_id = self.link_store.get_device_link().device_id

        self.tm = unittest.mock.Mock(spec=TokenManager)
        self.tm.is_linked.return_value = True

        self.sc = unittest.mock.Mock(spec=SyncClient)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class TestFetchUnprocessed(_Base):

    def test_returns_rows_after_cursor(self):
        _seed_commits(self.conn, 5)
        rows = _fetch_unprocessed(self.conn, self.lock, after_id=0, limit=100)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0].id, 1)
        self.assertEqual(rows[-1].id, 5)
        self.assertEqual(rows[0].input_raw, "input0")
        self.assertEqual(rows[0].output, "输出0")

    def test_cursor_filters(self):
        _seed_commits(self.conn, 5)
        rows = _fetch_unprocessed(self.conn, self.lock, after_id=2, limit=100)
        self.assertEqual([r.id for r in rows], [3, 4, 5])

    def test_limit_respected(self):
        _seed_commits(self.conn, 10)
        rows = _fetch_unprocessed(self.conn, self.lock, after_id=0, limit=3)
        self.assertEqual(len(rows), 3)

    def test_empty(self):
        rows = _fetch_unprocessed(self.conn, self.lock, after_id=0, limit=10)
        self.assertEqual(rows, [])


class TestBuildPayload(_Base):

    def test_payload_schema(self):
        _seed_commits(self.conn, 1)
        rows = _fetch_unprocessed(self.conn, self.lock, 0, 10)
        payload = _build_payload(rows, self.device_id)
        self.assertEqual(len(payload), 1)
        item = payload[0]
        # 字段白名单 — 必须有这些
        self.assertEqual(item["device_id"], self.device_id)
        self.assertEqual(item["client_ts"], 1700000000000)
        self.assertEqual(item["input_raw"], "input0")
        self.assertEqual(item["output"], "输出0")
        self.assertEqual(item["user_picked"], 0)
        self.assertIn("content_hash", item)
        # content_hash 与 hasher 公式一致
        expected = hasher.hash_commit(self.device_id, 1700000000000, "input0", "输出0")
        self.assertEqual(item["content_hash"], expected)

    def test_optional_fields_present_when_set(self):
        # 插一条带 app_name 的
        self.conn.execute(
            "INSERT INTO commit_log (timestamp, input_raw, output, app_name, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (1700000999000, "test", "测试", "Code.exe", 450),
        )
        self.conn.commit()
        rows = _fetch_unprocessed(self.conn, self.lock, 0, 10)
        payload = _build_payload(rows, self.device_id)
        self.assertEqual(payload[0]["app_name"], "Code.exe")
        self.assertEqual(payload[0]["duration_ms"], 450)

    def test_optional_fields_omitted_when_null(self):
        self.conn.execute(
            "INSERT INTO commit_log (timestamp, input_raw, output) VALUES (?, ?, ?)",
            (1700001000000, "x", "y"),
        )
        self.conn.commit()
        rows = _fetch_unprocessed(self.conn, self.lock, 0, 10)
        payload = _build_payload(rows, self.device_id)
        # duration_ms / app_name 缺失时不应在 payload (服务端 strict schema 会拒空字符串等)
        item = payload[0]
        # duration_ms 默认 NULL 不出现
        self.assertNotIn("duration_ms", item)
        self.assertNotIn("app_name", item)


class TestSyncOnce(_Base):

    def test_not_linked_returns_not_linked(self):
        self.tm.is_linked.return_value = False
        out = sync_once(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc,
        )
        self.assertEqual(out.error, "not_linked")
        self.sc.upload_signals.assert_not_called()

    def test_empty_commit_log_no_call(self):
        out = sync_once(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc,
        )
        self.assertEqual(out.pulled, 0)
        self.assertIsNone(out.error)
        self.sc.upload_signals.assert_not_called()

    def test_successful_sync_advances_cursor(self):
        _seed_commits(self.conn, 3)
        self.sc.upload_signals.return_value = IngestResponse(
            received=3, deduplicated=0, inserted=3, deletion_notices=[],
        )
        out = sync_once(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc,
        )
        self.assertEqual(out.pulled, 3)
        self.assertEqual(out.uploaded, 3)
        self.assertEqual(out.deduplicated, 0)
        self.assertIsNone(out.error)

        cursor = self.link_store.get_sync_cursor()
        self.assertEqual(cursor.last_synced_commit_id, 3)
        self.assertEqual(cursor.total_uploaded, 3)
        self.assertEqual(cursor.pending_count, 0)

    def test_partial_dedup_counted(self):
        _seed_commits(self.conn, 5)
        self.sc.upload_signals.return_value = IngestResponse(
            received=5, deduplicated=2, inserted=3, deletion_notices=[],
        )
        out = sync_once(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc,
        )
        self.assertEqual(out.uploaded, 3)
        self.assertEqual(out.deduplicated, 2)
        # cursor 仍推到最后一条 (即使部分被去重)
        self.assertEqual(self.link_store.get_sync_cursor().last_synced_commit_id, 5)
        # total_uploaded 仅累 inserted
        self.assertEqual(self.link_store.get_sync_cursor().total_uploaded, 3)

    def test_link_invalid_marks_failure_does_not_advance(self):
        _seed_commits(self.conn, 2)
        self.sc.upload_signals.side_effect = LinkInvalidError("device_revoked")
        out = sync_once(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc,
        )
        self.assertEqual(out.error, "link_invalid")
        # cursor 没动
        self.assertEqual(self.link_store.get_sync_cursor().last_synced_commit_id, 0)
        # 错误已写
        self.assertIn("link_invalid", self.link_store.get_sync_cursor().last_error or "")

    def test_rate_limit_records_retry_after(self):
        _seed_commits(self.conn, 1)
        self.sc.upload_signals.side_effect = RateLimitError("too fast", retry_after=42)
        out = sync_once(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc,
        )
        self.assertEqual(out.error, "rate_limit")
        self.assertEqual(out.rate_limited_for, 42)
        # cursor 没动
        self.assertEqual(self.link_store.get_sync_cursor().last_synced_commit_id, 0)

    def test_generic_failure_recorded(self):
        _seed_commits(self.conn, 1)
        self.sc.upload_signals.side_effect = RuntimeError("network reset")
        out = sync_once(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc,
        )
        self.assertEqual(out.error, "upload_error")
        self.assertIn("network reset", self.link_store.get_sync_cursor().last_error or "")

    def test_batch_size_capped(self):
        """commit_log 有 > SYNC_BATCH_MAX_SIZE 行时只取 N 条"""
        _seed_commits(self.conn, SYNC_BATCH_MAX_SIZE + 50)
        self.sc.upload_signals.return_value = IngestResponse(
            received=SYNC_BATCH_MAX_SIZE, deduplicated=0,
            inserted=SYNC_BATCH_MAX_SIZE, deletion_notices=[],
        )
        out = sync_once(
            conn=self.conn, lock=self.lock, link_store=self.link_store,
            token_manager=self.tm, sync_client=self.sc,
        )
        self.assertEqual(out.pulled, SYNC_BATCH_MAX_SIZE)


class TestHeartbeatOnce(_Base):

    def test_returns_server_response(self):
        from sync.sync_client import HeartbeatResponse
        self.sc.heartbeat.return_value = HeartbeatResponse(
            server_total_received=42, deletion_notices=[{"x": 1}], device_status="active",
        )
        r = heartbeat_once(link_store=self.link_store, sync_client=self.sc)
        self.assertIsNotNone(r)
        self.assertEqual(r["server_total_received"], 42)
        self.assertEqual(r["deletion_notices"], [{"x": 1}])

    def test_not_linked_returns_none(self):
        self.link_store.clear_link()
        r = heartbeat_once(link_store=self.link_store, sync_client=self.sc)
        self.assertIsNone(r)
        self.sc.heartbeat.assert_not_called()

    def test_exception_returns_none(self):
        self.sc.heartbeat.side_effect = RuntimeError("net")
        r = heartbeat_once(link_store=self.link_store, sync_client=self.sc)
        self.assertIsNone(r)


class TestUploaderDaemon(_Base):

    def test_start_stop(self):
        """start → stop 优雅退出"""
        self.sc.upload_signals.return_value = IngestResponse(0, 0, 0, [])
        d = UploaderDaemon(
            self.conn, self.lock, self.link_store, self.tm, self.sc,
            sync_interval=999, heartbeat_interval=999,  # 长 interval 避免 race
        )
        d.start()
        self.assertIsNotNone(d._thread)
        time.sleep(0.1)
        d.stop(timeout=3)
        self.assertFalse(d._thread.is_alive() if d._thread else False)

    def test_trigger_sync_wakes_loop(self):
        _seed_commits(self.conn, 2)
        self.sc.upload_signals.return_value = IngestResponse(
            received=2, deduplicated=0, inserted=2, deletion_notices=[],
        )
        d = UploaderDaemon(
            self.conn, self.lock, self.link_store, self.tm, self.sc,
            sync_interval=9999,  # 长 interval,只有 trigger 才动
        )
        d.start()
        time.sleep(0.2)  # 等首次 sync 完成
        d.trigger_sync()
        time.sleep(0.3)
        d.stop(timeout=3)
        # 至少 inserted 2
        self.assertGreaterEqual(self.link_store.get_sync_cursor().total_uploaded, 2)

    def test_link_invalid_breaks_loop(self):
        _seed_commits(self.conn, 1)
        self.sc.upload_signals.side_effect = LinkInvalidError("revoked")
        d = UploaderDaemon(
            self.conn, self.lock, self.link_store, self.tm, self.sc,
            sync_interval=9999,
        )
        d.start()
        time.sleep(0.3)  # 等首次 sync 出错
        # 线程应已自己退出 (不需要外部 stop)
        if d._thread:
            d._thread.join(timeout=3)
            self.assertFalse(d._thread.is_alive())
        d.stop(timeout=1)


if __name__ == "__main__":
    unittest.main()
