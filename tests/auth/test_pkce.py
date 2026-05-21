"""
PKCE 生成 + 与服务端 base64urlSha256 公式 byte-for-byte 一致性测试。

如果服务端 PKCE 校验拒绝了客户端生成的 challenge，说明这里的公式偏离了
src/app/api/v1/auth/ime-token/route.ts base64urlSha256()。

运行:
    python3 -m unittest tests.auth.test_pkce -v
"""
from __future__ import annotations

import base64
import hashlib
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from auth.pkce import (  # noqa: E402
    derive_code_challenge,
    generate_code_verifier,
    generate_pkce_pair,
    generate_state,
)

# RFC 7636 §4.1 unreserved 字符集（base64url-safe）
PKCE_CHARSET_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class TestVerifier(unittest.TestCase):
    def test_length_in_rfc_range(self):
        """43 ≤ len ≤ 128 (RFC 7636 §4.1)"""
        v = generate_code_verifier()
        self.assertGreaterEqual(len(v), 43)
        self.assertLessEqual(len(v), 128)

    def test_charset_base64url(self):
        """仅 base64url 字符 (A-Z a-z 0-9 - _)"""
        v = generate_code_verifier()
        self.assertRegex(v, PKCE_CHARSET_RE)

    def test_high_entropy(self):
        """20 次生成无重复 (cryptographically random)"""
        seen = {generate_code_verifier() for _ in range(20)}
        self.assertEqual(len(seen), 20)


class TestChallenge(unittest.TestCase):
    def test_known_value(self):
        """已知 verifier → 已知 challenge (与服务端公式一致)"""
        verifier = "test-verifier-for-known-value-check-123456789"
        # 手算 expected
        sha = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(sha).rstrip(b"=").decode("ascii")

        actual = derive_code_challenge(verifier)
        self.assertEqual(actual, expected)

    def test_43_chars_no_padding(self):
        """sha256 32 字节 → base64url 44 字符 → 去 padding → 43 字符 (服务端校验)"""
        v = generate_code_verifier()
        c = derive_code_challenge(v)
        self.assertEqual(len(c), 43)
        self.assertNotIn("=", c)  # 必须去 padding

    def test_charset_base64url(self):
        c = derive_code_challenge(generate_code_verifier())
        self.assertRegex(c, PKCE_CHARSET_RE)

    def test_deterministic(self):
        """同 verifier → 同 challenge"""
        v = "fixed-verifier-1234567890abcdef" * 2
        self.assertEqual(derive_code_challenge(v), derive_code_challenge(v))

    def test_matches_server_formula(self):
        """模拟服务端 src/app/api/v1/auth/ime-token/route.ts base64urlSha256 实现:
            const sha = createHash("sha256").update(input, "utf-8").digest();
            return sha.toString("base64").replace(/=+$/, "").replace(/\\+/g, "-").replace(/\\//g, "_");

        ASCII 输入下 utf-8 与 ascii 等价,客户端用 ascii 编码 verifier 与服务端一致。
        """
        verifier = generate_code_verifier()
        # 服务端等效公式
        sha = hashlib.sha256(verifier.encode("utf-8")).digest()
        b64 = base64.b64encode(sha).decode("ascii")
        server_challenge = b64.replace("=", "").replace("+", "-").replace("/", "_")

        client_challenge = derive_code_challenge(verifier)
        self.assertEqual(client_challenge, server_challenge)


class TestState(unittest.TestCase):
    def test_length_above_server_min(self):
        """服务端要求 ≥16 字符,我们生成 32 字符给安全冗余"""
        s = generate_state()
        self.assertGreaterEqual(len(s), 16)

    def test_charset_base64url(self):
        self.assertRegex(generate_state(), PKCE_CHARSET_RE)

    def test_high_entropy(self):
        seen = {generate_state() for _ in range(20)}
        self.assertEqual(len(seen), 20)


class TestGeneratePkcePair(unittest.TestCase):
    def test_triple_consistent(self):
        """一次性生成的 (verifier, challenge, state) 三元组互相一致"""
        verifier, challenge, state = generate_pkce_pair()
        self.assertEqual(derive_code_challenge(verifier), challenge)
        self.assertGreaterEqual(len(state), 16)

    def test_independent_calls(self):
        """每次调用都生成新的三元组"""
        t1 = generate_pkce_pair()
        t2 = generate_pkce_pair()
        self.assertNotEqual(t1[0], t2[0])  # verifier 不同
        self.assertNotEqual(t1[1], t2[1])  # challenge 不同
        self.assertNotEqual(t1[2], t2[2])  # state 不同


if __name__ == "__main__":
    unittest.main()
