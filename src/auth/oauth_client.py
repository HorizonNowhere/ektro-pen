"""
OAuth Client — 与服务端 /api/v1/auth/ime-* endpoints 对话。

封装 5 个调用:
1. exchange_code: PKCE code → tokens (主路径 step 9-10)
2. refresh_access_token: refresh token 旋转
3. start_device_code: Device Grant 启动
4. poll_device_code: Device Grant 轮询
5. revoke_device: 解绑设备

详见 docs/ektro-link-protocol.md §4 与 §9.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sync import http_client


@dataclass(frozen=True)
class TokenPair:
    """OAuth token 兑换响应。"""
    access_token: str
    refresh_token: str
    expires_in: int           # access_token 寿命秒数
    access_expires_at: int    # access_token 过期 unix ms
    device_id: str
    scope: str = "ime-ingest"
    user_id: str | None = None
    user_handle: str | None = None


@dataclass(frozen=True)
class DeviceCodeStart:
    """Device Grant 启动响应。"""
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class OAuthError(RuntimeError):
    """OAuth-specific 错误 (PKCE 失败 / token 过期 / 设备吊销)."""


def _api(endpoint: str, base: str) -> str:
    base = base.rstrip("/")
    return f"{base}{endpoint}"


def exchange_code(
    *,
    base_url: str,
    code: str,
    code_verifier: str,
    device_id: str,
    timeout: float = http_client.DEFAULT_TIMEOUT,
) -> TokenPair:
    """主路径 step 9-10: PKCE code → access+refresh tokens。"""
    resp = http_client.post(
        _api("/api/v1/auth/ime-token", base_url),
        body={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "device_id": device_id,
        },
        timeout=timeout,
    )
    return _parse_token_payload(resp.payload, device_id_fallback=device_id)


def refresh_access_token(
    *,
    base_url: str,
    refresh_token: str,
    device_id: str,
    timeout: float = http_client.DEFAULT_TIMEOUT,
) -> TokenPair:
    """旋转 refresh_token → 新 access + 新 refresh。"""
    resp = http_client.post(
        _api("/api/v1/auth/ime-refresh", base_url),
        body={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "device_id": device_id,
        },
        timeout=timeout,
    )
    return _parse_token_payload(resp.payload, device_id_fallback=device_id)


def start_device_code(
    *,
    base_url: str,
    device_id: str,
    device_label: str | None = None,
    timeout: float = http_client.DEFAULT_TIMEOUT,
) -> DeviceCodeStart:
    """Device Grant 启动:拿 user_code / device_code / verification_uri。"""
    body: dict[str, Any] = {
        "client_id": "ektro-pen",
        "device_id": device_id,
        "scope": "ime-ingest",
    }
    if device_label:
        body["device_label"] = device_label
    resp = http_client.post(
        _api("/api/v1/auth/ime-device-code", base_url),
        body=body,
        timeout=timeout,
    )
    p = resp.payload
    return DeviceCodeStart(
        device_code=p["device_code"],
        user_code=p["user_code"],
        verification_uri=p["verification_uri"],
        verification_uri_complete=p["verification_uri_complete"],
        expires_in=int(p["expires_in"]),
        interval=int(p["interval"]),
    )


def poll_device_code(
    *,
    base_url: str,
    device_code: str,
    timeout: float = http_client.DEFAULT_TIMEOUT,
) -> TokenPair | None:
    """Device Grant 单次轮询。

    Returns:
        TokenPair 表示用户已批准
        None 表示仍在 pending (调用方应按 interval 等待后重试)

    Raises:
        OAuthError: expired_token / access_denied / 其他终态
        http_client.ApiError: 非 OAuth 语义的错误
    """
    try:
        resp = http_client.post(
            _api("/api/v1/auth/ime-device-poll", base_url),
            body={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "ektro-pen",
            },
            timeout=timeout,
        )
        # 200 → 已批准
        return _parse_token_payload(resp.payload, device_id_fallback="")
    except http_client.ApiError as e:
        if e.status == 400 and e.code == "authorization_pending":
            return None
        if e.status == 400 and e.code == "slow_down":
            # RFC 8628: 客户端应增加 interval +5s,这里向上传递让调用方处理
            raise OAuthError(f"slow_down: increase poll interval") from e
        if e.status == 400 and e.code == "expired_token":
            raise OAuthError("device_code expired — restart device flow") from e
        if e.status == 400 and e.code == "access_denied":
            raise OAuthError("user denied authorization") from e
        # 5xx / 网络层错误向上抛
        raise


def revoke_device(
    *,
    base_url: str,
    access_token: str,
    device_id: str,
    timeout: float = http_client.DEFAULT_TIMEOUT,
) -> bool:
    """主动解绑设备 (服务端会清 refresh_tokens 表 + 标 status=revoked)。

    Returns:
        True 总是 (failure 抛 ApiError)
    """
    http_client.post(
        _api(f"/api/v1/me/devices/{device_id}/revoke", base_url),
        body={},
        bearer_token=access_token,
        timeout=timeout,
    )
    return True


def _parse_token_payload(payload: dict[str, Any], *, device_id_fallback: str) -> TokenPair:
    """服务端 token 响应 → TokenPair。"""
    try:
        # access_expires_at 服务端可能不返回时, 用 expires_in 算
        access_exp = payload.get("access_expires_at")
        if access_exp is None:
            import time
            access_exp = int(time.time() * 1000) + int(payload["expires_in"]) * 1000

        user = payload.get("user") or {}
        return TokenPair(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_in=int(payload["expires_in"]),
            access_expires_at=int(access_exp),
            device_id=str(payload.get("device_id") or device_id_fallback),
            scope=str(payload.get("scope", "ime-ingest")),
            user_id=user.get("id"),
            user_handle=user.get("handle"),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise OAuthError(f"malformed token response: {e!r}") from e
