// SPDX-License-Identifier: Apache-2.0
// EKTRO Cycle 2 C++ 移植骨架 (D-011 GO 后第一份)
//
// 对应 Python: src/shared/protocols.py MemoryView
// 实现: src-cpp/src/memory_store.cpp (待写)
//
// C++ 移植规约 (来自 D-005 / D-009 / D-011):
//   - 字段名 1:1 对应 Python
//   - 错误信号用 enum class (不用 magic int)
//   - phrase_pair 是 *字粒度* (不是词粒度)
//   - 隐私拦截唯一权威: is_password_field (TSF IS_PASSWORD)

#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace ektro {

// ────────── 对应 Python CommitRecord ──────────

struct CommitRecord {
    std::int64_t id;
    std::int64_t timestamp_ms;
    std::string input_raw;
    std::string output;
    std::optional<std::string> app_name;
    std::optional<std::int64_t> context_id;
    bool user_picked;
    std::optional<std::int32_t> duration_ms;
};

// ────────── 对应 Python WordFreq ──────────

struct WordFreq {
    std::string word;
    std::int64_t count;
    std::int64_t last_used_ms;
};

// ────────── MemoryView (只读接口，对应 Python Protocol) ──────────
//
// Reranker / Predictor 仅依赖此接口，不依赖具体 MemoryStore 类。
// 这是接口隔离 (ISP) 的工程现实。

class MemoryView {
public:
    virtual ~MemoryView() = default;

    // 最近 N 条 commit（按时间倒排）
    virtual std::vector<CommitRecord> recent_outputs(
        std::size_t limit = 20,
        std::optional<std::int64_t> since_ms = std::nullopt
    ) const = 0;

    // 批量查字频。未出现的字 count=0
    virtual std::unordered_map<std::string, std::int64_t> word_freq_lookup(
        std::span<const std::string> words
    ) const = 0;

    // 单字 prev → [(curr, count)] 按 count 倒排
    virtual std::vector<std::pair<std::string, std::int64_t>> phrase_pair_lookup(
        std::string_view prev
    ) const = 0;
};

// ────────── BatchMemoryView (可选扩展接口) ──────────

class BatchMemoryView : public MemoryView {
public:
    // 批量查多个 prev → {prev: [(curr, count)]}
    // D-011 P1.3: reranker 用此消 N+1 查询
    virtual std::unordered_map<
        std::string,
        std::vector<std::pair<std::string, std::int64_t>>
    > phrase_pair_batch_lookup(
        std::span<const std::string> prev_chars
    ) const = 0;
};

}  // namespace ektro
