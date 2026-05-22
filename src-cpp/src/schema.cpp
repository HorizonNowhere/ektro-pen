// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/memory/schema.py
#include "ektro/schema.h"

#include <sqlite3.h>

#include <string>

namespace ektro {

int init_db(sqlite3* db) {
    if (!db) return 0;

    // 读 user_version
    int current = 0;
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, "PRAGMA user_version", -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            current = sqlite3_column_int(stmt, 0);
        }
        sqlite3_finalize(stmt);
    }

    if (current == 0) {
        // 新库 — 建 v1 + v2 增量 + seed v2 单行
        char* err = nullptr;
        std::string sql = std::string{kSchemaV1} + std::string{kSchemaV2Additions} +
                          std::string{kSchemaV2Seed};
        if (sqlite3_exec(db, sql.c_str(), nullptr, nullptr, &err) != SQLITE_OK) {
            sqlite3_free(err);
            return 0;
        }
        std::string set_ver = "PRAGMA user_version = " + std::to_string(kCurrentSchemaVersion);
        sqlite3_exec(db, set_ver.c_str(), nullptr, nullptr, nullptr);
        return kCurrentSchemaVersion;
    }

    if (current > kCurrentSchemaVersion) {
        return -1;  // 未来版本 DB，本进程无法处理
    }

    if (current == 1 && kCurrentSchemaVersion >= 2) {
        // v1 → v2 增量迁移: ADD TABLE x3 + seed 单行 (不动 v1 五张表)
        char* err = nullptr;
        std::string sql = std::string{kSchemaV2Additions} + std::string{kSchemaV2Seed};
        if (sqlite3_exec(db, sql.c_str(), nullptr, nullptr, &err) != SQLITE_OK) {
            sqlite3_free(err);
            return 0;
        }
        std::string set_ver = "PRAGMA user_version = " + std::to_string(kCurrentSchemaVersion);
        sqlite3_exec(db, set_ver.c_str(), nullptr, nullptr, nullptr);
        return kCurrentSchemaVersion;
    }

    // current == kCurrentSchemaVersion
    return current;
}

bool seed_default_config(sqlite3* db) {
    if (!db) return false;

    struct { const char* key; const char* val; } defaults[] = {
        {"enable_rerank", "true"},
        {"enable_predictor", "true"},
        {"predictor_delay_ms", "300"},
        {"learning_enabled", "true"},
        {"excluded_apps", "[]"},
        {"theme", "auto"},
    };

    const char* sql = "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)";
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        return false;
    }
    for (auto& d : defaults) {
        sqlite3_bind_text(stmt, 1, d.key, -1, SQLITE_STATIC);
        sqlite3_bind_text(stmt, 2, d.val, -1, SQLITE_STATIC);
        sqlite3_step(stmt);
        sqlite3_reset(stmt);
    }
    sqlite3_finalize(stmt);
    return true;
}

}  // namespace ektro
