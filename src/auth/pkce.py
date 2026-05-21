"""
PKCE (RFC 7636) 参数生成 — code_verifier / code_challenge / state。

服务端校验严格 S256 method:
    code_challenge == base64url(sha256(code_verifier))

任何偏离公式或编码（含 padding / 大小写）都会被服务端 PKCE 校验拒绝。

详见 docs/ektro-link-protocol.md §5 与 RFC 7636 §4.1-§4.2。
"""
from __future__ import annotations

import base64
import hashlib
import secrets


# RFC 7636 §4.1: code_verifier 长度 43~128 字符（base64url 字符集）
VERIFIER_BYTES = 48  # token_urlsafe(48) → 64 字符，落在 43~128 安全区间


def generate_code_verifier() -> str:
    """生成 PKCE code_verifier — base64url 编码的 cryptographically random 字符串。

    Returns:
        64 字符 base64url (RFC 7636 §4.1 合规)
    """
    return secrets.token_urlsafe(VERIFIER_BYTES)


def derive_code_challenge(code_verifier: str) -> str:
    """从 code_verifier 推导 code_challenge（S256 method）。

    公式: base64url(sha256(verifier))，去除 padding，与服务端
    src/app/api/v1/auth/ime-token/route.ts base64urlSha256() 严格一致。

    Args:
        code_verifier: generate_code_verifier() 的输出

    Returns:
        43 字符 base64url（sha256 32 字节 → base64url 44 字符去 padding → 43 字符）
    """
    sha = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(sha).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """生成 CSRF state token — 长度 ≥16（服务端校验下限）。"""
    # token_urlsafe(24) → 32 字符，安全冗余
    return secrets.token_urlsafe(24)


def generate_pkce_pair() -> tuple[str, str, str]:
    """一次性生成完整 PKCE 三元组（verifier / challenge / state）。

    使用示例:
        verifier, challenge, state = generate_pkce_pair()
        # 把 challenge + state 发到 /me/ime-link, 留 verifier 在内存
        # 收到 code 后用 verifier 调 /api/v1/auth/ime-token

    Returns:
        (verifier, challenge, state)
    """
    v = generate_code_verifier()
    c = derive_code_challenge(v)
    s = generate_state()
    return v, c, s
