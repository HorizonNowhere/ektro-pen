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

CURRENT_SCHEMA_VERSION = 2

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

# v2 新增：ime-twin-link 链接基础设施
# 详见 docs/local-memory-schema.md §3
SCHEMA_V2_ADDITIONS = """
-- 设备元信息 + ektro 链接状态（单行 CHECK 强制一机一账号）
CREATE TABLE IF NOT EXISTS device_link (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    device_id       TEXT    NOT NULL UNIQUE,
    device_label    TEXT,
    created_at      INTEGER NOT NULL,
    linked_user_id     TEXT,
    linked_user_handle TEXT,
    linked_at          INTEGER,
    revoked_at         INTEGER,
    ektro_endpoint     TEXT NOT NULL DEFAULT 'https://ektroai.com'
);

-- 增量同步位点（单行）
CREATE TABLE IF NOT EXISTS sync_cursor (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    last_synced_commit_id   INTEGER NOT NULL DEFAULT 0,
    last_sync_at            INTEGER,
    last_attempt_at         INTEGER,
    last_error              TEXT,
    pending_count           INTEGER NOT NULL DEFAULT 0,
    total_uploaded          INTEGER NOT NULL DEFAULT 0
);

-- 首次回填进度（单行）
CREATE TABLE IF NOT EXISTS backfill_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    mode                    TEXT,
    started_at              INTEGER,
    completed_at            INTEGER,
    last_uploaded_commit_id INTEGER,
    total_to_upload         INTEGER,
    total_uploaded          INTEGER NOT NULL DEFAULT 0,
    error                   TEXT
);
"""


def _seed_v2_singleton_rows(conn) -> None:
    """v2 三张单行表的初始化：device_id 首启生成 UUIDv4，cursor / backfill 占位行。"""
    import time
    import uuid
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT OR IGNORE INTO device_link (id, device_id, created_at) VALUES (1, ?, ?)",
        (str(uuid.uuid4()), now_ms),
    )
    conn.execute("INSERT OR IGNORE INTO sync_cursor (id) VALUES (1)")
    conn.execute("INSERT OR IGNORE INTO backfill_state (id) VALUES (1)")


def _migrate_v1_to_v2(conn) -> None:
    """v1 → v2: ADD TABLE device_link / sync_cursor / backfill_state + seed 单行。

    不动 v1 的 5 张表任何字段。详见 docs/local-memory-schema.md §6。
    """
    conn.executescript(SCHEMA_V2_ADDITIONS)
    _seed_v2_singleton_rows(conn)


def init_db(conn) -> int:
    """
    初始化或升级 schema。返回最终 schema 版本号。

    使用 PRAGMA user_version 跟踪 schema 版本（SQLite 内置机制）。
    """
    cur = conn.execute("PRAGMA user_version")
    current = cur.fetchone()[0]

    if current == 0:
        # 全新库 → 建 v1 + v2 增量
        conn.executescript(SCHEMA_V1)
        conn.executescript(SCHEMA_V2_ADDITIONS)
        _seed_v2_singleton_rows(conn)
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        conn.commit()
        return CURRENT_SCHEMA_VERSION

    if current == 1 and CURRENT_SCHEMA_VERSION >= 2:
        # v1 → v2 增量迁移
        _migrate_v1_to_v2(conn)
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        conn.commit()
        return CURRENT_SCHEMA_VERSION

    if current < CURRENT_SCHEMA_VERSION:
        # 未来 v2 → v3 等迁移路径在此处接
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
