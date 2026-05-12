// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/memory/store.py LogResult / LogOutcome
// D-009 P0.4 + D-011 P0.5.5

#pragma once

#include <cstdint>
#include <optional>
#include <string>

namespace ektro {

enum class LogResult {
    kCommitted = 0,
    kSkippedPassword,
    kSkippedSensitive,
    kSkippedApp,
    kDbError,
};

constexpr bool is_committed(LogResult r) noexcept {
    return r == LogResult::kCommitted;
}

constexpr bool is_skipped(LogResult r) noexcept {
    return r == LogResult::kSkippedPassword
        || r == LogResult::kSkippedSensitive
        || r == LogResult::kSkippedApp;
}

struct LogOutcome {
    LogResult result;
    std::optional<std::int64_t> row_id = std::nullopt;
    std::optional<std::string> error_detail = std::nullopt;
};

}  // namespace ektro
