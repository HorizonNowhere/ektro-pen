// SPDX-License-Identifier: Apache-2.0
// 对应 Python: BaselinePredictor 等价测试

#include <filesystem>
#include <string>
#include <utility>
#include <vector>

#include <gtest/gtest.h>

#include "ektro/memory_store.h"
#include "ektro/predictor.h"

namespace fs = std::filesystem;
using ektro::EktroMemoryStore;
using ektro::BaselinePredictor;
using ektro::PredictionErrorKind;

TEST(BaselinePredictor, EmptyPrefixReturnsEmpty) {
    auto tmp = fs::temp_directory_path() / "ektro_pred_empty.db";
    std::error_code ec;
    fs::remove(tmp, ec);
    {
        EktroMemoryStore store(tmp.string());
        BaselinePredictor pred(store);
        auto r = pred.predict("");
        EXPECT_EQ(r.error_kind, PredictionErrorKind::kEmpty);
        EXPECT_TRUE(r.text.empty());
    }  // store 析构释放 sqlite 句柄
    fs::remove(tmp, ec);
    fs::remove(fs::path(tmp.string() + "-wal"), ec);
    fs::remove(fs::path(tmp.string() + "-shm"), ec);
}

TEST(BaselinePredictor, ChainContinuation) {
    auto tmp = fs::temp_directory_path() / "ektro_pred_chain.db";
    std::error_code ec;
    fs::remove(tmp, ec);
    {
        EktroMemoryStore store(tmp.string());

        // 用户写过: 你好 / 好的 / 的什 / 什么
        for (auto [i, o] : std::vector<std::pair<std::string, std::string>>{
                {"a", "你好"}, {"b", "好的"}, {"c", "的什"}, {"d", "什么"}}) {
            EktroMemoryStore::LogCommitArgs a;
            a.input_raw = i;
            a.output = o;
            store.log_commit(a);
        }
        BaselinePredictor pred(store);
        auto r = pred.predict("你");
        EXPECT_EQ(r.error_kind, PredictionErrorKind::kOk);
        EXPECT_FALSE(r.text.empty());
        EXPECT_TRUE(r.text.size() >= 3);
        EXPECT_EQ(r.text.substr(0, 3), "好");
    }
    fs::remove(tmp, ec);
    fs::remove(fs::path(tmp.string() + "-wal"), ec);
    fs::remove(fs::path(tmp.string() + "-shm"), ec);
}
