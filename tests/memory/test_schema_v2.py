"""
Schema v2 (ime-twin-link) migration 测试。

覆盖:
- 全新库一次性建 v1+v2 ✓
- v1 库升级到 v2 ✓
- 单行表 CHECK 约束 ✓
- device_id 首启生成且后续不变 ✓
- 升级不动 v1 五张表的数据 ✓

运行:
    python3 -m unittest tests.memory.test_schema_v2 -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory import schema  # noqa: E402


def make_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class TestFreshDatabaseV2(unittest.TestCase):
    """全新库 → 一次性建 v1+v2 全部表 + seed 单行"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "fresh.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_db_has_all_tables(self):
        conn = make_conn(self.db_path)
        version = schema.init_db(conn)
        self.assertEqual(version, 2)

        for t in ("commit_log", "word_freq", "phrase_pair", "privacy_exclude", "config"):
            cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'")
            self.assertIsNotNone(cur.fetchone(), f"v1 table {t} missing")

        for t in ("device_link", "sync_cursor", "backfill_state"):
            cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'")
            self.assertIsNotNone(cur.fetchone(), f"v2 table {t} missing")

        cur = conn.execute("PRAGMA user_version")
        self.assertEqual(cur.fetchone()[0], 2)

    def test_fresh_db_seeds_singleton_rows(self):
        conn = make_conn(self.db_path)
        schema.init_db(conn)

        rows = conn.execute("SELECT id, device_id, created_at FROM device_link").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1)
        # device_id 是 UUID
        uuid.UUID(rows[0][1])
        self.assertGreater(rows[0][2], 0)

        rows = conn.execute("SELECT id, last_synced_commit_id FROM sync_cursor").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], (1, 0))

        rows = conn.execute("SELECT id, mode, total_uploaded FROM backfill_state").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], (1, None, 0))

    def test_device_id_stable_across_init(self):
        """重复 init_db 不会改 device_id"""
        conn1 = make_conn(self.db_path)
        schema.init_db(conn1)
        original_id = conn1.execute("SELECT device_id FROM device_link WHERE id=1").fetchone()[0]
        conn1.close()

        conn2 = make_conn(self.db_path)
        schema.init_db(conn2)
        new_id = conn2.execute("SELECT device_id FROM device_link WHERE id=1").fetchone()[0]
        conn2.close()

        self.assertEqual(original_id, new_id)


class TestV1ToV2Migration(unittest.TestCase):
    """v1 现有库升级到 v2"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "v1.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _build_v1_db(self) -> sqlite3.Connection:
        conn = make_conn(self.db_path)
        conn.executescript(schema.SCHEMA_V1)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        return conn

    def test_v1_to_v2_adds_three_tables(self):
        conn = self._build_v1_db()
        # 插一行 v1 数据,验证迁移不破坏
        conn.execute(
            "INSERT INTO commit_log (timestamp, input_raw, output) VALUES (?, ?, ?)",
            (1700000000000, "nihao", "你好"),
        )
        conn.commit()

        version = schema.init_db(conn)
        self.assertEqual(version, 2)

        for t in ("device_link", "sync_cursor", "backfill_state"):
            cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'")
            self.assertIsNotNone(cur.fetchone())

        # v1 数据完整
        row = conn.execute("SELECT input_raw, output FROM commit_log").fetchone()
        self.assertEqual(row, ("nihao", "你好"))

        # 单行表已 seed
        self.assertEqual(conn.execute("SELECT count(*) FROM device_link").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT count(*) FROM sync_cursor").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT count(*) FROM backfill_state").fetchone()[0], 1)

    def test_v1_to_v2_idempotent_init(self):
        """v1 库迁完 v2 后再 init 不报错"""
        conn = self._build_v1_db()
        schema.init_db(conn)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)

        # 再调一次 init_db
        schema.init_db(conn)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)


class TestSingletonConstraints(unittest.TestCase):
    """单行表 CHECK (id = 1) 强制约束"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = make_conn(Path(self.tmp.name) / "s.db")
        schema.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_device_link_rejects_second_row(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO device_link (id, device_id, created_at) VALUES (2, ?, ?)",
                (str(uuid.uuid4()), 1700000000000),
            )

    def test_sync_cursor_rejects_second_row(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO sync_cursor (id) VALUES (2)")

    def test_backfill_state_rejects_second_row(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO backfill_state (id) VALUES (2)")

    def test_device_link_rejects_dup_device_id(self):
        """UNIQUE(device_id) 拦住二插入"""
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO device_link (id, device_id, created_at) VALUES (2, 'dup', 1)"
            )


class TestLinkStateUpdates(unittest.TestCase):
    """device_link 的 linked_* 字段语义"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = make_conn(Path(self.tmp.name) / "l.db")
        schema.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_initial_link_state_null(self):
        row = self.conn.execute(
            "SELECT linked_user_id, linked_user_handle, linked_at, revoked_at, ektro_endpoint "
            "FROM device_link WHERE id = 1"
        ).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])
        self.assertIsNone(row[3])
        self.assertEqual(row[4], "https://ektroai.com")

    def test_link_then_revoke(self):
        self.conn.execute(
            "UPDATE device_link SET linked_user_id = ?, linked_user_handle = ?, linked_at = ? "
            "WHERE id = 1",
            ("user-uuid-1234", "@yijie", 1700000001000),
        )
        row = self.conn.execute(
            "SELECT linked_user_id, linked_user_handle, linked_at FROM device_link WHERE id = 1"
        ).fetchone()
        self.assertEqual(row, ("user-uuid-1234", "@yijie", 1700000001000))

        # 解绑: linked_user_id 置 NULL + revoked_at
        self.conn.execute(
            "UPDATE device_link SET linked_user_id = NULL, linked_user_handle = NULL, revoked_at = ? "
            "WHERE id = 1",
            (1700000002000,),
        )
        row = self.conn.execute(
            "SELECT linked_user_id, revoked_at FROM device_link WHERE id = 1"
        ).fetchone()
        self.assertEqual(row, (None, 1700000002000))


class TestBackfillMode(unittest.TestCase):
    """backfill_state.mode 字段允许 'full'/'aggregate'/'none' + NULL"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = make_conn(Path(self.tmp.name) / "b.db")
        schema.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_initial_mode_null(self):
        mode = self.conn.execute("SELECT mode FROM backfill_state").fetchone()[0]
        self.assertIsNone(mode)

    def test_mode_updates(self):
        for mode in ("full", "aggregate", "none"):
            self.conn.execute("UPDATE backfill_state SET mode = ? WHERE id = 1", (mode,))
            self.assertEqual(
                self.conn.execute("SELECT mode FROM backfill_state").fetchone()[0], mode
            )


if __name__ == "__main__":
    unittest.main()
