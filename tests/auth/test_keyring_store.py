"""
keyring_store 测试 — KeyringRecord JSON 序列化 + subprocess dispatch (mock)。

不真调系统 keyring (CI 环境不可用),用 unittest.mock.patch 模拟 subprocess。

运行:
    python3 -m unittest tests.auth.test_keyring_store -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from auth import keyring_store as ks  # noqa: E402
from auth.keyring_store import KeyringRecord, KeyringUnavailableError  # noqa: E402


class TestKeyringRecord(unittest.TestCase):
    """凭证数据结构序列化/反序列化"""

    def _sample(self) -> KeyringRecord:
        return KeyringRecord(
            access_token="eyJabc.def.ghi",
            refresh_token="refresh-token-xyz-1234",
            expires_at=1_800_000_000_000,
            issued_at=1_700_000_000_000,
        )

    def test_to_json_then_from_json(self):
        r = self._sample()
        s = r.to_json()
        r2 = KeyringRecord.from_json(s)
        self.assertEqual(r2, r)

    def test_to_json_compact(self):
        """无 padding,适合塞进 OS keyring (有大小限制)"""
        r = self._sample()
        s = r.to_json()
        # 无多余空格
        self.assertNotIn("  ", s)
        self.assertNotIn(", ", s)
        # 字段都在
        d = json.loads(s)
        self.assertEqual(set(d.keys()), {"access_token", "refresh_token", "expires_at", "issued_at"})

    def test_from_json_rejects_missing_field(self):
        with self.assertRaises(KeyError):
            KeyringRecord.from_json('{"access_token":"a","refresh_token":"b","expires_at":1}')

    def test_is_access_expired_future(self):
        """未来过期 → 未过期"""
        far_future = int(time.time() * 1000) + 3600 * 1000
        r = KeyringRecord("a", "r", far_future, far_future - 1000)
        self.assertFalse(r.is_access_expired())

    def test_is_access_expired_within_slack(self):
        """1 分钟后过期,默认 5 分钟 slack → 视为已过期 (该刷)"""
        soon = int(time.time() * 1000) + 60 * 1000
        r = KeyringRecord("a", "r", soon, soon - 1000)
        self.assertTrue(r.is_access_expired())

    def test_is_access_expired_with_zero_slack(self):
        """slack=0 时,1 分钟后过期 → 还没过"""
        soon = int(time.time() * 1000) + 60 * 1000
        r = KeyringRecord("a", "r", soon, soon - 1000)
        self.assertFalse(r.is_access_expired(slack_ms=0))


class _FakeProc:
    """轻量 subprocess.CompletedProcess 仿真"""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestMacOSDispatch(unittest.TestCase):
    """mock subprocess.run 验证 macOS 命令行参数 + 返回处理"""

    def _patch_platform(self, system: str = "Darwin"):
        return unittest.mock.patch("platform.system", return_value=system)

    def test_save_calls_security_command(self):
        with self._patch_platform("Darwin"), \
             unittest.mock.patch("subprocess.run", return_value=_FakeProc(0)) as m:
            r = KeyringRecord("a", "r", 100, 50)
            ks.save_credentials("device-uuid-1", r)
            cmd = m.call_args.args[0]
            self.assertEqual(cmd[0], "security")
            self.assertEqual(cmd[1], "add-generic-password")
            self.assertIn("-U", cmd)  # 强制更新
            self.assertIn(ks.SERVICE, cmd)
            self.assertIn("device-uuid-1", cmd)
            self.assertIn(r.to_json(), cmd)

    def test_save_fails_raises(self):
        with self._patch_platform("Darwin"), \
             unittest.mock.patch("subprocess.run", return_value=_FakeProc(1, stderr="oops")):
            with self.assertRaises(KeyringUnavailableError):
                ks.save_credentials("d", KeyringRecord("a", "r", 1, 1))

    def test_load_returns_record(self):
        r = KeyringRecord("access-xyz", "refresh-abc", 100, 50)
        with self._patch_platform("Darwin"), \
             unittest.mock.patch("subprocess.run",
                                 return_value=_FakeProc(0, stdout=r.to_json() + "\n")):
            loaded = ks.load_credentials("d")
            self.assertEqual(loaded, r)

    def test_load_not_found_returns_none(self):
        with self._patch_platform("Darwin"), \
             unittest.mock.patch("subprocess.run",
                                 return_value=_FakeProc(1, stderr="The specified item could not be found in the keychain.")):
            self.assertIsNone(ks.load_credentials("d"))

    def test_load_other_error_raises(self):
        with self._patch_platform("Darwin"), \
             unittest.mock.patch("subprocess.run",
                                 return_value=_FakeProc(2, stderr="totally different problem")):
            with self.assertRaises(KeyringUnavailableError):
                ks.load_credentials("d")

    def test_load_corrupt_raises(self):
        with self._patch_platform("Darwin"), \
             unittest.mock.patch("subprocess.run",
                                 return_value=_FakeProc(0, stdout="not json at all")):
            with self.assertRaises(KeyringUnavailableError):
                ks.load_credentials("d")

    def test_delete_success(self):
        with self._patch_platform("Darwin"), \
             unittest.mock.patch("subprocess.run", return_value=_FakeProc(0)):
            self.assertTrue(ks.delete_credentials("d"))

    def test_delete_missing(self):
        with self._patch_platform("Darwin"), \
             unittest.mock.patch("subprocess.run",
                                 return_value=_FakeProc(1, stderr="could not be found")):
            self.assertFalse(ks.delete_credentials("d"))


class TestLinuxDispatch(unittest.TestCase):
    """mock secret-tool 验证 Linux 命令"""

    def _patch_platform(self):
        return unittest.mock.patch("platform.system", return_value="Linux")

    def test_save_uses_secret_tool_store(self):
        with self._patch_platform(), \
             unittest.mock.patch("subprocess.run", return_value=_FakeProc(0)) as m:
            r = KeyringRecord("a", "r", 100, 50)
            ks.save_credentials("d-1", r)
            cmd = m.call_args.args[0]
            self.assertEqual(cmd[0], "secret-tool")
            self.assertEqual(cmd[1], "store")
            self.assertIn("--label=EKTRO", cmd)
            # secret 通过 stdin 传
            self.assertEqual(m.call_args.kwargs.get("input"), r.to_json())

    def test_load_lookup(self):
        r = KeyringRecord("a", "r", 100, 50)
        with self._patch_platform(), \
             unittest.mock.patch("subprocess.run",
                                 return_value=_FakeProc(0, stdout=r.to_json())):
            self.assertEqual(ks.load_credentials("d"), r)

    def test_load_not_found_returncode_1(self):
        with self._patch_platform(), \
             unittest.mock.patch("subprocess.run", return_value=_FakeProc(1)):
            self.assertIsNone(ks.load_credentials("d"))


class TestWindowsUnsupported(unittest.TestCase):
    def test_raises_not_implemented(self):
        with unittest.mock.patch("platform.system", return_value="Windows"):
            with self.assertRaises(NotImplementedError) as ctx:
                ks.save_credentials("d", KeyringRecord("a", "r", 1, 1))
            self.assertIn("v0.4", str(ctx.exception))


class TestSecretSizeLimit(unittest.TestCase):
    def test_huge_secret_rejected(self):
        """超大 secret (> 4KB) 在写入前就 ValueError,避免 OS keyring 拒收"""
        huge = "x" * 8000
        r = KeyringRecord(huge, "r", 1, 1)
        with unittest.mock.patch("platform.system", return_value="Darwin"):
            with self.assertRaises(ValueError):
                ks.save_credentials("d", r)


if __name__ == "__main__":
    unittest.main()
