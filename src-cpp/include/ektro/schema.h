// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/memory/schema.py
// D-005 移植规约: SQL DDL 字符串原样复制

#pragma once

#include <string_view>

struct sqlite3;

namespace ektro {

constexpr int kCurrentSchemaVersion = 2;

// Schema v1 - 与 Python schema.py SCHEMA_V1 一字不差
constexpr std::string_view kSchemaV1 = R"(
CREATE TABLE IF NOT EXISTS commit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,
    input_raw   TEXT NOT NULL,
    output      TEXT NOT NULL,
    app_name    TEXT,
    context_id  INTEGER,
    user_picked INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_commit_time ON commit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_commit_app ON commit_log(app_name, timestamp DESC);

CREATE TABLE IF NOT EXISTS word_freq (
    word        TEXT PRIMARY KEY,
    count       INTEGER NOT NULL DEFAULT 1,
    last_used   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_word_count ON word_freq(count DESC);

CREATE TABLE IF NOT EXISTS phrase_pair (
    prev        TEXT NOT NULL,
    curr        TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (prev, curr)
);
CREATE INDEX IF NOT EXISTS idx_phrase_curr ON phrase_pair(curr, count DESC);

CREATE TABLE IF NOT EXISTS privacy_exclude (
    pattern     TEXT PRIMARY KEY,
    reason      TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
)";

// Schema v2 增量 - 与 Python schema.py SCHEMA_V2_ADDITIONS 一字不差
// ime-twin-link 链接基础设施: device_link / sync_cursor / backfill_state
// 三张单行表 (CHECK id=1 强制),不动 v1 五张表。
constexpr std::string_view kSchemaV2Additions = R"(
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

CREATE TABLE IF NOT EXISTS sync_cursor (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    last_synced_commit_id   INTEGER NOT NULL DEFAULT 0,
    last_sync_at            INTEGER,
    last_attempt_at         INTEGER,
    last_error              TEXT,
    pending_count           INTEGER NOT NULL DEFAULT 0,
    total_uploaded          INTEGER NOT NULL DEFAULT 0
);

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
)";

// v2 单行表 seed - device_id 用 SQLite randomblob 生成 UUIDv4-ish (无需 C++ uuid 库)
// 与 Python _seed_v2_singleton_rows 等价 (Python 用 uuid.uuid4(),此处用 hex randomblob)
constexpr std::string_view kSchemaV2Seed = R"(
INSERT OR IGNORE INTO device_link (id, device_id, created_at)
VALUES (1,
  lower(
    hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' ||
    substr(hex(randomblob(2)),2) || '-' ||
    substr('89ab',abs(random())%4+1,1) || substr(hex(randomblob(2)),2) || '-' ||
    hex(randomblob(6))
  ),
  CAST(strftime('%s','now') AS INTEGER) * 1000
);
INSERT OR IGNORE INTO sync_cursor (id) VALUES (1);
INSERT OR IGNORE INTO backfill_state (id) VALUES (1);
)";

// 初始化或升级 schema. 返回最终版本号, 0 表失败, -1 表 DB 版本高于本进程.
int init_db(sqlite3* db);

// 填默认配置 (与 Python schema.py DEFAULT_CONFIG 一致)
bool seed_default_config(sqlite3* db);

}  // namespace ektro
