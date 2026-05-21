"""
完整 OAuth 链接流程编排 — 用户主入口。

调用 link_account() 一行触发:
1. 从 LinkStore 读 device_id + ektro_endpoint
2. 生成 PKCE 三元组
3. 启 loopback 临时 server (60s)
4. 打开浏览器跳 /me/ime-link?...
5. 阻塞等回调
6. 拿 code → exchange tokens
7. 写 keyring + 更新 LinkStore.set_link
8. 返回 LinkResult

降级路径见 device_grant.py。

详见 docs/ektro-link-protocol.md §3。
"""
from __future__ import annotations

import socket
import webbrowser
from dataclasses import dataclass
from urllib.parse import urlencode

from auth import loopback, oauth_client, pkce
from auth.token_manager import TokenManager
from memory.link_store import LinkStore


@dataclass(frozen=True)
class LinkResult:
    """链接尝试的最终结果。"""
    ok: bool
    error: str | None = None             # 'state_mismatch' / 'access_denied' / 'timeout' / 'exchange_failed' / 'open_browser'
    error_description: str | None = None
    user_id: str | None = None
    user_handle: str | None = None


def link_account(
    *,
    link_store: LinkStore,
    token_manager: TokenManager,
    device_label: str | None = None,
    timeout: float = 60.0,
    open_browser: bool = True,
) -> LinkResult:
    """
    完整链接流程 — 阻塞 ≤ timeout 秒,期间用户在浏览器完成同意。

    Args:
        link_store: 提供 device_id / ektro_endpoint
        token_manager: 用于保存最终凭证
        device_label: 显示给用户的设备名 (None=用 hostname)
        timeout: 整个流程 (loopback + exchange) 上限
        open_browser: True (默认) 调 webbrowser.open;
                      False 仅返回 URL 让上层 UI 自己开 (常用于 GUI 嵌入)

    Returns:
        LinkResult — ok=True 时凭证已写 keyring + LinkStore 已 set_link
    """
    link = link_store.get_device_link()
    device_id = link.device_id
    endpoint = link.ektro_endpoint
    label = device_label or _default_device_label()

    # 1. PKCE
    verifier, challenge, state = pkce.generate_pkce_pair()

    # 2. 启动 loopback (在打开浏览器前 — 端口先 bind 住,避免 race)
    # wait_for_callback 内部自己 bind,我们提前 _allocate_port 拿端口
    port = loopback._allocate_port()  # type: ignore[attr-defined]
    redirect_uri = f"http://{loopback.DEFAULT_HOST}:{port}/callback"

    # 3. 构造 /me/ime-link URL
    auth_url = endpoint.rstrip("/") + "/me/ime-link?" + urlencode({
        "response_type": "code",
        "client_id": "ektro-pen",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "device_id": device_id,
        "device_label": label,
        "scope": "ime-ingest",
    })

    # 4. 打开浏览器
    if open_browser:
        try:
            opened = webbrowser.open(auth_url, new=2)
            if not opened:
                return LinkResult(
                    ok=False, error="open_browser",
                    error_description="no usable browser; fall back to device-grant flow",
                )
        except Exception as e:
            return LinkResult(
                ok=False, error="open_browser", error_description=str(e),
            )

    # 5. 等回调 (阻塞)
    _, result = loopback.wait_for_callback(
        expected_state=state, timeout=timeout, port=port,
    )

    if not result.ok:
        return LinkResult(
            ok=False, error=result.error, error_description=result.error_description,
        )

    # 6. PKCE code → tokens
    try:
        tp = oauth_client.exchange_code(
            base_url=endpoint, code=result.code or "",
            code_verifier=verifier, device_id=device_id,
        )
    except Exception as e:
        return LinkResult(
            ok=False, error="exchange_failed", error_description=str(e),
        )

    # 7. 写入凭证 + 链接状态
    token_manager.save_initial_tokens(tp)

    # 同步更新 device_label (用户可能首次链接时改了)
    if device_label:
        link_store.set_device_label(device_label)

    return LinkResult(
        ok=True, user_id=tp.user_id, user_handle=tp.user_handle,
    )


def _default_device_label() -> str:
    """生成默认设备名 — hostname,无网络访问。"""
    try:
        return socket.gethostname()[:64]
    except Exception:
        return "ektro-pen-device"
