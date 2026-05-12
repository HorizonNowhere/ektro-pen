"""
EktroMemoryStore SQLite schema.

设计文档来源：openspec/changes/ektro-mvp/design.md §8

特性：
- 明文 SQLite（CLAUDE.md 公理 ②：用户能看见和拥有自己的数据）
- 版本管理：通过 PRAGMA user_version 升级
- 索引：commit_log 按时间倒排，word_freq 按 count 倒排
- 隐私豁免：privacy_exclude 列出不落库的应用 / scope

未来 C++ 移植时按本文件字段逐一对照。
"""
from __future__ import annotations

CURRENT_SCHEMA_VERSION = 1

# 完整 schema (v1)
SCHEMA_V1 = """
-- 每次 commit 一条
CREATE TABLE IF NOT EXISTS commit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,            -- unix ms
    input_raw   TEXT NOT NULL,               -- 原始拼音 "nihaoshijie"
    output      TEXT NOT NULL,               -- commit 的中文 "你好世界"
    app_name    TEXT,                        -- 焦点应用 "Code.exe"
    context_id  INTEGER,                     -- 上下文会话 id（同一应用 + 短时间内的 commits 视为同一上下文）
    user_picked INTEGER NOT NULL DEFAULT 0,  -- 是否用户按 Tab 切换过候选
    duration_ms INTEGER                      -- 从首字到 commit 的耗时（打字节奏）
);
CREATE INDEX IF NOT EXISTS idx_commit_time ON commit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_commit_app ON commit_log(app_name, timestamp DESC);

-- 字/词频
CREATE TABLE IF NOT EXISTS word_freq (
    word        TEXT PRIMARY KEY,
    count       INTEGER NOT NULL DEFAULT 1,
    last_used   INTEGER NOT NULL             -- unix ms
);
CREATE INDEX IF NOT EXISTS idx_word_count ON word_freq(count DESC);

-- 二元组（前一个词 → 当前词）
CREATE TABLE IF NOT EXISTS phrase_pair (
    prev        TEXT NOT NULL,
    curr        TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (prev, curr)
);
CREATE INDEX IF NOT EXISTS idx_phrase_curr ON phrase_pair(curr, count DESC);

-- 用户标记的"私密"应用 / scope，不落库
CREATE TABLE IF NOT EXISTS privacy_exclude (
    pattern     TEXT PRIMARY KEY,            -- app_name 或 "PASSWORD" 等特殊 scope
    reason      TEXT,                        -- 为什么排除（用户备注）
    created_at  INTEGER NOT NULL
);

-- 配置（限制 ≤6 项核心设置，CLAUDE.md 公理 ③）
CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""


def init_db(conn) -> int:
    """
    初始化或升级 schema。返回最终 schema 版本号。

    使用 PRAGMA user_version 跟踪 schema 版本（SQLite 内置机制）。
    """
    cur = conn.execute("PRAGMA user_version")
    current = cur.fetchone()[0]

    if current == 0:
        # 全新库，建 v1
        conn.executescript(SCHEMA_V1)
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        conn.commit()
        return CURRENT_SCHEMA_VERSION

    if current < CURRENT_SCHEMA_VERSION:
        # 未来的迁移路径：v1 → v2 时在此处加 migration 函数
        raise NotImplementedError(
            f"Schema migration from v{current} to v{CURRENT_SCHEMA_VERSION} not implemented"
        )

    if current > CURRENT_SCHEMA_VERSION:
        # 用户用了未来版本的工具，现在降级使用
        raise RuntimeError(
            f"Database is schema v{current} but this code only supports v{CURRENT_SCHEMA_VERSION}. "
            f"Please upgrade EKTRO."
        )

    return current


DEFAULT_CONFIG = {
    "enable_rerank": "true",
    "enable_predictor": "true",
    "predictor_delay_ms": "300",
    "learning_enabled": "true",
    "excluded_apps": "[]",
    "theme": "auto",
}


def seed_default_config(conn) -> None:
    """填入 design.md §8 规定的 6 项默认配置（仅当 key 不存在时）。"""
    for k, v in DEFAULT_CONFIG.items():
        conn.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v)
        )
    conn.commit()
