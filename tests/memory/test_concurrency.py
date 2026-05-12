"""
SQLite 并发读写测试 (D-009 P0.6)。

store.py 标榜"线程安全 + WAL + check_same_thread=False"。
Swarm 验收发现这只有单线程顺序测试。

本测试: 多写线程 + 多读线程并发跑，验证：
1. commit_log 行数 == 总写入数（无丢失）
2. word_freq 总和 == 期望（无脏写）
3. phrase_pair 一致
4. 全程无异常
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.store import EktroMemoryStore, LogResult  # noqa: E402


class TestStoreConcurrency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self.store = EktroMemoryStore(self.tmp.name)
        self.errors: list[Exception] = []
        self.error_lock = threading.Lock()

    def tearDown(self):
        self.store.close()
        for ext in ("", "-wal", "-shm"):
            p = self.tmp.name + ext
            if os.path.exists(p):
                os.unlink(p)

    def _record_error(self, e: Exception):
        with self.error_lock:
            self.errors.append(e)

    def test_4_writers_2_readers_1000_each(self):
        """4 个写线程 + 2 个读线程，每线程 1000 次操作。"""
        N_WRITES_PER_THREAD = 1000
        N_WRITERS = 4
        N_READERS = 2
        N_READS_PER_THREAD = 1000

        def writer(thread_id: int):
            try:
                for i in range(N_WRITES_PER_THREAD):
                    # D-011 P0.5.4: 用"啊你" 2 字 commit，让 phrase_pair 也被验证
                    # （单字 commit 走 _update_phrase_pairs len<2 早返，等于无测）
                    outcome = self.store.log_commit(
                        f"in{thread_id}_{i}",
                        "啊你",
                        app_name=f"Thread{thread_id}",
                    )
                    if outcome.result != LogResult.COMMITTED:
                        raise RuntimeError(f"unexpected {outcome.result}")
            except Exception as e:
                self._record_error(e)

        def reader(thread_id: int):
            try:
                for _ in range(N_READS_PER_THREAD):
                    # 三种读
                    self.store.recent_outputs(limit=10)
                    self.store.word_freq_lookup(["啊", "你", "好"])
                    self.store.top_words(limit=5)
            except Exception as e:
                self._record_error(e)

        threads = []
        for i in range(N_WRITERS):
            t = threading.Thread(target=writer, args=(i,), name=f"W{i}")
            threads.append(t)
        for i in range(N_READERS):
            t = threading.Thread(target=reader, args=(i,), name=f"R{i}")
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            self.assertFalse(t.is_alive(), f"thread {t.name} hung")

        # 验证零异常
        self.assertEqual(self.errors, [], f"concurrency errors: {self.errors[:3]}")

        # 验证 commit_log 总数
        expected_commits = N_WRITERS * N_WRITES_PER_THREAD
        self.assertEqual(
            self.store.stats()["total_commits"], expected_commits,
            "commit_log 行数不等于写入数 → 有丢失",
        )

        # 验证 word_freq("啊") 和 word_freq("你") 都等于总写入数
        wf = self.store.word_freq_lookup(["啊", "你"])
        self.assertEqual(
            wf["啊"], expected_commits,
            f"word_freq 脏写：期望 {expected_commits} 实际 {wf['啊']}",
        )
        self.assertEqual(
            wf["你"], expected_commits,
            f"word_freq[你] 脏写：期望 {expected_commits} 实际 {wf['你']}",
        )

        # D-011 P0.5.4: 验证 phrase_pair (啊→你) count == expected_commits
        # 之前 commit "啊" 单字导致 phrase_pair 表完全空，测试形同虚设。
        pairs = self.store.phrase_pair_lookup("啊")
        pair_count = dict(pairs).get("你", 0)
        self.assertEqual(
            pair_count, expected_commits,
            f"phrase_pair[啊→你] 脏写：期望 {expected_commits} 实际 {pair_count}",
        )


class TestStoreWriterReaderInterleave(unittest.TestCase):
    """单写线程 + 多读线程，验证 WAL 模式下读不阻塞写。"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self.store = EktroMemoryStore(self.tmp.name)
        # 先放一些数据让读有内容
        for i in range(100):
            self.store.log_commit(f"in{i}", "你好")

    def tearDown(self):
        self.store.close()
        for ext in ("", "-wal", "-shm"):
            p = self.tmp.name + ext
            if os.path.exists(p):
                os.unlink(p)

    def test_continuous_read_during_writes(self):
        stop = threading.Event()
        errors: list[Exception] = []

        def writer():
            try:
                i = 0
                while not stop.is_set() and i < 500:
                    self.store.log_commit(f"x{i}", "啊")
                    i += 1
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    r = self.store.recent_outputs(limit=5)
                    if r:
                        # 每条记录字段应该完整
                        for rec in r:
                            assert rec.output is not None
            except Exception as e:
                errors.append(e)

        wt = threading.Thread(target=writer, daemon=True)
        rt = threading.Thread(target=reader, daemon=True)
        wt.start()
        rt.start()

        wt.join(timeout=30)
        stop.set()
        rt.join(timeout=5)

        self.assertEqual(errors, [], f"interleave errors: {errors[:3]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
