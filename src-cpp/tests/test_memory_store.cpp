// SPDX-License-Identifier: Apache-2.0
// C++ 对照测试: 与 tests/memory/test_store.py 同样的 case
// 跑通后 cross-check 行为一致 (PORTING_GUIDE §5)

#include <filesystem>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

#include "ektro/memory_store.h"
#include "ektro/log_result.h"

namespace fs = std::filesystem;
using ektro::EktroMemoryStore;
using ektro::LogResult;

class StoreFixture : public ::testing::Test {
protected:
    fs::path tmp_db;
    std::unique_ptr<EktroMemoryStore> store;

    void SetUp() override {
        tmp_db = fs::temp_directory_path() / ("ektro_test_" +
                  std::to_string(std::hash<std::thread::id>{}(std::this_thread::get_id())) +
                  ".db");
        if (fs::exists(tmp_db)) fs::remove(tmp_db);
        store = std::make_unique<EktroMemoryStore>(tmp_db.string());
    }

    void TearDown() override {
        store.reset();
        std::error_code ec;
        for (auto suf : {"", "-wal", "-shm"}) {
            fs::remove(fs::path(tmp_db.string() + suf), ec);
        }
    }
};

// ─── Schema ───

TEST_F(StoreFixture, InitCreatesTablesEmpty) {
    auto s = store->stats();
    EXPECT_EQ(s.total_commits, 0);
}

// ─── LogCommit ───

TEST_F(StoreFixture, BasicCommit) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "nihao";
    a.output = "你好";
    a.app_name = "Code.exe";
    auto outcome = store->log_commit(a);
    EXPECT_EQ(outcome.result, LogResult::kCommitted);
    EXPECT_TRUE(outcome.row_id.has_value());

    auto recent = store->recent_outputs(10);
    ASSERT_EQ(recent.size(), 1);
    EXPECT_EQ(recent[0].output, "你好");
}

TEST_F(StoreFixture, WordFreqUpdated) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "nihao";
    a.output = "你好";
    store->log_commit(a);
    a.output = "你好世界";
    store->log_commit(a);

    std::vector<std::string> q = {"你", "好", "世", "界", "不存在"};
    auto wf = store->word_freq_lookup(q);
    EXPECT_EQ(wf["你"], 2);
    EXPECT_EQ(wf["好"], 2);
    EXPECT_EQ(wf["世"], 1);
    EXPECT_EQ(wf["界"], 1);
    EXPECT_EQ(wf["不存在"], 0);
}

TEST_F(StoreFixture, PhrasePairUpdated) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "nihao";
    a.output = "你好";
    store->log_commit(a);
    auto p1 = store->phrase_pair_lookup("你");
    ASSERT_EQ(p1.size(), 1);
    EXPECT_EQ(p1[0].first, "好");
    EXPECT_EQ(p1[0].second, 1);

    store->log_commit(a);
    auto p2 = store->phrase_pair_lookup("你");
    ASSERT_EQ(p2.size(), 1);
    EXPECT_EQ(p2[0].second, 2);
}

// ─── 隐私拦截 (D-009 P0.1) ───

TEST_F(StoreFixture, PasswordFieldRejected) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "pwd";
    a.output = "MySecret123";
    a.is_password_field = true;
    auto o = store->log_commit(a);
    EXPECT_EQ(o.result, LogResult::kSkippedPassword);
    EXPECT_EQ(store->stats().total_commits, 0);
}

TEST_F(StoreFixture, BankcardInInputRawRejected) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "6222021234567890123";  // 19 位连续数字
    a.output = "卡号";
    a.app_name = "A";
    auto o = store->log_commit(a);
    EXPECT_EQ(o.result, LogResult::kSkippedSensitive);
}

TEST_F(StoreFixture, IdCardInInputRawRejected) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "110101199001011234";
    a.output = "id";
    a.app_name = "A";
    EXPECT_EQ(store->log_commit(a).result, LogResult::kSkippedSensitive);
}

TEST_F(StoreFixture, ChineseOutputWithNumbersNotRejected) {
    // 关键回归: V60 滤杯 / 编号 / 价格不被误杀
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "v60lvbei";
    a.output = "V60 滤杯";
    a.app_name = "A";
    EXPECT_EQ(store->log_commit(a).result, LogResult::kCommitted);
}

TEST_F(StoreFixture, ExcludedAppRejected) {
    store->add_excluded_app("BankApp.exe", "财务");
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "nihao";
    a.output = "你好";
    a.app_name = "BankApp.exe";
    EXPECT_EQ(store->log_commit(a).result, LogResult::kSkippedApp);

    a.app_name = "Notepad.exe";
    EXPECT_EQ(store->log_commit(a).result, LogResult::kCommitted);
}

// ─── 边界 (D-009 P1.6) ───

TEST_F(StoreFixture, EmojiInOutput) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "biaoqing";
    a.output = "😀好心情";
    EXPECT_EQ(store->log_commit(a).result, LogResult::kCommitted);
}

TEST_F(StoreFixture, EmptyOutputCommitted) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "x";
    a.output = "";
    EXPECT_EQ(store->log_commit(a).result, LogResult::kCommitted);
    EXPECT_EQ(store->stats().unique_chars, 0);
}

TEST_F(StoreFixture, SqlInjectionInOutput) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "x";
    a.output = "你好'; DROP TABLE commit_log; --";
    EXPECT_EQ(store->log_commit(a).result, LogResult::kCommitted);
    EXPECT_EQ(store->stats().total_commits, 1);
}

// ─── 维护 ───

TEST_F(StoreFixture, ClearAllRequiresConfirm) {
    EXPECT_THROW(store->clear_all(false), std::invalid_argument);
}

TEST_F(StoreFixture, ClearAllWithConfirm) {
    EktroMemoryStore::LogCommitArgs a;
    a.input_raw = "x";
    a.output = "你好";
    store->log_commit(a);
    store->clear_all(true);
    EXPECT_EQ(store->stats().total_commits, 0);
}

// ─── 并发 (D-009 P0.6) ───

TEST_F(StoreFixture, ConcurrentWriteRead) {
    constexpr int N_WRITES = 500;
    constexpr int N_WRITERS = 2;
    std::vector<std::thread> threads;
    for (int t = 0; t < N_WRITERS; ++t) {
        threads.emplace_back([this, t]() {
            for (int i = 0; i < N_WRITES; ++i) {
                EktroMemoryStore::LogCommitArgs a;
                a.input_raw = "x";
                a.output = "啊你";  // 2 字, 让 phrase_pair 起作用
                a.app_name = std::string("T") + std::to_string(t);
                auto o = store->log_commit(a);
                ASSERT_EQ(o.result, LogResult::kCommitted);
            }
        });
    }
    for (auto& th : threads) th.join();

    EXPECT_EQ(store->stats().total_commits, N_WRITES * N_WRITERS);
    std::vector<std::string> q = {"啊", "你"};
    auto wf = store->word_freq_lookup(q);
    EXPECT_EQ(wf["啊"], N_WRITES * N_WRITERS);
    EXPECT_EQ(wf["你"], N_WRITES * N_WRITERS);

    // 关键: phrase_pair (啊→你) 一致性 (D-011 P0.5.4)
    auto pairs = store->phrase_pair_lookup("啊");
    int64_t pair_count = 0;
    for (auto& [c, n] : pairs) if (c == "你") { pair_count = n; break; }
    EXPECT_EQ(pair_count, N_WRITES * N_WRITERS);
}
