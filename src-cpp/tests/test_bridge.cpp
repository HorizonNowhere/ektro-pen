// SPDX-License-Identifier: Apache-2.0
// D-016 Cycle-2 准入: ektro_bridge facade 契约测试。
// 覆盖 pr-test-analyzer 指认的最高价值缺口: permutation 合法性 /
// round-trip / 直通分支 / noexcept 安全 —— 正是 _EktroApplyRerank
// 依赖、且会触发"选词键错位"类回归的精确契约。
//
// 注意: ektro_bridge 是进程级 singleton (一次性 tried flag), 故全部
// 断言放在单个 TEST 内按序执行, 不依赖 GoogleTest 跨用例顺序。

#include <filesystem>
#include <string>
#include <system_error>
#include <vector>

#include <gtest/gtest.h>

#include "ektro/bridge.h"

namespace fs = std::filesystem;

namespace {

// 校验: 要么空(直通, 合法), 要么是 [0,n) 的完整双射排列。
// 这正是 RimeWithWeasel::_EktroApplyRerank 依赖的硬契约
// (best=order[0] 喂给 librime highlight; 非法排列→错高亮/越界)。
void ExpectEmptyOrValidPermutation(const std::vector<int>& order,
                                   std::size_t n) {
    if (order.empty())
        return;  // 直通: 合法
    ASSERT_EQ(order.size(), n) << "非空必须恰好 n 个 (调用方依赖 size==n)";
    std::vector<bool> seen(n, false);
    for (int v : order) {
        ASSERT_GE(v, 0);
        ASSERT_LT(v, static_cast<int>(n)) << "索引越界 → librime 高亮越界";
        ASSERT_FALSE(seen[v]) << "重复索引 " << v << " → 双射破坏";
        seen[v] = true;
    }
    for (std::size_t i = 0; i < n; ++i)
        ASSERT_TRUE(seen[i]) << "缺索引 " << i << " → 候选丢失";
}

}  // namespace

TEST(EktroBridge, FacadeContract) {
    using namespace ektro_bridge;

    const std::vector<std::string> cands = {"你好", "拟好", "泥嚎", "你嚎"};

    // 1. 未 init: reranker 为空 → 必直通(空), noexcept 不抛。
    EXPECT_TRUE(rerank_order(cands, "", {}).empty())
        << "未初始化引擎必须 passthrough";

    // 2. init 到临时 db (noexcept; std::filesystem 异常已在内部吞+log)。
    std::error_code ec;
    const fs::path db = fs::temp_directory_path() / "ektro_bridge_test.db";
    fs::remove(db, ec);
    init(db.string());

    // 3. <2 候选: 合法 no-op (非故障) → 空。
    EXPECT_TRUE(rerank_order({"你好"}, "", {}).empty());
    EXPECT_TRUE(rerank_order({}, "", {}).empty());

    // 4. N 候选: 空(直通) 或 [0,N) 完整合法排列。
    ExpectEmptyOrValidPermutation(rerank_order(cands, "", {}), cands.size());

    // 5. log_commit: noexcept, 多次/空串/中文均不崩不抛。
    for (int i = 0; i < 10; ++i)
        log_commit("你好世界");
    log_commit("");
    log_commit("测试中文输入");

    // 6. 学习后 rerank 仍守契约 (round-trip: 排列性质不随状态破坏)。
    ExpectEmptyOrValidPermutation(rerank_order(cands, "", {}), cands.size());

    // 7. init 幂等: 再调安全 (tried flag 早返回)。
    init(db.string());
    ExpectEmptyOrValidPermutation(rerank_order(cands, "", {}), cands.size());

    fs::remove(db, ec);
}
