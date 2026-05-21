"""
首次回填三模式编排 — 链接成功后的一次性历史迁移。

模式 (用户选):
- 'full':       上传完整 commit_log 历史 (最强 Twin 训练)
- 'aggregate':  仅上传 word_freq + phrase_pair 聚合 (隐私优先)
- 'none':       从链接时刻起开始增量 (跳过历史)

设计:
- 分片上传 (每片 SYNC_BATCH_MAX_SIZE 条),失败按 backfill_state.last_uploaded_commit_id 续传
- 永远不抛异常 — 错误写入 BackfillResult.error 由调用方决定 UI 反馈
- 进度持久化 (backfill_state),崩了再启可断点续传
- 完成后由 UploaderDaemon 自动接管增量 sync

详见 docs/ime-ingest-contract.md §3。
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

from auth.token_manager import LinkInvalidError, TokenManager
from memory.link_store import LinkStore
from sync import hasher
from sync.sync_client import IngestResponse, RateLimitError, SyncClient


# 与服务端 ime-ingest-contract §3 一致
BACKFILL_CHUNK_SIZE = 500
BACKFILL_MAX_PAYLOAD_BYTES = 1024 * 1024  # 1 MB


@dataclass(frozen=True)
class BackfillResult:
    """一次回填周期的结果 (整个 backfill 可能多次调 run_backfill 续传)。"""
    mode: str
    total_pulled: int        # 本次从本地拉的条数
    total_uploaded: int      # 服务端 inserted (去重后)
    completed: bool          # 整个 backfill 是否完成
    error: str | None = None # 'link_invalid' / 'rate_limit' / 'upload_error' / 'invalid_mode'
    rate_limited_for: int | None = None


def _count_local_commits(conn: sqlite3.Connection, lock: threading.Lock) -> int:
    with lock:
        return conn.execute("SELECT count(*) FROM commit_log").fetchone()[0]


def _count_words(conn: sqlite3.Connection, lock: threading.Lock) -> int:
    with lock:
        return conn.execute("SELECT count(*) FROM word_freq").fetchone()[0]


def _count_phrases(conn: sqlite3.Connection, lock: threading.Lock) -> int:
    with lock:
        return conn.execute("SELECT count(*) FROM phrase_pair").fetchone()[0]


def _fetch_commit_chunk(
    conn: sqlite3.Connection, lock: threading.Lock,
    after_id: int, limit: int,
) -> list[tuple]:
    with lock:
        return conn.execute(
            "SELECT id, timestamp, input_raw, output, user_picked, duration_ms, app_name "
            "FROM commit_log WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        ).fetchall()


def _build_commit_items(rows: list[tuple], device_id: str) -> list[dict]:
    out = []
    for r in rows:
        cid, ts, input_raw, output, user_picked, duration_ms, app_name = r
        item: dict = {
            "device_id": device_id,
            "client_ts": ts,
            "input_raw": input_raw,
            "output": output,
            "user_picked": 1 if user_picked else 0,
            "content_hash": hasher.hash_commit(device_id, ts, input_raw, output),
        }
        if duration_ms is not None:
            item["duration_ms"] = duration_ms
        if app_name:
            item["app_name"] = app_name
        out.append(item)
    return out


def _fetch_words(conn: sqlite3.Connection, lock: threading.Lock) -> list[tuple]:
    with lock:
        return conn.execute(
            "SELECT word, count, last_used FROM word_freq ORDER BY count DESC"
        ).fetchall()


def _fetch_phrases(conn: sqlite3.Connection, lock: threading.Lock) -> list[tuple]:
    with lock:
        return conn.execute(
            "SELECT prev, curr, count FROM phrase_pair ORDER BY count DESC"
        ).fetchall()


def _build_word_items(rows: list[tuple], device_id: str) -> list[dict]:
    return [
        {
            "device_id": device_id,
            "word": word,
            "count": count,
            "last_used_ts": last_used,
            "content_hash": hasher.hash_word(device_id, word),
        }
        for word, count, last_used in rows
    ]


def _build_phrase_items(rows: list[tuple], device_id: str) -> list[dict]:
    return [
        {
            "device_id": device_id,
            "prev": prev,
            "curr": curr,
            "count": count,
            "content_hash": hasher.hash_phrase(device_id, prev, curr),
        }
        for prev, curr, count in rows
    ]


def run_backfill(
    *,
    conn: sqlite3.Connection,
    lock: threading.Lock,
    link_store: LinkStore,
    token_manager: TokenManager,
    sync_client: SyncClient,
    mode: str,
) -> BackfillResult:
    """启动并执行回填 (阻塞直到完成或失败/限流)。

    Args:
        mode: 'full' / 'aggregate' / 'none'

    Returns:
        BackfillResult — completed=True 表示 backfill 标记完成,UploaderDaemon 可接管增量
    """
    if mode not in ("full", "aggregate", "none"):
        return BackfillResult(mode=mode, total_pulled=0, total_uploaded=0,
                              completed=False, error="invalid_mode")

    if not token_manager.is_linked():
        return BackfillResult(mode=mode, total_pulled=0, total_uploaded=0,
                              completed=False, error="not_linked")

    device_id = link_store.get_device_link().device_id

    # ── 'none' 模式: 立即标完成,跳过实际上传 ──
    if mode == "none":
        link_store.start_backfill("none")
        return BackfillResult(mode="none", total_pulled=0, total_uploaded=0, completed=True)

    # ── 'full' / 'aggregate' 都需要先调服务端 start ──
    total_commits = _count_local_commits(conn, lock) if mode == "full" else 0
    total_words = _count_words(conn, lock) if mode == "aggregate" else 0
    total_phrases = _count_phrases(conn, lock) if mode == "aggregate" else 0

    try:
        start_resp = sync_client.start_backfill(
            device_id=device_id, mode=mode,
            total_commits=total_commits if mode == "full" else None,
            total_words=total_words if mode == "aggregate" else None,
            total_phrases=total_phrases if mode == "aggregate" else None,
        )
    except LinkInvalidError as e:
        link_store.record_backfill_error(f"link_invalid: {e}")
        return BackfillResult(mode=mode, total_pulled=0, total_uploaded=0,
                              completed=False, error="link_invalid")
    except RateLimitError as e:
        link_store.record_backfill_error(f"rate_limit: retry_after {e.retry_after}s")
        return BackfillResult(mode=mode, total_pulled=0, total_uploaded=0,
                              completed=False, error="rate_limit",
                              rate_limited_for=e.retry_after)
    except Exception as e:
        link_store.record_backfill_error(f"start failed: {e}")
        return BackfillResult(mode=mode, total_pulled=0, total_uploaded=0,
                              completed=False, error="start_failed")

    backfill_id = start_resp["backfill_id"]
    expected_total = total_commits if mode == "full" else (total_words + total_phrases)
    link_store.start_backfill(mode, total_to_upload=expected_total)

    # ── 分片上传 ──
    total_pulled = 0
    total_uploaded = 0

    if mode == "full":
        # 续传:从 backfill_state.last_uploaded_commit_id 之后开始
        last_id = link_store.get_backfill_state().last_uploaded_commit_id or 0
        while True:
            rows = _fetch_commit_chunk(conn, lock, last_id, BACKFILL_CHUNK_SIZE)
            if not rows:
                break
            items = _build_commit_items(rows, device_id)
            try:
                resp = sync_client.upload_backfill_chunk(
                    backfill_id=backfill_id, device_id=device_id,
                    kind="commits", items=items,
                )
            except LinkInvalidError as e:
                link_store.record_backfill_error(f"link_invalid: {e}")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "link_invalid")
            except RateLimitError as e:
                link_store.record_backfill_error(f"rate_limit: retry_after {e.retry_after}s")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "rate_limit", rate_limited_for=e.retry_after)
            except Exception as e:
                link_store.record_backfill_error(f"chunk error: {e}")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "upload_error")

            total_pulled += len(rows)
            total_uploaded += resp.inserted
            last_id = rows[-1][0]  # commit_log.id
            link_store.advance_backfill(last_id, resp.inserted)

    else:  # aggregate
        # words 一次性上传 (一般 ≤ 几千条)
        word_rows = _fetch_words(conn, lock)
        for chunk_start in range(0, len(word_rows), BACKFILL_CHUNK_SIZE):
            chunk = word_rows[chunk_start:chunk_start + BACKFILL_CHUNK_SIZE]
            items = _build_word_items(chunk, device_id)
            try:
                resp = sync_client.upload_backfill_chunk(
                    backfill_id=backfill_id, device_id=device_id,
                    kind="words", items=items,
                )
            except LinkInvalidError as e:
                link_store.record_backfill_error(f"link_invalid: {e}")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "link_invalid")
            except RateLimitError as e:
                link_store.record_backfill_error(f"rate_limit: retry_after {e.retry_after}s")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "rate_limit", rate_limited_for=e.retry_after)
            except Exception as e:
                link_store.record_backfill_error(f"words chunk error: {e}")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "upload_error")
            total_pulled += len(chunk)
            total_uploaded += resp.inserted
            link_store.advance_backfill(None, resp.inserted)

        # phrases
        phrase_rows = _fetch_phrases(conn, lock)
        for chunk_start in range(0, len(phrase_rows), BACKFILL_CHUNK_SIZE):
            chunk = phrase_rows[chunk_start:chunk_start + BACKFILL_CHUNK_SIZE]
            items = _build_phrase_items(chunk, device_id)
            try:
                resp = sync_client.upload_backfill_chunk(
                    backfill_id=backfill_id, device_id=device_id,
                    kind="phrases", items=items,
                )
            except LinkInvalidError as e:
                link_store.record_backfill_error(f"link_invalid: {e}")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "link_invalid")
            except RateLimitError as e:
                link_store.record_backfill_error(f"rate_limit: retry_after {e.retry_after}s")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "rate_limit", rate_limited_for=e.retry_after)
            except Exception as e:
                link_store.record_backfill_error(f"phrases chunk error: {e}")
                return BackfillResult(mode, total_pulled, total_uploaded,
                                      False, "upload_error")
            total_pulled += len(chunk)
            total_uploaded += resp.inserted
            link_store.advance_backfill(None, resp.inserted)

    # ── 全部分片上传完 — 调 complete ──
    try:
        sync_client.complete_backfill(
            backfill_id=backfill_id, device_id=device_id,
            client_total_uploaded=total_uploaded,
        )
    except Exception as e:
        link_store.record_backfill_error(f"complete failed: {e}")
        return BackfillResult(mode, total_pulled, total_uploaded,
                              False, "complete_failed")

    link_store.complete_backfill()
    return BackfillResult(mode=mode, total_pulled=total_pulled,
                          total_uploaded=total_uploaded, completed=True)
