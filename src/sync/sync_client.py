"""
SyncClient — 包装所有需要 access_token 的 IME endpoint 调用。

统一处理:
- 自动从 TokenManager 取 access_token
- 401 → on_unauthorized 单次 refresh 重试
- 403 device_revoked → handle_server_revocation 后抛 LinkInvalidError (调用方应停止 sync worker)
- 429 → 抛 RateLimitError 含 retry_after,调用方退避

详见 docs/ime-ingest-contract.md §1 端点清单 / §9 错误码。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from auth.token_manager import LinkInvalidError, NotLinkedError, TokenManager
from memory.link_store import LinkStore
from sync import http_client
from sync.http_client import ApiError


@dataclass(frozen=True)
class IngestResponse:
    received: int
    deduplicated: int
    inserted: int
    deletion_notices: list[dict[str, Any]]


@dataclass(frozen=True)
class HeartbeatResponse:
    server_total_received: int
    deletion_notices: list[dict[str, Any]]
    device_status: str  # 'active' / 'revoked'


class RateLimitError(RuntimeError):
    """429 限流。调用方按 retry_after 秒后重试。"""

    def __init__(self, message: str, retry_after: int | None):
        super().__init__(message)
        self.retry_after = retry_after or 60


class SyncClient:

    def __init__(self, link_store: LinkStore, token_manager: TokenManager):
        self._store = link_store
        self._tm = token_manager

    @property
    def base_url(self) -> str:
        return self._store.get_device_link().ektro_endpoint.rstrip("/")

    # ────────── 共用 wrapper ──────────

    def _authed(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        retry_on_401: bool = True,
    ) -> http_client.ApiResponse:
        """自动注入 Bearer access_token + 401/403/429 统一处理。"""
        token = self._tm.get_valid_access_token()
        try:
            return http_client.request(
                method, f"{self.base_url}{path}",
                body=body, bearer_token=token,
            )
        except ApiError as e:
            # 403 device_revoked: 服务端已吊销,清本地后向上抛 LinkInvalidError
            if e.status == 403 and e.code == "device_revoked":
                self._tm.handle_server_revocation()
                raise LinkInvalidError(f"device revoked by server: {e.message}") from e

            # 401: 单次 refresh 重试
            if e.status == 401 and retry_on_401:
                try:
                    self._tm.on_unauthorized()
                except LinkInvalidError:
                    raise
                # refresh 完成 — 重试一次,但不再 401-retry
                return self._authed(method, path, body=body, retry_on_401=False)

            # 429: 包装专用异常
            if e.status == 429:
                raise RateLimitError(e.message, e.retry_after) from e

            # 其他错误向上抛
            raise

    # ────────── Ingest 增量上传 ──────────

    def upload_signals(
        self,
        *,
        device_id: str,
        commits: list[dict[str, Any]],
        client_seq: int | None = None,
    ) -> IngestResponse:
        """POST /api/v1/ime/ingest

        Args:
            commits: 每条含 device_id / client_ts / input_raw / output / user_picked /
                     duration_ms / app_name / content_hash (按 docs/ime-ingest-contract §4)
        """
        body: dict[str, Any] = {"device_id": device_id, "commits": commits}
        if client_seq is not None:
            body["client_seq"] = client_seq
        resp = self._authed("POST", "/api/v1/ime/ingest", body=body)
        p = resp.payload
        return IngestResponse(
            received=p["received"],
            deduplicated=p["deduplicated"],
            inserted=p["inserted"],
            deletion_notices=p.get("deletion_notices", []),
        )

    # ────────── Backfill ──────────

    def start_backfill(
        self,
        *,
        device_id: str,
        mode: str,  # 'full' / 'aggregate' / 'none'
        total_commits: int | None = None,
        total_words: int | None = None,
        total_phrases: int | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/ime/backfill/start"""
        body: dict[str, Any] = {"device_id": device_id, "mode": mode}
        if total_commits is not None:
            body["total_commits"] = total_commits
        if total_words is not None:
            body["total_words"] = total_words
        if total_phrases is not None:
            body["total_phrases"] = total_phrases
        return self._authed("POST", "/api/v1/ime/backfill/start", body=body).payload

    def upload_backfill_chunk(
        self,
        *,
        backfill_id: str,
        device_id: str,
        kind: str,  # 'commits' / 'words' / 'phrases'
        items: list[dict[str, Any]],
    ) -> IngestResponse:
        """POST /api/v1/ime/backfill/chunk"""
        body = {
            "backfill_id": backfill_id,
            "device_id": device_id,
            "kind": kind,
            "items": items,
        }
        p = self._authed("POST", "/api/v1/ime/backfill/chunk", body=body).payload
        return IngestResponse(
            received=p["received"], deduplicated=p["deduplicated"],
            inserted=p["inserted"], deletion_notices=p.get("deletion_notices", []),
        )

    def complete_backfill(
        self, *, backfill_id: str, device_id: str, client_total_uploaded: int = 0,
    ) -> dict[str, Any]:
        """POST /api/v1/ime/backfill/complete"""
        body = {
            "backfill_id": backfill_id,
            "device_id": device_id,
            "client_total_uploaded": client_total_uploaded,
        }
        return self._authed("POST", "/api/v1/ime/backfill/complete", body=body).payload

    # ────────── Heartbeat ──────────

    def heartbeat(
        self,
        *,
        device_id: str,
        pending_count: int = 0,
        total_uploaded: int = 0,
        last_sync_at: int | None = None,
    ) -> HeartbeatResponse:
        """POST /api/v1/ime/heartbeat"""
        body: dict[str, Any] = {
            "device_id": device_id,
            "client_state": {
                "pending_count": pending_count,
                "total_uploaded": total_uploaded,
            },
        }
        if last_sync_at is not None:
            body["client_state"]["last_sync_at"] = last_sync_at
        p = self._authed("POST", "/api/v1/ime/heartbeat", body=body).payload
        return HeartbeatResponse(
            server_total_received=p["server_total_received"],
            deletion_notices=p.get("deletion_notices", []),
            device_status=p.get("device_status", "active"),
        )

    # ────────── 用户主动数据治理 ──────────

    def delete_range(self, *, from_ms: int, to_ms: int) -> int:
        """DELETE /api/v1/me/inputs?from=&to= 返回 deleted_count"""
        resp = self._authed(
            "DELETE", f"/api/v1/me/inputs?from={from_ms}&to={to_ms}",
        )
        return int(resp.payload.get("deleted_count", 0))

    def delete_all(self) -> int:
        """DELETE /api/v1/me/inputs/all 返回 deleted_count"""
        resp = self._authed("DELETE", "/api/v1/me/inputs/all")
        return int(resp.payload.get("deleted_count", 0))

    def fetch_since(
        self, *, device_id: str, cursor: str | None = None, limit: int = 500,
    ) -> dict[str, Any]:
        """GET /api/v1/me/inputs/since — 跨机回灌"""
        params = f"device_id={device_id}&limit={limit}"
        if cursor:
            from urllib.parse import quote
            params += f"&cursor={quote(cursor)}"
        resp = self._authed("GET", f"/api/v1/me/inputs/since?{params}")
        return resp.payload  # {items, next_cursor, has_more}
