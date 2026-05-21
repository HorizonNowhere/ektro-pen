"""
LinkStore — schema v2 三张单行表的 CRUD API：device_link / sync_cursor / backfill_state。

设计:
- 与 EktroMemoryStore 共享同一 SQLite 连接 + lock（线程安全）
- 单行表全部用 UPDATE WHERE id=1 操作（CHECK 强制只能 1 行）
- 不暴露 JWT/refresh_token 字段（凭证走 keyring_store，详见 docs/ektro-link-protocol.md §6）

详见 docs/local-memory-schema.md §3。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceLink:
    """device_link 表的单行快照。"""
    device_id: str
    device_label: str | None
    created_at: int
    linked_user_id: str | None
    linked_user_handle: str | None
    linked_at: int | None
    revoked_at: int | None
    ektro_endpoint: str

    @property
    def is_linked(self) -> bool:
        """是否当前已链接 ektro 账号（linked_user_id 非空即视为已链接）。"""
        return self.linked_user_id is not None


@dataclass(frozen=True)
class SyncCursor:
    """sync_cursor 表的单行快照。"""
    last_synced_commit_id: int
    last_sync_at: int | None
    last_attempt_at: int | None
    last_error: str | None
    pending_count: int
    total_uploaded: int


@dataclass(frozen=True)
class BackfillState:
    """backfill_state 表的单行快照。"""
    mode: str | None  # 'full' | 'aggregate' | 'none' | None=未开始
    started_at: int | None
    completed_at: int | None
    last_uploaded_commit_id: int | None
    total_to_upload: int | None
    total_uploaded: int
    error: str | None

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None


class LinkStore:
    """
    schema v2 三表的 CRUD 封装。

    并发：每次操作内部 acquire lock，调用方不需要外部加锁。

    使用示例:
        store = LinkStore(conn, lock)
        link = store.get_device_link()
        assert link.device_id  # 首启已生成 UUID
        if not link.is_linked:
            print("尚未链接 ektro 账号")
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock):
        self._conn = conn
        self._lock = lock

    # ───────── device_link ─────────

    def get_device_link(self) -> DeviceLink:
        """读取 device_link 单行。schema.init_db 已 seed，必有一行。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT device_id, device_label, created_at, linked_user_id, "
                "linked_user_handle, linked_at, revoked_at, ektro_endpoint "
                "FROM device_link WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("device_link row missing — schema.init_db not run?")
        return DeviceLink(
            device_id=row[0], device_label=row[1], created_at=row[2],
            linked_user_id=row[3], linked_user_handle=row[4], linked_at=row[5],
            revoked_at=row[6], ektro_endpoint=row[7],
        )

    def set_device_label(self, label: str) -> None:
        """更新本机显示名（用户可改）。"""
        with self._lock:
            self._conn.execute("UPDATE device_link SET device_label = ? WHERE id = 1", (label,))

    def set_link(self, user_id: str, user_handle: str | None) -> None:
        """链接成功后更新 user_id / user_handle / linked_at。revoked_at 清空。"""
        now = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "UPDATE device_link SET linked_user_id = ?, linked_user_handle = ?, "
                "linked_at = ?, revoked_at = NULL WHERE id = 1",
                (user_id, user_handle, now),
            )

    def clear_link(self) -> None:
        """解绑：清 linked_*，写 revoked_at。"""
        now = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "UPDATE device_link SET linked_user_id = NULL, linked_user_handle = NULL, "
                "revoked_at = ? WHERE id = 1",
                (now,),
            )

    def set_ektro_endpoint(self, endpoint: str) -> None:
        """覆盖默认 https://ektroai.com（自部署 / 测试用）。"""
        with self._lock:
            self._conn.execute("UPDATE device_link SET ektro_endpoint = ? WHERE id = 1", (endpoint,))

    # ───────── sync_cursor ─────────

    def get_sync_cursor(self) -> SyncCursor:
        """读取增量同步位点。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_synced_commit_id, last_sync_at, last_attempt_at, "
                "last_error, pending_count, total_uploaded FROM sync_cursor WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("sync_cursor row missing")
        return SyncCursor(
            last_synced_commit_id=row[0], last_sync_at=row[1], last_attempt_at=row[2],
            last_error=row[3], pending_count=row[4], total_uploaded=row[5],
        )

    def advance_cursor(self, new_commit_id: int, *, uploaded_delta: int) -> None:
        """
        增量上传成功后推进 cursor。

        Args:
            new_commit_id: 已上传到的 commit_log.id 最大值
            uploaded_delta: 本次新增上传的条数（累加 total_uploaded）
        """
        now = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "UPDATE sync_cursor SET last_synced_commit_id = ?, last_sync_at = ?, "
                "last_attempt_at = ?, last_error = NULL, "
                "total_uploaded = total_uploaded + ? WHERE id = 1",
                (new_commit_id, now, now, uploaded_delta),
            )

    def record_sync_failure(self, error: str) -> None:
        """同步失败：仅更新 last_attempt_at + last_error，不推 cursor。"""
        now = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "UPDATE sync_cursor SET last_attempt_at = ?, last_error = ? WHERE id = 1",
                (now, error[:500]),  # 截断防 OOM
            )

    def set_pending_count(self, count: int) -> None:
        """更新待上传数（UI 显示用）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE sync_cursor SET pending_count = ? WHERE id = 1", (count,),
            )

    # ───────── backfill_state ─────────

    def get_backfill_state(self) -> BackfillState:
        """读取首次回填进度。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT mode, started_at, completed_at, last_uploaded_commit_id, "
                "total_to_upload, total_uploaded, error FROM backfill_state WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("backfill_state row missing")
        return BackfillState(
            mode=row[0], started_at=row[1], completed_at=row[2],
            last_uploaded_commit_id=row[3], total_to_upload=row[4],
            total_uploaded=row[5], error=row[6],
        )

    def start_backfill(self, mode: str, total_to_upload: int = 0) -> None:
        """
        启动首次回填。

        Args:
            mode: 'full' | 'aggregate' | 'none'
        """
        if mode not in ("full", "aggregate", "none"):
            raise ValueError(f"invalid backfill mode: {mode!r}")
        now = int(time.time() * 1000)
        completed_at = now if mode == "none" else None  # 'none' 即时完成
        with self._lock:
            self._conn.execute(
                "UPDATE backfill_state SET mode = ?, started_at = ?, completed_at = ?, "
                "total_to_upload = ?, total_uploaded = 0, error = NULL WHERE id = 1",
                (mode, now, completed_at, total_to_upload),
            )

    def advance_backfill(self, last_commit_id: int | None, uploaded_delta: int) -> None:
        """回填中推进进度。"""
        with self._lock:
            self._conn.execute(
                "UPDATE backfill_state SET last_uploaded_commit_id = ?, "
                "total_uploaded = total_uploaded + ? WHERE id = 1",
                (last_commit_id, uploaded_delta),
            )

    def complete_backfill(self) -> None:
        """标记回填完成。"""
        now = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "UPDATE backfill_state SET completed_at = ?, error = NULL WHERE id = 1",
                (now,),
            )

    def record_backfill_error(self, error: str) -> None:
        """回填失败时记录错误（可恢复，下次继续）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE backfill_state SET error = ? WHERE id = 1", (error[:500],),
            )
