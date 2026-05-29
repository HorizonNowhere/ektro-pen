// SPDX-License-Identifier: Apache-2.0
// C++ 对照测试: 与 tests/memory/test_schema_v2.py 同样的 case
// schema v2 (ime-twin-link) — device_link / sync_cursor / backfill_state
// 跑通后 cross-check 与 Python 端行为一致 (PORTING_GUIDE §5)

#include <filesystem>
#include <string>

#include <gtest/gtest.h>
#include <sqlite3.h>

#include "ektro/schema.h"

namespace fs = std::filesystem;

class SchemaV2Fixture : public ::testing::Test {
protected:
    fs::path tmp_db;
    sqlite3* db = nullptr;

    void SetUp() override {
        tmp_db = fs::temp_directory_path() /
                 ("ektro_schema_v2_test_" +
                  std::to_string(reinterpret_cast<uintptr_t>(this)) + ".db");
        fs::remove(tmp_db);
        ASSERT_EQ(sqlite3_open(tmp_db.string().c_str(), &db), SQLITE_OK);
    }

    void TearDown() override {
        if (db) sqlite3_close(db);
        std::error_code ec;
        fs::remove(tmp_db, ec);
    }

    // helper: 查单 int
    int query_int(const std::string& sql) {
        sqlite3_stmt* stmt = nullptr;
        int result = -999;
        if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
            if (sqlite3_step(stmt) == SQLITE_ROW) {
                result = sqlite3_column_int(stmt, 0);
            }
            sqlite3_finalize(stmt);
        }
        return result;
    }

    // helper: 查单 int64 (timestamp 等 bigint 列必须用这个,int 会截断)
    int64_t query_int64(const std::string& sql) {
        sqlite3_stmt* stmt = nullptr;
        int64_t result = -999;
        if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
            if (sqlite3_step(stmt) == SQLITE_ROW) {
                result = sqlite3_column_int64(stmt, 0);
            }
            sqlite3_finalize(stmt);
        }
        return result;
    }

    // helper: 查单 text
    std::string query_text(const std::string& sql) {
        sqlite3_stmt* stmt = nullptr;
        std::string result;
        if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
            if (sqlite3_step(stmt) == SQLITE_ROW) {
                const unsigned char* t = sqlite3_column_text(stmt, 0);
                if (t) result = reinterpret_cast<const char*>(t);
            }
            sqlite3_finalize(stmt);
        }
        return result;
    }

    bool table_exists(const std::string& name) {
        return query_int(
                   "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='" +
                   name + "'") == 1;
    }

    bool exec(const std::string& sql) {
        return sqlite3_exec(db, sql.c_str(), nullptr, nullptr, nullptr) == SQLITE_OK;
    }
};

// ─────────── 全新库 ───────────

TEST_F(SchemaV2Fixture, FreshDbBuildsAllTables) {
    int version = ektro::init_db(db);
    EXPECT_EQ(version, 2);

    // v1 五张表
    EXPECT_TRUE(table_exists("commit_log"));
    EXPECT_TRUE(table_exists("word_freq"));
    EXPECT_TRUE(table_exists("phrase_pair"));
    EXPECT_TRUE(table_exists("privacy_exclude"));
    EXPECT_TRUE(table_exists("config"));

    // v2 三张新表
    EXPECT_TRUE(table_exists("device_link"));
    EXPECT_TRUE(table_exists("sync_cursor"));
    EXPECT_TRUE(table_exists("backfill_state"));

    // PRAGMA user_version
    EXPECT_EQ(query_int("PRAGMA user_version"), 2);
}

TEST_F(SchemaV2Fixture, FreshDbSeedsSingletonRows) {
    ektro::init_db(db);

    EXPECT_EQ(query_int("SELECT count(*) FROM device_link"), 1);
    EXPECT_EQ(query_int("SELECT count(*) FROM sync_cursor"), 1);
    EXPECT_EQ(query_int("SELECT count(*) FROM backfill_state"), 1);

    // device_id 非空 + 形如 UUID (36 字符含 4 个 dash)
    std::string device_id = query_text("SELECT device_id FROM device_link WHERE id=1");
    EXPECT_EQ(device_id.size(), 36u);
    EXPECT_EQ(std::count(device_id.begin(), device_id.end(), '-'), 4);

    // created_at > 0
    EXPECT_GT(query_int("SELECT created_at > 0 FROM device_link WHERE id=1"), 0);

    // sync_cursor 初始 0
    EXPECT_EQ(query_int("SELECT last_synced_commit_id FROM sync_cursor WHERE id=1"), 0);
    EXPECT_EQ(query_int("SELECT total_uploaded FROM sync_cursor WHERE id=1"), 0);

    // backfill_state.mode 初始 NULL
    EXPECT_EQ(query_int("SELECT mode IS NULL FROM backfill_state WHERE id=1"), 1);

    // ektro_endpoint 默认值
    EXPECT_EQ(query_text("SELECT ektro_endpoint FROM device_link WHERE id=1"),
              "https://ektroai.com");
}

TEST_F(SchemaV2Fixture, DeviceIdStableAcrossInit) {
    ektro::init_db(db);
    std::string id1 = query_text("SELECT device_id FROM device_link WHERE id=1");

    // 再 init 一次 — device_id 不变
    ektro::init_db(db);
    std::string id2 = query_text("SELECT device_id FROM device_link WHERE id=1");
    EXPECT_EQ(id1, id2);
}

// ─────────── v1 → v2 迁移 ───────────

TEST_F(SchemaV2Fixture, V1ToV2Migration) {
    // 手动建 v1 状态
    ASSERT_TRUE(exec(std::string{ektro::kSchemaV1}));
    ASSERT_TRUE(exec("PRAGMA user_version = 1"));

    // 插一行 v1 数据验证迁移不破坏
    ASSERT_TRUE(exec(
        "INSERT INTO commit_log (timestamp, input_raw, output) "
        "VALUES (1700000000000, 'nihao', '你好')"));

    // 跑迁移
    int version = ektro::init_db(db);
    EXPECT_EQ(version, 2);

    // v2 三表已建
    EXPECT_TRUE(table_exists("device_link"));
    EXPECT_TRUE(table_exists("sync_cursor"));
    EXPECT_TRUE(table_exists("backfill_state"));

    // v1 数据完整
    EXPECT_EQ(query_text("SELECT output FROM commit_log WHERE input_raw='nihao'"),
              "你好");

    // 单行表已 seed
    EXPECT_EQ(query_int("SELECT count(*) FROM device_link"), 1);
    EXPECT_EQ(query_int("SELECT count(*) FROM sync_cursor"), 1);
    EXPECT_EQ(query_int("SELECT count(*) FROM backfill_state"), 1);
}

TEST_F(SchemaV2Fixture, V1ToV2IdempotentReinit) {
    ASSERT_TRUE(exec(std::string{ektro::kSchemaV1}));
    ASSERT_TRUE(exec("PRAGMA user_version = 1"));

    EXPECT_EQ(ektro::init_db(db), 2);
    // 再 init — 不报错,仍 v2
    EXPECT_EQ(ektro::init_db(db), 2);
}

// ─────────── 单行 CHECK 约束 ───────────

TEST_F(SchemaV2Fixture, DeviceLinkRejectsSecondRow) {
    ektro::init_db(db);
    // id=2 违反 CHECK(id=1)
    EXPECT_FALSE(exec(
        "INSERT INTO device_link (id, device_id, created_at) "
        "VALUES (2, 'dup-id', 1700000000000)"));
}

TEST_F(SchemaV2Fixture, SyncCursorRejectsSecondRow) {
    ektro::init_db(db);
    EXPECT_FALSE(exec("INSERT INTO sync_cursor (id) VALUES (2)"));
}

TEST_F(SchemaV2Fixture, BackfillStateRejectsSecondRow) {
    ektro::init_db(db);
    EXPECT_FALSE(exec("INSERT INTO backfill_state (id) VALUES (2)"));
}

// ─────────── 链接状态语义 ───────────

TEST_F(SchemaV2Fixture, LinkThenRevoke) {
    ektro::init_db(db);

    // 初始未链接
    EXPECT_EQ(query_int("SELECT linked_user_id IS NULL FROM device_link WHERE id=1"), 1);

    // 链接
    ASSERT_TRUE(exec(
        "UPDATE device_link SET linked_user_id='user-1', "
        "linked_user_handle='@testuser', linked_at=1700000001000 WHERE id=1"));
    EXPECT_EQ(query_text("SELECT linked_user_id FROM device_link WHERE id=1"), "user-1");

    // 解绑
    ASSERT_TRUE(exec(
        "UPDATE device_link SET linked_user_id=NULL, linked_user_handle=NULL, "
        "revoked_at=1700000002000 WHERE id=1"));
    EXPECT_EQ(query_int("SELECT linked_user_id IS NULL FROM device_link WHERE id=1"), 1);
    EXPECT_EQ(query_int64("SELECT revoked_at FROM device_link WHERE id=1"), 1700000002000LL);
}

// ─────────── 与 Python 端跨语言一致性 ───────────

TEST_F(SchemaV2Fixture, CrossLangConsistencyWithPython) {
    // C++ kCurrentSchemaVersion 必须与 Python CURRENT_SCHEMA_VERSION 一致 (都是 2)
    EXPECT_EQ(ektro::kCurrentSchemaVersion, 2);

    ektro::init_db(db);
    // sqlite_master 里的 device_link DDL 应含 Python schema.py 同样的列
    std::string ddl = query_text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='device_link'");
    EXPECT_NE(ddl.find("linked_user_id"), std::string::npos);
    EXPECT_NE(ddl.find("ektro_endpoint"), std::string::npos);
    EXPECT_NE(ddl.find("CHECK (id = 1)"), std::string::npos);
}
