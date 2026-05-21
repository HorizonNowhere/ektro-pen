"""
TokenManager — keyring + link_store + oauth_client 三件套综合 API。

供 sync uploader / heartbeat / IME 主流程统一调:
- get_valid_access_token():  返回未过期的 access_token,自动 refresh 兜底
- on_unauthorized():         API 401/403 时调,触发 refresh 重试或转入失效态
- revoke_local():            清 keyring + LinkStore.clear_link (本地侧解绑)

线程安全:get_valid_access_token 用 Lock 保护 refresh,避免多并发同时刷新撞 token rotation。

详见 docs/ektro-link-protocol.md §7-§8。
"""
from __future__ import annotations

import threading
import time

from auth import keyring_store, oauth_client
from auth.keyring_store import KeyringRecord
from auth.oauth_client import OAuthError, TokenPair
from memory.link_store import LinkStore


class NotLinkedError(RuntimeError):
    """当前未链接 ektro 账号。调用方应引导用户走 link 流程。"""


class LinkInvalidError(RuntimeError):
    """凭证已失效 (refresh 也过期 / 设备被吊销)。调用方应清状态并提示重新链接。"""


class TokenManager:

    def __init__(self, link_store: LinkStore):
        self._store = link_store
        self._refresh_lock = threading.Lock()

    # ─────────── 读取 ───────────

    def is_linked(self) -> bool:
        """当前是否已链接到 ektro。"""
        link = self._store.get_device_link()
        return link.is_linked

    def current_credentials(self) -> KeyringRecord | None:
        """读 keyring 凭证。None 表示未链接 / keyring 没值。"""
        link = self._store.get_device_link()
        if not link.is_linked:
            return None
        return keyring_store.load_credentials(link.device_id)

    # ─────────── 写入 (link/refresh/revoke) ───────────

    def save_initial_tokens(self, tp: TokenPair) -> None:
        """OAuth handshake 成功后,把 tokens + 链接状态一次性写入。"""
        now = int(time.time() * 1000)
        record = KeyringRecord(
            access_token=tp.access_token,
            refresh_token=tp.refresh_token,
            expires_at=tp.access_expires_at,
            issued_at=now,
        )
        keyring_store.save_credentials(tp.device_id, record)

        if tp.user_id:
            self._store.set_link(user_id=tp.user_id, user_handle=tp.user_handle)
        # else: device-grant 流程不一定返回 user 信息时,后续 heartbeat 可补

    def get_valid_access_token(self, *, base_url: str | None = None) -> str:
        """返回未过期的 access_token,过期前 5min 自动 refresh。

        Raises:
            NotLinkedError:  未链接 / 无凭证
            LinkInvalidError: refresh 也失败 (要重新链接)
        """
        link = self._store.get_device_link()
        if not link.is_linked:
            raise NotLinkedError("device not linked to any ektro account")

        endpoint = base_url or link.ektro_endpoint
        record = keyring_store.load_credentials(link.device_id)
        if record is None:
            raise NotLinkedError("credentials missing from keyring")

        if not record.is_access_expired():
            return record.access_token

        # 需要 refresh — 加锁,避免多线程并发触发 token rotation 撞坏
        with self._refresh_lock:
            # double-check:可能别的线程刚刷新完
            record = keyring_store.load_credentials(link.device_id)
            if record is None:
                raise NotLinkedError("credentials missing (race)")
            if not record.is_access_expired():
                return record.access_token

            try:
                tp = oauth_client.refresh_access_token(
                    base_url=endpoint,
                    refresh_token=record.refresh_token,
                    device_id=link.device_id,
                )
            except Exception as e:
                # refresh 失败 = 旧 refresh 也过期或设备被吊销
                # 不立刻清状态 — 让 on_unauthorized 决定 (可能临时网络问题)
                raise LinkInvalidError(f"refresh failed: {e}") from e

            new_record = KeyringRecord(
                access_token=tp.access_token,
                refresh_token=tp.refresh_token,
                expires_at=tp.access_expires_at,
                issued_at=int(time.time() * 1000),
            )
            keyring_store.save_credentials(link.device_id, new_record)
            return new_record.access_token

    def on_unauthorized(self) -> None:
        """API 返回 401 时调:强制 refresh 一次。

        如果当前 access_token 不是即将过期 (但服务端却 401),说明:
        - 服务器视图与本地视图不一致 (可能服务端 JWT 已轮换密钥但本地缓存 JWKS 过期)
        - refresh 一次重新拿带新签名的 token

        Raises:
            LinkInvalidError: refresh 也失败
        """
        link = self._store.get_device_link()
        if not link.is_linked:
            raise NotLinkedError("device not linked")

        endpoint = link.ektro_endpoint
        record = keyring_store.load_credentials(link.device_id)
        if record is None:
            raise NotLinkedError("credentials missing")

        with self._refresh_lock:
            try:
                tp = oauth_client.refresh_access_token(
                    base_url=endpoint,
                    refresh_token=record.refresh_token,
                    device_id=link.device_id,
                )
            except Exception as e:
                raise LinkInvalidError(f"on_unauthorized refresh failed: {e}") from e

            new_record = KeyringRecord(
                access_token=tp.access_token,
                refresh_token=tp.refresh_token,
                expires_at=tp.access_expires_at,
                issued_at=int(time.time() * 1000),
            )
            keyring_store.save_credentials(link.device_id, new_record)

    def revoke_local(self, *, call_server: bool = True) -> None:
        """解绑设备:清 keyring + clear_link。

        Args:
            call_server: True (默认) 时也调服务端 revoke API;失败也继续清本地
                        (用户主动解绑必须立即生效,不能因网络不通就锁定)。
        """
        link = self._store.get_device_link()
        device_id = link.device_id

        if call_server and link.is_linked:
            record = keyring_store.load_credentials(device_id)
            if record:
                try:
                    oauth_client.revoke_device(
                        base_url=link.ektro_endpoint,
                        access_token=record.access_token,
                        device_id=device_id,
                    )
                except Exception:
                    # 服务端不可达不阻塞本地 revoke
                    pass

        # 本地清理 — 不可恢复
        keyring_store.delete_credentials(device_id)
        self._store.clear_link()

    def handle_server_revocation(self) -> None:
        """API 收到 403 device_revoked 时调:服务端已吊销,清本地状态。"""
        link = self._store.get_device_link()
        if link.is_linked:
            keyring_store.delete_credentials(link.device_id)
            self._store.clear_link()
