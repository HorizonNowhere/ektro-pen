"""
TokenManager 综合测试 — mock keyring + oauth_client + 真 LinkStore (sqlite in-memory)。

覆盖:
- 未链接时 get_valid_access_token → NotLinkedError
- 已链接且 token 未过期 → 直接返回
- 即将过期 → 自动 refresh + 更新 keyring
- refresh 失败 → LinkInvalidError
- on_unauthorized → 强制 refresh
- revoke_local → 清 keyring + clear_link (服务端不可达也成功)
- handle_server_revocation → 同上但不调服务端

运行:
    python3 -m unittest tests.auth.test_token_manager -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from auth import token_manager  # noqa: E402
from auth.keyring_store import KeyringRecord  # noqa: E402
from auth.oauth_client import TokenPair  # noqa: E402
from auth.token_manager import (  # noqa: E402
    LinkInvalidError,
    NotLinkedError,
    TokenManager,
)
from memory import schema  # noqa: E402
from memory.link_store import LinkStore  # noqa: E402


class _Fixture(unittest.TestCase):
    """每个 test 用独立 sqlite + LinkStore。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "t.db"),
                                    check_same_thread=False, isolation_level=None)
        schema.init_db(self.conn)
        self.link_store = LinkStore(self.conn, threading.Lock())
        self.tm = TokenManager(self.link_store)
        self.device_id = self.link_store.get_device_link().device_id

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _link_with_record(self, record: KeyringRecord) -> None:
        """模拟链接:写 keyring (mock) + LinkStore.set_link"""
        self.link_store.set_link("user-uuid", "@yijie")
        # keyring mock 由调用方设置


class TestNotLinked(_Fixture):

    def test_is_linked_false_initially(self):
        self.assertFalse(self.tm.is_linked())

    def test_get_token_raises(self):
        with self.assertRaises(NotLinkedError):
            self.tm.get_valid_access_token()

    def test_current_credentials_none(self):
        self.assertIsNone(self.tm.current_credentials())


class TestLinkedFreshToken(_Fixture):

    def setUp(self):
        super().setUp()
        # 模拟链接 + 未过期 record
        far_future = int(time.time() * 1000) + 3600 * 1000
        self.record = KeyringRecord("access-A", "refresh-A", far_future, far_future - 60_000)
        self.link_store.set_link("user-1", "@yijie")

    def test_get_token_no_refresh(self):
        """未过期时直接返回,不调 refresh"""
        with unittest.mock.patch("auth.token_manager.keyring_store.load_credentials",
                                 return_value=self.record):
            with unittest.mock.patch("auth.token_manager.oauth_client.refresh_access_token") as refresh:
                token = self.tm.get_valid_access_token()
                self.assertEqual(token, "access-A")
                refresh.assert_not_called()


class TestExpiringTokenAutoRefresh(_Fixture):

    def setUp(self):
        super().setUp()
        # 1 分钟后过期,默认 5min slack → 视为已过期
        soon = int(time.time() * 1000) + 60_000
        self.expiring = KeyringRecord("access-old", "refresh-old", soon, soon - 60_000)
        self.link_store.set_link("user-1", None)

    def test_refresh_triggered_and_persisted(self):
        new_far = int(time.time() * 1000) + 3600 * 1000
        new_tp = TokenPair(
            access_token="access-NEW",
            refresh_token="refresh-NEW",
            expires_in=3600,
            access_expires_at=new_far,
            device_id=self.device_id,
        )

        with unittest.mock.patch("auth.token_manager.keyring_store.load_credentials",
                                 return_value=self.expiring) as load_mock, \
             unittest.mock.patch("auth.token_manager.keyring_store.save_credentials") as save_mock, \
             unittest.mock.patch("auth.token_manager.oauth_client.refresh_access_token",
                                 return_value=new_tp) as refresh_mock:
            token = self.tm.get_valid_access_token()

        self.assertEqual(token, "access-NEW")
        refresh_mock.assert_called_once()
        save_mock.assert_called_once()
        # 验证新 record 内容
        new_record = save_mock.call_args.args[1]
        self.assertEqual(new_record.access_token, "access-NEW")
        self.assertEqual(new_record.refresh_token, "refresh-NEW")
        self.assertEqual(new_record.expires_at, new_far)

    def test_refresh_failure_raises_link_invalid(self):
        with unittest.mock.patch("auth.token_manager.keyring_store.load_credentials",
                                 return_value=self.expiring), \
             unittest.mock.patch("auth.token_manager.oauth_client.refresh_access_token",
                                 side_effect=RuntimeError("refresh denied")):
            with self.assertRaises(LinkInvalidError):
                self.tm.get_valid_access_token()


class TestOnUnauthorized(_Fixture):

    def setUp(self):
        super().setUp()
        far = int(time.time() * 1000) + 3600 * 1000
        self.record = KeyringRecord("a", "r", far, far - 60_000)
        self.link_store.set_link("u", "@h")

    def test_forces_refresh_even_when_fresh(self):
        new_far = int(time.time() * 1000) + 3600 * 1000
        new_tp = TokenPair("NEW-A", "NEW-R", 3600, new_far, self.device_id)
        with unittest.mock.patch("auth.token_manager.keyring_store.load_credentials",
                                 return_value=self.record), \
             unittest.mock.patch("auth.token_manager.keyring_store.save_credentials") as save, \
             unittest.mock.patch("auth.token_manager.oauth_client.refresh_access_token",
                                 return_value=new_tp) as refresh:
            self.tm.on_unauthorized()

        refresh.assert_called_once()
        save.assert_called_once()
        rec = save.call_args.args[1]
        self.assertEqual(rec.access_token, "NEW-A")

    def test_when_not_linked_raises(self):
        # 重置到未链接
        self.link_store.clear_link()
        with self.assertRaises(NotLinkedError):
            self.tm.on_unauthorized()

    def test_refresh_fail_raises_link_invalid(self):
        with unittest.mock.patch("auth.token_manager.keyring_store.load_credentials",
                                 return_value=self.record), \
             unittest.mock.patch("auth.token_manager.oauth_client.refresh_access_token",
                                 side_effect=RuntimeError("dead")):
            with self.assertRaises(LinkInvalidError):
                self.tm.on_unauthorized()


class TestRevokeLocal(_Fixture):

    def setUp(self):
        super().setUp()
        far = int(time.time() * 1000) + 3600 * 1000
        self.record = KeyringRecord("a", "r", far, far - 60_000)
        self.link_store.set_link("u", "@h")

    def test_full_revoke_calls_server_and_clears(self):
        with unittest.mock.patch("auth.token_manager.keyring_store.load_credentials",
                                 return_value=self.record), \
             unittest.mock.patch("auth.token_manager.keyring_store.delete_credentials") as delete, \
             unittest.mock.patch("auth.token_manager.oauth_client.revoke_device") as revoke:
            self.tm.revoke_local(call_server=True)

        revoke.assert_called_once()
        delete.assert_called_once()
        # link 应清空
        self.assertFalse(self.link_store.get_device_link().is_linked)

    def test_server_unreachable_still_clears_local(self):
        """关键铁律:用户主动解绑必须立即生效,服务端不可达不阻塞本地"""
        with unittest.mock.patch("auth.token_manager.keyring_store.load_credentials",
                                 return_value=self.record), \
             unittest.mock.patch("auth.token_manager.keyring_store.delete_credentials") as delete, \
             unittest.mock.patch("auth.token_manager.oauth_client.revoke_device",
                                 side_effect=RuntimeError("network down")):
            self.tm.revoke_local(call_server=True)

        delete.assert_called_once()
        self.assertFalse(self.link_store.get_device_link().is_linked)

    def test_skip_server_call(self):
        with unittest.mock.patch("auth.token_manager.keyring_store.load_credentials",
                                 return_value=self.record), \
             unittest.mock.patch("auth.token_manager.keyring_store.delete_credentials") as delete, \
             unittest.mock.patch("auth.token_manager.oauth_client.revoke_device") as revoke:
            self.tm.revoke_local(call_server=False)

        revoke.assert_not_called()
        delete.assert_called_once()


class TestHandleServerRevocation(_Fixture):

    def test_clears_state(self):
        """收到 403 device_revoked 后,清 keyring + link"""
        self.link_store.set_link("u", "@h")
        with unittest.mock.patch("auth.token_manager.keyring_store.delete_credentials") as delete:
            self.tm.handle_server_revocation()
        delete.assert_called_once()
        self.assertFalse(self.link_store.get_device_link().is_linked)

    def test_idempotent_when_not_linked(self):
        """未链接时调 handle_server_revocation 应安全 noop"""
        with unittest.mock.patch("auth.token_manager.keyring_store.delete_credentials") as delete:
            self.tm.handle_server_revocation()
        delete.assert_not_called()


class TestSaveInitialTokens(_Fixture):

    def test_writes_keyring_and_link(self):
        far = int(time.time() * 1000) + 3600 * 1000
        tp = TokenPair("a", "r", 3600, far, self.device_id, user_id="user-x", user_handle="@x")

        with unittest.mock.patch("auth.token_manager.keyring_store.save_credentials") as save:
            self.tm.save_initial_tokens(tp)

        save.assert_called_once()
        # 验证传入的 record
        record = save.call_args.args[1]
        self.assertEqual(record.access_token, "a")
        self.assertEqual(record.refresh_token, "r")

        link = self.link_store.get_device_link()
        self.assertTrue(link.is_linked)
        self.assertEqual(link.linked_user_id, "user-x")
        self.assertEqual(link.linked_user_handle, "@x")


if __name__ == "__main__":
    unittest.main()
