"""
content_hash 公式与服务端 byte-for-byte 一致性测试。

如果这些测试值改变,说明客户端公式偏离了服务端 src/core/ime/content-hash.ts —
服务端 UNIQUE 去重会失效,任何重传都会重复入库。

运行:
    python3 -m unittest tests.sync.test_hasher -v
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sync.hasher import hash_commit, hash_word, hash_phrase  # noqa: E402


class TestHashCommit(unittest.TestCase):
    """commit hash = sha256("ime|commit|<device>|<ts>|<input>|<output>")"""

    def test_known_value(self):
        """已知 fixture: device/ts/input/output 组合 → 服务端等价 hash"""
        device_id = "e0df294e-ce66-46bc-a14e-8da46ba2b122"
        client_ts = 1747800000000
        input_raw = "nihao"
        output = "你好"

        # 手算验证：必须与服务端 byte-for-byte 一致
        msg = f"ime|commit|{device_id}|{client_ts}|{input_raw}|{output}"
        expected = hashlib.sha256(msg.encode("utf-8")).hexdigest()

        actual = hash_commit(device_id, client_ts, input_raw, output)
        self.assertEqual(actual, expected)
        # 64 字符 lowercase hex
        self.assertEqual(len(actual), 64)
        self.assertTrue(actual.islower())
        self.assertTrue(all(c in "0123456789abcdef" for c in actual))

    def test_chinese_output_byte_consistent(self):
        """中文 output 必须 utf-8 编码,与服务端 Node Buffer.from(str, 'utf-8') 一致"""
        h1 = hash_commit("dev-1", 1700000000000, "ni", "你")
        # 重新算
        msg = "ime|commit|dev-1|1700000000000|ni|你"
        expected = hashlib.sha256(msg.encode("utf-8")).hexdigest()
        self.assertEqual(h1, expected)

    def test_different_fields_yield_different_hashes(self):
        """4 个字段任一变都产生不同 hash"""
        base = hash_commit("d1", 1000, "a", "甲")
        self.assertNotEqual(base, hash_commit("d2", 1000, "a", "甲"))  # device 变
        self.assertNotEqual(base, hash_commit("d1", 1001, "a", "甲"))  # ts 变
        self.assertNotEqual(base, hash_commit("d1", 1000, "b", "甲"))  # input 变
        self.assertNotEqual(base, hash_commit("d1", 1000, "a", "乙"))  # output 变

    def test_deterministic(self):
        """同输入永远同输出"""
        args = ("xyz", 999, "test", "测试")
        self.assertEqual(hash_commit(*args), hash_commit(*args))


class TestHashWord(unittest.TestCase):
    """word hash = sha256("ime|word|<device>|<word>")"""

    def test_known_value(self):
        h = hash_word("dev-uuid", "好")
        expected = hashlib.sha256("ime|word|dev-uuid|好".encode("utf-8")).hexdigest()
        self.assertEqual(h, expected)

    def test_no_collision_with_commit(self):
        """word hash 与 commit hash 域分离"""
        wh = hash_word("d", "x")
        ch = hash_commit("d", 0, "", "x")
        self.assertNotEqual(wh, ch)


class TestHashPhrase(unittest.TestCase):
    """phrase hash = sha256("ime|phrase|<device>|<prev>|<curr>")"""

    def test_known_value(self):
        h = hash_phrase("dev-uuid", "你", "好")
        expected = hashlib.sha256("ime|phrase|dev-uuid|你|好".encode("utf-8")).hexdigest()
        self.assertEqual(h, expected)

    def test_order_sensitive(self):
        """phrase (prev, curr) 顺序敏感: (a,b) != (b,a)"""
        self.assertNotEqual(hash_phrase("d", "你", "好"), hash_phrase("d", "好", "你"))

    def test_no_collision_with_word(self):
        ph = hash_phrase("d", "", "x")
        wh = hash_word("d", "x")
        self.assertNotEqual(ph, wh)


if __name__ == "__main__":
    unittest.main()
