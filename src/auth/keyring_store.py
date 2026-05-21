"""
跨平台 OS keyring — subprocess 调系统命令,避免引外部 keyring 库。

JWT access_token / refresh_token 必须存 keyring (不是 SQLite),因为:
- 凭证泄露风险与用户数据不同 (任何能读 db 的人都能假冒身份上传)
- README.md 明文承诺只对用户数据成立,不含凭证

详见 docs/ektro-link-protocol.md §6 凭证存储。

实现策略 (零外部依赖,纯 subprocess):
- macOS: security add/find/delete-generic-password
- Linux:  secret-tool (依赖 libsecret,各发行版默认装)
- Windows: cmdkey + powershell (PSCredentialManager) 或 ctypes/wincred —
  当前先实现 macOS + Linux,Windows 留 NotImplementedError 标 TODO

Service: ektro-pen
Account: <device_id>
Secret (JSON): {"access_token","refresh_token","expires_at","issued_at"}
"""
from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass


SERVICE = "ektro-pen"
SECRET_MAX_BYTES = 4096  # 防 OS keyring 写入过大


@dataclass(frozen=True)
class KeyringRecord:
    """keyring 存储的凭证完整快照。"""
    access_token: str
    refresh_token: str
    expires_at: int  # access_token 过期时间 unix ms
    issued_at: int   # 签发时间 unix ms

    def is_access_expired(self, slack_ms: int = 5 * 60 * 1000) -> bool:
        """access_token 是否过期 (默认 5min 提前刷新窗口)。"""
        import time
        return self.expires_at - slack_ms < int(time.time() * 1000)

    def to_json(self) -> str:
        return json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "issued_at": self.issued_at,
        }, separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> "KeyringRecord":
        d = json.loads(s)
        return cls(
            access_token=d["access_token"],
            refresh_token=d["refresh_token"],
            expires_at=int(d["expires_at"]),
            issued_at=int(d["issued_at"]),
        )


class KeyringUnavailableError(RuntimeError):
    """OS keyring 命令不可用 / 调用失败。"""


# ─────────────────── macOS implementation ───────────────────

def _macos_set(account: str, secret: str) -> None:
    # -U 强制更新已存在条目
    proc = subprocess.run(
        ["security", "add-generic-password", "-U",
         "-s", SERVICE, "-a", account, "-w", secret],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise KeyringUnavailableError(f"macOS security add failed: {proc.stderr.strip()}")


def _macos_get(account: str) -> str | None:
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return proc.stdout.rstrip("\n")
    # 44 = SecKeychainItemNotFound
    if "could not be found" in proc.stderr.lower() or "44" in proc.stderr:
        return None
    raise KeyringUnavailableError(f"macOS security find failed: {proc.stderr.strip()}")


def _macos_delete(account: str) -> bool:
    proc = subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", account],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return True
    if "could not be found" in proc.stderr.lower():
        return False
    raise KeyringUnavailableError(f"macOS security delete failed: {proc.stderr.strip()}")


# ─────────────────── Linux implementation (libsecret) ───────────────────

def _linux_set(account: str, secret: str) -> None:
    # secret-tool store --label="EKTRO" service ektro-pen account <id>
    proc = subprocess.run(
        ["secret-tool", "store", "--label=EKTRO",
         "service", SERVICE, "account", account],
        input=secret, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise KeyringUnavailableError(
            f"secret-tool store failed: {proc.stderr.strip()} "
            f"(install libsecret-tools? `sudo apt install libsecret-tools` on Debian)"
        )


def _linux_get(account: str) -> str | None:
    proc = subprocess.run(
        ["secret-tool", "lookup", "service", SERVICE, "account", account],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return proc.stdout.rstrip("\n") or None
    if proc.returncode == 1:  # not found
        return None
    raise KeyringUnavailableError(f"secret-tool lookup failed: {proc.stderr.strip()}")


def _linux_delete(account: str) -> bool:
    proc = subprocess.run(
        ["secret-tool", "clear", "service", SERVICE, "account", account],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


# ─────────────────── Platform dispatch ───────────────────

def _dispatch():
    sys = platform.system()
    if sys == "Darwin":
        return _macos_set, _macos_get, _macos_delete
    if sys == "Linux":
        return _linux_set, _linux_get, _linux_delete
    if sys == "Windows":
        raise NotImplementedError(
            "Windows keyring not implemented in v0.4 — fallback to wincred ctypes 或 cmdkey 留 TODO. "
            "Bug us when shipping v0.4 windows build."
        )
    raise NotImplementedError(f"unsupported platform: {sys}")


def save_credentials(account: str, record: KeyringRecord) -> None:
    """写入凭证。account 必须是 device_id。"""
    secret = record.to_json()
    if len(secret.encode("utf-8")) > SECRET_MAX_BYTES:
        raise ValueError(f"secret exceeds {SECRET_MAX_BYTES} bytes")
    set_fn, _, _ = _dispatch()
    set_fn(account, secret)


def load_credentials(account: str) -> KeyringRecord | None:
    """读取凭证。返回 None 表示未存过。"""
    _, get_fn, _ = _dispatch()
    raw = get_fn(account)
    if raw is None:
        return None
    try:
        return KeyringRecord.from_json(raw)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise KeyringUnavailableError(f"corrupt credential record: {e}") from e


def delete_credentials(account: str) -> bool:
    """删除凭证 (解绑后调)。返回 True=删了 / False=本来就没有。"""
    _, _, del_fn = _dispatch()
    return del_fn(account)
