// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/memory/store.py EktroMemoryStore
// D-009 P0/P1 + D-011 P0.5 全部修正后的最终接口

#pragma once

#include "ektro/log_result.h"
#include "ektro/memory_view.h"

#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>

struct sqlite3;

namespace ektro {

class EktroMemoryStore : public BatchMemoryView {
public:
    explicit EktroMemoryStore(const std::string& db_path);
    ~EktroMemoryStore() override;

    // 不可拷贝（含 sqlite3* 资源）
    EktroMemoryStore(const EktroMemoryStore&) = delete;
    EktroMemoryStore& operator=(const EktroMemoryStore&) = delete;

    // ─── 隐私拦截 (D-009 P0.1) ───
    // 注意: 检测对象是 input_raw, 不是 output (中文 commit)
    [[nodiscard]] bool is_sensitive_input(std::string_view input_raw) const;
    [[nodiscard]] bool is_excluded_app(std::optional<std::string_view> app_name) const;

    // ─── 写入 ───
    struct LogCommitArgs {
        std::string_view input_raw;
        std::string_view output;
        std::optional<std::string> app_name = std::nullopt;
        std::optional<std::int64_t> context_id = std::nullopt;
        bool user_picked = false;
        std::optional<std::int32_t> duration_ms = std::nullopt;
        bool is_password_field = false;
        std::optional<std::int64_t> timestamp_ms = std::nullopt;  // null=now
    };
    LogOutcome log_commit(const LogCommitArgs& args);

    // ─── 读 (MemoryView 实现) ───
    std::vector<CommitRecord> recent_outputs(
        std::size_t limit = 20,
        std::optional<std::int64_t> since_ms = std::nullopt
    ) const override;

    std::unordered_map<std::string, std::int64_t> word_freq_lookup(
        std::span<const std::string> words
    ) const override;

    std::vector<std::pair<std::string, std::int64_t>> phrase_pair_lookup(
        std::string_view prev
    ) const override;

    // ─── 批量读 (BatchMemoryView, D-011 P1.3) ───
    std::unordered_map<
        std::string,
        std::vector<std::pair<std::string, std::int64_t>>
    > phrase_pair_batch_lookup(
        std::span<const std::string> prev_chars
    ) const override;

    // ─── 隐私管理 ───
    void add_excluded_app(std::string_view app_name, std::string_view reason = "");
    void remove_excluded_app(std::string_view app_name);
    std::vector<std::tuple<std::string, std::string, std::int64_t>>
        list_excluded_apps() const;

    // ─── 配置 ───
    std::optional<std::string> get_config(std::string_view key) const;
    void set_config(std::string_view key, std::string_view value);

    // ─── 维护 ───
    struct Stats {
        std::int64_t total_commits;
        std::int64_t unique_chars;
        std::int64_t unique_phrase_pairs;
        std::optional<std::int64_t> first_commit_ms;
        std::optional<std::int64_t> last_commit_ms;
        std::int64_t db_size_bytes;
    };
    Stats stats() const;

    void clear_all(bool confirm = false);

private:
    void update_word_freq_locked_(std::string_view output, std::int64_t ts);
    void update_phrase_pairs_locked_(std::string_view output);

    std::string db_path_;
    sqlite3* conn_ = nullptr;
    mutable std::mutex mu_;
};

}  // namespace ektro
