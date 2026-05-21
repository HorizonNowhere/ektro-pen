"""
Device Authorization Grant — RFC 8628 降级路径。

无浏览器 / 防火墙阻 loopback / SSH 远程 等场景使用。

流程:
1. POST /api/v1/auth/ime-device-code → user_code (展示给用户) + device_code (轮询用)
2. 用户在另一设备/浏览器打开 verification_uri 输入 user_code
3. 客户端按 interval 轮询 /api/v1/auth/ime-device-poll
4. 用户在浏览器批准 → 下次 poll 返回 tokens

提供两个 API:
- start_device_grant: 启动并返回 user-facing 信息 (调用方决定怎么显示)
- poll_until_complete: 阻塞轮询直到批准 / 超时 / 拒绝

详见 docs/ektro-link-protocol.md §9。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from auth import oauth_client
from auth.oauth_client import DeviceCodeStart, OAuthError, TokenPair
from auth.token_manager import TokenManager
from memory.link_store import LinkStore


@dataclass(frozen=True)
class DeviceGrantSession:
    """启动 Device Grant 后供 UI 展示 / 轮询用。"""
    user_code: str             # 展示给用户输入: "WKXG-9PPP"
    verification_uri: str      # https://ektroai.com/me/ime-link
    verification_uri_complete: str  # 上面带 ?user_code= 拼好
    device_code: str           # 客户端保留,不展示
    expires_in: int            # 整体 device_code 寿命秒
    interval: int              # 轮询间隔秒


@dataclass(frozen=True)
class DeviceGrantResult:
    """轮询完成的结果。"""
    ok: bool
    error: str | None = None           # 'expired_token' / 'access_denied' / 'timeout'
    user_id: str | None = None
    user_handle: str | None = None


def start_device_grant(
    *,
    link_store: LinkStore,
    device_label: str | None = None,
) -> DeviceGrantSession:
    """启动 Device Grant — 返回 user-facing 信息供 UI 渲染。"""
    link = link_store.get_device_link()
    start = oauth_client.start_device_code(
        base_url=link.ektro_endpoint,
        device_id=link.device_id,
        device_label=device_label,
    )
    return DeviceGrantSession(
        user_code=start.user_code,
        verification_uri=start.verification_uri,
        verification_uri_complete=start.verification_uri_complete,
        device_code=start.device_code,
        expires_in=start.expires_in,
        interval=start.interval,
    )


def poll_until_complete(
    *,
    link_store: LinkStore,
    token_manager: TokenManager,
    session: DeviceGrantSession,
    max_wait_seconds: float | None = None,
) -> DeviceGrantResult:
    """阻塞轮询直到用户批准 / 拒绝 / 超时。

    Args:
        session: start_device_grant 的返回值
        max_wait_seconds: 调用方上限 (None=用 session.expires_in)

    Returns:
        DeviceGrantResult — ok=True 时凭证已存入
    """
    link = link_store.get_device_link()
    deadline = time.time() + (max_wait_seconds or session.expires_in)
    interval = max(1, session.interval)

    while time.time() < deadline:
        try:
            tp = oauth_client.poll_device_code(
                base_url=link.ektro_endpoint,
                device_code=session.device_code,
            )
        except OAuthError as e:
            msg = str(e)
            if "expired" in msg.lower():
                return DeviceGrantResult(ok=False, error="expired_token")
            if "denied" in msg.lower():
                return DeviceGrantResult(ok=False, error="access_denied")
            if "slow_down" in msg.lower():
                interval += 5
                time.sleep(interval)
                continue
            return DeviceGrantResult(ok=False, error="oauth_error")
        except Exception:
            # 网络抖动 — 不放弃,按 interval 重试
            time.sleep(interval)
            continue

        if tp is not None:
            # 用户已批准 — 保存凭证
            token_manager.save_initial_tokens(tp)
            return DeviceGrantResult(
                ok=True, user_id=tp.user_id, user_handle=tp.user_handle,
            )

        time.sleep(interval)

    return DeviceGrantResult(ok=False, error="timeout")
