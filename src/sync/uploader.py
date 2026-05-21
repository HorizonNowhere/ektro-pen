"""
Sync uploader — IME commit_log 异步增量同步到 ektroai.com 的守护循环。

设计原则:
- 实时打字通路 100% 本地,这里是**旁路** — 失败永远不阻塞 IME
- 推 cursor 仅在服务端确认 inserted 之后,失败可重试 (content_hash 服务端去重兜底)
- 收到 LinkInvalidError (设备被吊销) 自动停 worker,不继续耗资源
- 收到 RateLimitError 按 retry_after 退避
- heartbeat 与 ingest 同 worker,每 HEARTBEAT_INTERVAL 一次 (1h)

详见 docs/ime-ingest-contract.md §4 §5。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass

from auth.token_manager import LinkInvalidError, NotLinkedError, TokenManager
from memory.link_store import LinkStore
from sync import hasher
from sync.sync_client import IngestResponse, RateLimitError, SyncClient


# ────────────── 调优参数 ──────────────

SYNC_INTERVAL_SECONDS = 5 * 60        # 5 min 触发一次增量 sync
SYNC_BATCH_THRESHOLD = 100            # 累积 N 条立刻触发 (不等 interval)
SYNC_BATCH_MAX_SIZE = 200             # 服务端 ingest 上限 (与 ime-ingest-contract §4 一致)
HEARTBEAT_INTERVAL_SECONDS = 60 * 60  # 1h 一次
BACKOFF_INITIAL = 5                   # 失败首次退避秒
BACKOFF_MAX = 300                     # 退避上限 5min


@dataclass
class _UnprocessedCommit:
    """从 commit_log 拉出来准备上传的行。"""
    id: int  # commit_log.id (本地)
    timestamp: int
    input_raw: str
    output: str
    user_picked: bool
    duration_ms: int | None
    app_name: str | None


def _fetch_unprocessed(
    conn: sqlite3.Connection,
    lock: threading.Lock,
    after_id: int,
    limit: int,
) -> list[_UnprocessedCommit]:
    """从 commit_log 拉 id > after_id 的行。"""
    with lock:
        cur = conn.execute(
            "SELECT id, timestamp, input_raw, output, user_picked, duration_ms, app_name "
            "FROM commit_log WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        )
        rows = cur.fetchall()
    return [
        _UnprocessedCommit(
            id=r[0], timestamp=r[1], input_raw=r[2], output=r[3],
            user_picked=bool(r[4]), duration_ms=r[5], app_name=r[6],
        )
        for r in rows
    ]


def _build_payload(commits: list[_UnprocessedCommit], device_id: str) -> list[dict]:
    """commit_log 行 → ingest payload (字段白名单,与服务端 strict zod 一致)。"""
    out = []
    for c in commits:
        item: dict = {
            "device_id": device_id,
            "client_ts": c.timestamp,
            "input_raw": c.input_raw,
            "output": c.output,
            "user_picked": 1 if c.user_picked else 0,
            "content_hash": hasher.hash_commit(device_id, c.timestamp, c.input_raw, c.output),
        }
        if c.duration_ms is not None:
            item["duration_ms"] = c.duration_ms
        if c.app_name:
            item["app_name"] = c.app_name
        out.append(item)
    return out


@dataclass
class SyncOutcome:
    """单次 sync 周期的结果。"""
    pulled: int        # 从 commit_log 拉的行数
    uploaded: int      # 服务端 inserted 数 (去重后)
    deduplicated: int  # 服务端 deduplicated 数
    error: str | None  # 失败原因 (网络 / 限流 / 凭证失效)
    rate_limited_for: int | None = None  # 限流时建议退避秒


def sync_once(
    *,
    conn: sqlite3.Connection,
    lock: threading.Lock,
    link_store: LinkStore,
    token_manager: TokenManager,
    sync_client: SyncClient,
) -> SyncOutcome:
    """执行单次 sync 周期 (拉 → 上传 → 推 cursor)。

    不抛异常 — 所有错误捕获后写入 SyncOutcome.error,worker loop 据此决定是否退避/停。

    Returns:
        SyncOutcome — 调用方据此决定下次 interval 和退避策略
    """
    if not token_manager.is_linked():
        return SyncOutcome(0, 0, 0, "not_linked")

    cursor = link_store.get_sync_cursor()
    device_link = link_store.get_device_link()
    device_id = device_link.device_id

    commits = _fetch_unprocessed(conn, lock, cursor.last_synced_commit_id, SYNC_BATCH_MAX_SIZE)
    if not commits:
        link_store.set_pending_count(0)
        return SyncOutcome(0, 0, 0, None)

    # 更新 pending_count (UI 显示用) — 在上传之前
    pending_total = len(commits)  # 这是当前批,实际可能还有更多 (我们只读 SYNC_BATCH_MAX_SIZE)
    link_store.set_pending_count(pending_total)

    payload = _build_payload(commits, device_id)
    last_commit_id = commits[-1].id

    try:
        resp: IngestResponse = sync_client.upload_signals(
            device_id=device_id, commits=payload,
        )
    except NotLinkedError:
        return SyncOutcome(len(commits), 0, 0, "not_linked")
    except LinkInvalidError as e:
        # 设备被吊销 / refresh 失败 — token_manager 已清状态
        link_store.record_sync_failure(f"link_invalid: {e}")
        return SyncOutcome(len(commits), 0, 0, "link_invalid")
    except RateLimitError as e:
        link_store.record_sync_failure(f"rate_limit: retry_after {e.retry_after}s")
        return SyncOutcome(len(commits), 0, 0, "rate_limit", rate_limited_for=e.retry_after)
    except Exception as e:
        link_store.record_sync_failure(f"upload error: {e}")
        return SyncOutcome(len(commits), 0, 0, "upload_error")

    # 成功 — 推 cursor (即使部分被服务端去重也算这一批已处理)
    link_store.advance_cursor(last_commit_id, uploaded_delta=resp.inserted)
    link_store.set_pending_count(0)

    return SyncOutcome(
        pulled=len(commits),
        uploaded=resp.inserted,
        deduplicated=resp.deduplicated,
        error=None,
    )


def heartbeat_once(
    *,
    link_store: LinkStore,
    sync_client: SyncClient,
) -> dict | None:
    """单次 heartbeat。返回 server response dict;None 表示未链接 / 失败。

    UI 应展示返回的 deletion_notices (作为"云端删除日志")。
    """
    if not link_store.get_device_link().is_linked:
        return None

    cursor = link_store.get_sync_cursor()
    device_id = link_store.get_device_link().device_id

    try:
        resp = sync_client.heartbeat(
            device_id=device_id,
            pending_count=cursor.pending_count,
            total_uploaded=cursor.total_uploaded,
            last_sync_at=cursor.last_sync_at,
        )
    except (NotLinkedError, LinkInvalidError):
        return None
    except Exception:
        # 不阻塞 sync,下次再试
        return None

    return {
        "server_total_received": resp.server_total_received,
        "deletion_notices": resp.deletion_notices,
        "device_status": resp.device_status,
    }


class UploaderDaemon:
    """sync 守护线程 — start/stop 控制生命周期。

    使用:
        daemon = UploaderDaemon(conn, lock, link_store, token_manager, sync_client)
        daemon.start()
        # ... IME 正常使用 ...
        daemon.stop()  # 优雅停止 (等当前 sync 结束)

    单例 — 一个进程只应有一个实例,避免重复 sync 撞 token rotation。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.Lock,
        link_store: LinkStore,
        token_manager: TokenManager,
        sync_client: SyncClient,
        *,
        sync_interval: int = SYNC_INTERVAL_SECONDS,
        heartbeat_interval: int = HEARTBEAT_INTERVAL_SECONDS,
    ):
        self._conn = conn
        self._lock = lock
        self._link_store = link_store
        self._token_manager = token_manager
        self._sync_client = sync_client
        self._sync_interval = sync_interval
        self._heartbeat_interval = heartbeat_interval

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()  # 外部 trigger 加速下次 sync
        self._thread: threading.Thread | None = None

    # ────────── 生命周期 ──────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ektro-sync-uploader", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def trigger_sync(self) -> None:
        """外部主动 trigger 立刻 sync (如累积 N 条触发)。"""
        self._wake_event.set()

    # ────────── 主循环 ──────────

    def _loop(self) -> None:
        backoff = BACKOFF_INITIAL
        next_heartbeat_at = time.time() + self._heartbeat_interval

        while not self._stop_event.is_set():
            # ── sync ──
            try:
                outcome = sync_once(
                    conn=self._conn,
                    lock=self._lock,
                    link_store=self._link_store,
                    token_manager=self._token_manager,
                    sync_client=self._sync_client,
                )
            except Exception as e:  # 安全网
                self._link_store.record_sync_failure(f"loop crash: {e}")
                outcome = SyncOutcome(0, 0, 0, "crash")

            # 决定下次等多久
            if outcome.error == "link_invalid":
                # 凭证失效 — 停 worker (TokenManager 已清状态;用户需重新链接)
                break

            if outcome.error == "rate_limit":
                wait = outcome.rate_limited_for or BACKOFF_MAX
            elif outcome.error in ("upload_error", "crash"):
                wait = min(backoff, BACKOFF_MAX)
                backoff = min(backoff * 2, BACKOFF_MAX)
            else:
                # 成功 — 重置退避,等 interval
                backoff = BACKOFF_INITIAL
                # 如果还有积压 (拉满了 SYNC_BATCH_MAX_SIZE),立刻继续
                if outcome.pulled >= SYNC_BATCH_MAX_SIZE:
                    wait = 0
                else:
                    wait = self._sync_interval

            # ── heartbeat ──
            if time.time() >= next_heartbeat_at:
                heartbeat_once(
                    link_store=self._link_store, sync_client=self._sync_client,
                )
                next_heartbeat_at = time.time() + self._heartbeat_interval

            # ── 等待 (可被 trigger / stop 提前唤醒) ──
            if wait > 0:
                self._wake_event.wait(timeout=wait)
                self._wake_event.clear()
