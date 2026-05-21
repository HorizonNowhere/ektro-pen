"""
content_hash 计算 — 与服务端 src/core/ime/content-hash.ts 公式严格一致。

服务端 ime_signals.UNIQUE(user_id, content_hash) 约束依赖客户端按这里公式计算的 hash 去重。
任何偏离公式都会导致客户端重传时服务端重复入库（去重失效）。

详见 docs/ime-ingest-contract.md §2 与 §"content_hash 客户端计算与服务端去重"。

公式（与服务端 byte-for-byte 一致）:
    commit: sha256("ime|commit|" + device_id + "|" + client_ts + "|" + input_raw + "|" + output)
    word:   sha256("ime|word|"   + device_id + "|" + word)
    phrase: sha256("ime|phrase|" + device_id + "|" + prev   + "|" + curr)

输出格式: lowercase hex（64 字符）
"""
from __future__ import annotations

import hashlib


def hash_commit(device_id: str, client_ts: int, input_raw: str, output: str) -> str:
    """计算 commit 信号的 content_hash。

    Args:
        device_id: 本机 UUIDv4
        client_ts: 上屏时刻 unix ms
        input_raw: 拼音原文（如 "nihao"）
        output: 上屏中文（如 "你好"）

    Returns:
        64 字符 lowercase hex sha256
    """
    msg = f"ime|commit|{device_id}|{client_ts}|{input_raw}|{output}"
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


def hash_word(device_id: str, word: str) -> str:
    """计算 aggregate word 信号的 content_hash（用于 backfill aggregate 模式）。"""
    msg = f"ime|word|{device_id}|{word}"
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


def hash_phrase(device_id: str, prev: str, curr: str) -> str:
    """计算 aggregate phrase 信号的 content_hash（2-gram）。"""
    msg = f"ime|phrase|{device_id}|{prev}|{curr}"
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()
