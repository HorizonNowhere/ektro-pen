"""
EktroMemoryStore — Python 参考实现。

职责（来自 design.md §3, §5, §8）:
- 每次 commit 异步落库（log_commit）
- 提供 rerank filter 用的查询（recent_outputs, word_freq, phrase_pair）
- 隐私豁免：检测密码框 / 私密应用并跳过落库
- 用户面板的 backing store（CLI 工具 read this）

线程安全：所有方法 acquire single-writer lock。
SQLite 单连接 + check_same_thread=False 给后台 worker 用。
"""
from __future__ import annotations

import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from . import schema

# 加载 common.logging
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.logging import get_logger  # noqa: E402

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────
# 隐私拦截（D-009 P0.1 重写）
#
# 原设计错误：把正则用在 output（中文 commit）上 → 永远命中不到。
# 新设计：
#   - is_password_field=True 是唯一权威（来自 TSF IS_PASSWORD scope）
#   - input_raw 检测银行卡/身份证/email（这是用户敲的，可能含数字串）
#   - output 不再做正则检测（中文 commit 上正则毫无意义）
# ──────────────────────────────────────────────────────────────────────

_RE_BANKCARD = re.compile(r"\b\d{16,19}\b")       # 16-19 位连续数字
_RE_IDCARD_CN = re.compile(r"\b\d{17}[\dXx]\b")   # 中国身份证 18 位 (末位可 X)
_RE_EMAIL = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")


class LogResult(Enum):
    """log_commit 的返回类型，调用方能区分"成功 / 被拦截 / DB 故障"。"""
    COMMITTED = "committed"             # 成功插入，row_id 在 .row_id 字段
    SKIPPED_PASSWORD = "skipped_pwd"    # 密码框
    SKIPPED_SENSITIVE = "skipped_sens"  # 命中银行卡/身份证/email
    SKIPPED_APP = "skipped_app"         # 在 privacy_exclude 名单
    DB_ERROR = "db_error"               # SQLite 写失败（日志已记）

    @property
    def is_committed(self) -> bool:
        return self == LogResult.COMMITTED

    @property
    def is_skipped(self) -> bool:
        return self in (LogResult.SKIPPED_PASSWORD,
                        LogResult.SKIPPED_SENSITIVE,
                        LogResult.SKIPPED_APP)


@dataclass(frozen=True)
class LogOutcome:
    """log_commit 详细结果。简单场景用 .result 即可，详细场景看 .row_id / .error_detail。"""
    result: LogResult
    row_id: Optional[int] = None
    error_detail: Optional[str] = None


@dataclass(frozen=True)
class CommitRecord:
    id: int
    timestamp: int
    input_raw: str
    output: str
    app_name: Optional[str]
    context_id: Optional[int]
    user_picked: bool
    duration_ms: Optional[int]


@dataclass(frozen=True)
class WordFreq:
    word: str
    count: int
    last_used: int


class EktroMemoryStore:
    """
    线程安全的 SQLite-backed 记忆存储。

    使用：
        store = EktroMemoryStore("/path/to/user.db")
        store.log_commit("nihao", "你好", app="Code.exe")
        recent = store.recent_outputs(limit=5)
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; 我们自己管事务
            )
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._lock = threading.Lock()
            with self._lock:
                schema.init_db(self._conn)
                schema.seed_default_config(self._conn)
            logger.info("EktroMemoryStore opened at %s", self.db_path)
        except Exception:
            # 构造失败资源泄漏防护 (D-009 P0)
            logger.exception("Failed to initialize EktroMemoryStore")
            try:
                self._conn.close()  # type: ignore[has-type]
            except Exception:
                pass
            raise

    # ────────── 写入 API ──────────

    def is_sensitive_input(self, input_raw: str) -> bool:
        """
        检测 input_raw (拼音原文，可能含数字串) 是否含敏感字段。

        D-009 P0.1 修订：原 is_sensitive 用在 output (中文) 上无效。
        现在仅在 input_raw 上检测——只在 input_raw 看起来像"用户粘贴了银行卡号"
        或类似时才拦截。
        """
        if not input_raw:
            return False
        if _RE_BANKCARD.search(input_raw):
            return True
        if _RE_IDCARD_CN.search(input_raw):
            return True
        if _RE_EMAIL.search(input_raw):
            return True
        return False

    # 保持旧名以兼容现有调用方；标记为 deprecated。
    is_sensitive = is_sensitive_input

    def is_excluded_app(self, app_name: Optional[str]) -> bool:
        """检查 app_name 是否在 privacy_exclude 列表中。"""
        if not app_name:
            return False
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM privacy_exclude WHERE pattern = ? LIMIT 1", (app_name,)
            )
            return cur.fetchone() is not None

    def log_commit(
        self,
        input_raw: str,
        output: str,
        *,
        app_name: Optional[str] = None,
        context_id: Optional[int] = None,
        user_picked: bool = False,
        duration_ms: Optional[int] = None,
        is_password_field: bool = False,
        timestamp: Optional[int] = None,
    ) -> LogOutcome:
        """
        记录一次 commit。返回 LogOutcome (D-009 P0.4)，调用方能区分:
        - COMMITTED (.row_id 有值)
        - SKIPPED_PASSWORD / SKIPPED_SENSITIVE / SKIPPED_APP (被拦)
        - DB_ERROR (DB 写失败，.error_detail 含原因)

        触发跳过的条件：
        - is_password_field=True (来自 TSF IS_PASSWORD scope，权威信号)
        - input_raw 命中银行卡/身份证/email 正则
        - app_name 在 privacy_exclude 列表中
        """
        if is_password_field:
            logger.debug("log_commit skipped: password field [app=%r]", app_name)
            return LogOutcome(LogResult.SKIPPED_PASSWORD)
        if self.is_sensitive_input(input_raw):
            logger.debug("log_commit skipped: sensitive input pattern [app=%r]", app_name)
            return LogOutcome(LogResult.SKIPPED_SENSITIVE)
        if self.is_excluded_app(app_name):
            logger.debug("log_commit skipped: excluded app [app=%r]", app_name)
            return LogOutcome(LogResult.SKIPPED_APP)

        ts = timestamp if timestamp is not None else int(time.time() * 1000)

        try:
            with self._lock:
                cur = self._conn.execute(
                    """INSERT INTO commit_log
                       (timestamp, input_raw, output, app_name, context_id, user_picked, duration_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ts, input_raw, output, app_name, context_id, 1 if user_picked else 0, duration_ms),
                )
                commit_id = cur.lastrowid
                self._update_word_freq(output, ts)
                self._update_phrase_pairs(output)
            return LogOutcome(LogResult.COMMITTED, row_id=commit_id)
        except sqlite3.Error as e:
            logger.exception("log_commit DB error")
            return LogOutcome(LogResult.DB_ERROR, error_detail=repr(e))

    def _update_word_freq(self, output: str, ts: int) -> None:
        """
        把 output 按字粒度拆分，每个字 freq+1 (D-011 P0.5.2: executemany 批量)。
        长 commit (10+ 字符) 不再持锁 N 次单条 INSERT。
        """
        if not output:
            return
        self._conn.executemany(
            """INSERT INTO word_freq (word, count, last_used)
               VALUES (?, 1, ?)
               ON CONFLICT(word) DO UPDATE SET
                   count = count + 1,
                   last_used = excluded.last_used""",
            [(ch, ts) for ch in output],
        )

    def _update_phrase_pairs(self, output: str) -> None:
        """
        字粒度二元组：(prev_char, curr_char) → count+1。
        D-011 P0.5.2: executemany 批量。
        """
        if len(output) < 2:
            return
        self._conn.executemany(
            """INSERT INTO phrase_pair (prev, curr, count)
               VALUES (?, ?, 1)
               ON CONFLICT(prev, curr) DO UPDATE SET count = count + 1""",
            list(zip(output, output[1:])),
        )

    # ────────── 读取 API（rerank filter 用）──────────

    def recent_outputs(self, limit: int = 20, since_ms: Optional[int] = None) -> List[CommitRecord]:
        """最近 N 条 commit（design.md §3：rerank 上下文）。"""
        with self._lock:
            if since_ms is not None:
                cur = self._conn.execute(
                    """SELECT id, timestamp, input_raw, output, app_name, context_id, user_picked, duration_ms
                       FROM commit_log WHERE timestamp >= ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (since_ms, limit),
                )
            else:
                cur = self._conn.execute(
                    """SELECT id, timestamp, input_raw, output, app_name, context_id, user_picked, duration_ms
                       FROM commit_log ORDER BY timestamp DESC LIMIT ?""",
                    (limit,),
                )
            return [CommitRecord(*row[:6], bool(row[6]), row[7]) for row in cur.fetchall()]

    def word_freq_lookup(self, words: Iterable[str]) -> dict[str, int]:
        """批量查字频。未出现的字 count=0。"""
        ws = list(words)
        if not ws:
            return {}
        with self._lock:
            placeholders = ",".join("?" * len(ws))
            cur = self._conn.execute(
                f"SELECT word, count FROM word_freq WHERE word IN ({placeholders})", ws
            )
            found = dict(cur.fetchall())
        return {w: found.get(w, 0) for w in ws}

    def top_words(self, limit: int = 50) -> List[WordFreq]:
        """高频字 top N。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT word, count, last_used FROM word_freq ORDER BY count DESC LIMIT ?",
                (limit,),
            )
            return [WordFreq(*row) for row in cur.fetchall()]

    def phrase_pair_lookup(self, prev: str) -> List[Tuple[str, int]]:
        """给定 prev 字，返回所有 curr 字按 count 倒排。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT curr, count FROM phrase_pair WHERE prev = ? ORDER BY count DESC",
                (prev,),
            )
            return cur.fetchall()

    def phrase_pair_batch_lookup(
        self, prev_chars: Iterable[str]
    ) -> dict[str, List[Tuple[str, int]]]:
        """
        批量查多个 prev 字的二元组 (D-009 P1.3: 消除 reranker 的 N+1 查询)。

        返回 {prev_char: [(curr, count), ...]} 字典。
        未出现的 prev 字键缺失。
        """
        prevs = list({p for p in prev_chars if p})  # 去重 + 过滤空
        if not prevs:
            return {}
        with self._lock:
            placeholders = ",".join("?" * len(prevs))
            cur = self._conn.execute(
                f"""SELECT prev, curr, count FROM phrase_pair
                    WHERE prev IN ({placeholders})
                    ORDER BY prev, count DESC""",
                prevs,
            )
            rows = cur.fetchall()
        result: dict[str, List[Tuple[str, int]]] = {}
        for prev, curr, count in rows:
            result.setdefault(prev, []).append((curr, count))
        return result

    # ────────── 统计 / 用户面板 API ──────────

    def stats(self) -> dict:
        """整体统计：用于用户面板首页。"""
        with self._lock:
            c1 = self._conn.execute("SELECT COUNT(*) FROM commit_log").fetchone()[0]
            c2 = self._conn.execute("SELECT COUNT(*) FROM word_freq").fetchone()[0]
            c3 = self._conn.execute("SELECT COUNT(*) FROM phrase_pair").fetchone()[0]
            first_ts = self._conn.execute("SELECT MIN(timestamp) FROM commit_log").fetchone()[0]
            last_ts = self._conn.execute("SELECT MAX(timestamp) FROM commit_log").fetchone()[0]
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "total_commits": c1,
            "unique_chars": c2,
            "unique_phrase_pairs": c3,
            "first_commit_ms": first_ts,
            "last_commit_ms": last_ts,
            "db_size_bytes": db_size,
        }

    # ────────── 隐私管理 ──────────

    def add_excluded_app(self, app_name: str, reason: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO privacy_exclude (pattern, reason, created_at) VALUES (?, ?, ?)",
                (app_name, reason, int(time.time() * 1000)),
            )

    def remove_excluded_app(self, app_name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM privacy_exclude WHERE pattern = ?", (app_name,))

    def list_excluded_apps(self) -> List[Tuple[str, str, int]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT pattern, reason, created_at FROM privacy_exclude ORDER BY created_at DESC"
            )
            return cur.fetchall()

    # ────────── 配置 ──────────

    def get_config(self, key: str) -> Optional[str]:
        with self._lock:
            cur = self._conn.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = cur.fetchone()
        return row[0] if row else None

    def set_config(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
            )

    def all_config(self) -> dict[str, str]:
        with self._lock:
            cur = self._conn.execute("SELECT key, value FROM config")
            return dict(cur.fetchall())

    # ────────── 维护 ──────────

    def export_all(self) -> dict:
        """全量导出（JSON 可序列化）。用于用户面板"导出"按钮。"""
        with self._lock:
            commits = [
                dict(zip(
                    ["id", "timestamp", "input_raw", "output", "app_name",
                     "context_id", "user_picked", "duration_ms"],
                    row,
                ))
                for row in self._conn.execute(
                    """SELECT id, timestamp, input_raw, output, app_name, context_id,
                              user_picked, duration_ms FROM commit_log ORDER BY id"""
                ).fetchall()
            ]
            words = [
                {"word": w, "count": c, "last_used": lu}
                for w, c, lu in self._conn.execute(
                    "SELECT word, count, last_used FROM word_freq ORDER BY count DESC"
                ).fetchall()
            ]
            pairs = [
                {"prev": p, "curr": c, "count": cnt}
                for p, c, cnt in self._conn.execute(
                    "SELECT prev, curr, count FROM phrase_pair ORDER BY count DESC"
                ).fetchall()
            ]
            excluded = [
                {"pattern": p, "reason": r, "created_at": ts}
                for p, r, ts in self._conn.execute(
                    "SELECT pattern, reason, created_at FROM privacy_exclude"
                ).fetchall()
            ]
        return {
            "schema_version": schema.CURRENT_SCHEMA_VERSION,
            "exported_at_ms": int(time.time() * 1000),
            "commits": commits,
            "word_freq": words,
            "phrase_pair": pairs,
            "privacy_exclude": excluded,
            "config": self.all_config(),
        }

    def clear_all(self, confirm: bool = False) -> None:
        """
        删除所有用户数据。仅当 confirm=True 才执行。

        D-009 P0.8: VACUUM 必须在事务外。autocommit 模式下 DELETE 不开事务，
        但显式 commit() 确保任何待写都刷盘，再 VACUUM。
        """
        if not confirm:
            raise ValueError("clear_all requires confirm=True")
        with self._lock:
            try:
                self._conn.execute("DELETE FROM commit_log")
                self._conn.execute("DELETE FROM word_freq")
                self._conn.execute("DELETE FROM phrase_pair")
                # 保留 privacy_exclude 与 config（用户偏好）
                self._conn.commit()           # 确保无活跃事务
                self._conn.execute("VACUUM")  # 现在安全
                logger.info("clear_all completed and VACUUM-ed")
            except sqlite3.Error:
                logger.exception("clear_all failed")
                raise

    def delete_range(self, start_ms: int, end_ms: int) -> int:
        """删除时间范围内的 commit（不影响 word_freq / phrase_pair 历史聚合）。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM commit_log WHERE timestamp >= ? AND timestamp <= ?",
                (start_ms, end_ms),
            )
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
                logger.debug("EktroMemoryStore closed [%s]", self.db_path)
            except Exception:
                logger.exception("EktroMemoryStore.close failed")

    def __enter__(self) -> "EktroMemoryStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()
