// SPDX-License-Identifier: Apache-2.0
// 对照 tests/rerank/test_baseline.py 强断言

#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "ektro/memory_store.h"
#include "ektro/reranker.h"

namespace fs = std::filesystem;
using ektro::EktroMemoryStore;
using ektro::BaselineReranker;

class RerankFixture : public ::testing::Test {
protected:
    fs::path tmp_db;
    std::unique_ptr<EktroMemoryStore> store;
    std::unique_ptr<BaselineReranker> reranker;

    void SetUp() override {
        tmp_db = fs::temp_directory_path() / "ektro_rerank_test.db";
        if (fs::exists(tmp_db)) fs::remove(tmp_db);
        store = std::make_unique<EktroMemoryStore>(tmp_db.string());
        reranker = std::make_unique<BaselineReranker>(*store);
    }

    void TearDown() override {
        reranker.reset();
        store.reset();
        std::error_code ec;
        for (auto suf : {"", "-wal", "-shm"}) {
            fs::remove(fs::path(tmp_db.string() + suf), ec);
        }
    }

    void commit(std::string_view input, std::string_view output) {
        EktroMemoryStore::LogCommitArgs a;
        a.input_raw = input;
        a.output = output;
        store->log_commit(a);
    }
};

TEST_F(RerankFixture, EmptyCandidates) {
    std::vector<std::string> cands;
    auto r = reranker->rerank(cands);
    EXPECT_TRUE(r.empty());
}

TEST_F(RerankFixture, SingleCandidatePassThrough) {
    std::vector<std::string> cands = {"你好"};
    auto r = reranker->rerank(cands);
    ASSERT_EQ(r.size(), 1);
    EXPECT_EQ(r[0].candidate, "你好");
    EXPECT_EQ(r[0].new_rank, 0);
}

TEST_F(RerankFixture, RimePriorHoldsWhenNoUserData) {
    std::vector<std::string> cands = {"甲乙", "丙丁", "戊己"};
    auto r = reranker->rerank(cands, "无关上下文");
    ASSERT_EQ(r.size(), 3);
    EXPECT_EQ(r[0].candidate, "甲乙");
    EXPECT_EQ(r[1].candidate, "丙丁");
    EXPECT_EQ(r[2].candidate, "戊己");
}

TEST_F(RerankFixture, UserFreqAloneCanReorder) {
    for (int i = 0; i < 15; ++i) commit("kafei", "咖啡");
    std::vector<std::string> cands = {"非常", "啡"};
    auto r = reranker->rerank(cands, "");  // 无 context
    ASSERT_EQ(r.size(), 2);
    EXPECT_EQ(r[0].candidate, "啡");
}

TEST_F(RerankFixture, BigramReordersWithContext) {
    for (int i = 0; i < 10; ++i) commit("kafei", "咖啡");
    std::vector<std::string> cands = {"非常", "啡", "废铁"};
    auto r = reranker->rerank(cands, "我要买咖");
    ASSERT_EQ(r.size(), 3);
    EXPECT_EQ(r[0].candidate, "啡");
}
