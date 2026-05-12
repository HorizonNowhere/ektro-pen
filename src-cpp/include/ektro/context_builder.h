// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/shared/context_builder.py

#pragma once

#include "ektro/memory_view.h"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace ektro {

struct ContextOptions {
    std::size_t max_chars = 80;
    std::size_t n_recent_commits = 5;
    int within_minutes = 60;
    bool same_app_only = false;
};

// 构造重排/预测用的上下文字符串
std::string build_context(
    const MemoryView& store,
    std::string_view cursor_prefix = "",
    std::optional<std::string_view> current_app = std::nullopt,
    const ContextOptions& opts = {}
);

// 专为 Predictor 设计 (D-004: 50 字符边界)
std::string build_predictor_prompt(
    const MemoryView& store,
    std::string_view cursor_prefix = "",
    std::optional<std::string_view> current_app = std::nullopt,
    std::size_t max_chars = 50
);

}  // namespace ektro
