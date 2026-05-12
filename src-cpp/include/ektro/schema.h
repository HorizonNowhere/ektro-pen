// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/memory/schema.py
// D-005 移植规约: SQL DDL 字符串原样复制

#pragma once

#include <string_view>

struct sqlite3;

namespace ektro {

constexpr int kCurrentSchemaVersion = 1;

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

// 初始化或升级 schema. 返回最终版本号, 0 表失败.
int init_db(sqlite3* db);

// 填默认配置 (与 Python schema.py DEFAULT_CONFIG 一致)
bool seed_default_config(sqlite3* db);

}  // namespace ektro
