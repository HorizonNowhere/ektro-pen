// SPDX-License-Identifier: Apache-2.0
#include "ektro/context_builder.h"

#include <algorithm>
#include <chrono>
#include <vector>

namespace ektro {

namespace {

// UTF-8 字符切分（与 memory_store.cpp 一致）
std::vector<std::string_view> utf8_views(std::string_view s) {
    std::vector<std::string_view> out;
    for (std::size_t i = 0; i < s.size();) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        std::size_t len = 1;
        if ((c & 0x80) == 0)        len = 1;
        else if ((c & 0xE0) == 0xC0) len = 2;
        else if ((c & 0xF0) == 0xE0) len = 3;
        else if ((c & 0xF8) == 0xF0) len = 4;
        if (i + len > s.size()) len = s.size() - i;
        out.push_back(s.substr(i, len));
        i += len;
    }
    return out;
}

std::string truncate_to_chars(std::string_view s, std::size_t max_chars) {
    auto chars = utf8_views(s);
    if (chars.size() <= max_chars) return std::string{s};
    std::string out;
    for (std::size_t i = chars.size() - max_chars; i < chars.size(); ++i) {
        out += std::string{chars[i]};
    }
    return out;
}

inline std::int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

}  // anonymous namespace

std::string build_context(
    const MemoryView& store,
    std::string_view cursor_prefix,
    std::optional<std::string_view> current_app,
    const ContextOptions& opts
) {
    std::string historical;
    std::int64_t since_ms = now_ms() - static_cast<std::int64_t>(opts.within_minutes) * 60'000;
    auto records = store.recent_outputs(opts.n_recent_commits, since_ms);

    // records 是 DESC（最近在前）；要从远到近拼接
    if (opts.same_app_only && current_app && !current_app->empty()) {
        records.erase(
            std::remove_if(records.begin(), records.end(),
                [&](const CommitRecord& r) {
                    return !r.app_name || *r.app_name != *current_app;
                }),
            records.end());
    }

    for (auto it = records.rbegin(); it != records.rend(); ++it) {
        historical += it->output;
    }

    std::string full = historical;
    if (!historical.empty()) full += ' ';
    full += std::string{cursor_prefix};

    return truncate_to_chars(full, opts.max_chars);
}

std::string build_predictor_prompt(
    const MemoryView& store,
    std::string_view cursor_prefix,
    std::optional<std::string_view> current_app,
    std::size_t max_chars
) {
    ContextOptions opts;
    opts.max_chars = max_chars;
    opts.n_recent_commits = 2;     // D-004: 最近 1-2 条
    opts.within_minutes = 10;      // 短时记忆
    opts.same_app_only = false;
    return build_context(store, cursor_prefix, current_app, opts);
}

}  // namespace ektro
