// SPDX-License-Identifier: Apache-2.0
// 对应 Python:
//   - src/predictor/client.py PredictorClient + PredictionResult
//   - src/predictor/trigger.py AsyncTrigger
//   - src/predictor/baseline.py BaselinePredictor
//   - src/shared/protocols.py BasePredictor
// D-009 P0.5 + D-011 P0.5.6

#pragma once

#include "memory_view.h"

#include <chrono>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <string_view>

namespace ektro {

// ────────── PredictionErrorKind ──────────

enum class PredictionErrorKind {
    kOk = 0,
    kEmpty,
    kTimeout,
    kServerDown,
    kHttpError,
    kParseError,
    kUnknown,
};

constexpr bool is_ok_kind(PredictionErrorKind k) noexcept {
    return k == PredictionErrorKind::kOk;
}

constexpr bool is_retryable_kind(PredictionErrorKind k) noexcept {
    return k == PredictionErrorKind::kTimeout
        || k == PredictionErrorKind::kServerDown;
}

// ────────── PredictionResult ──────────

struct PredictionResult {
    std::string text;
    std::optional<std::int64_t> prompt_tokens = std::nullopt;
    std::optional<double> prefill_ms = std::nullopt;
    double total_ms = 0.0;
    bool cache_hit = false;
    PredictionErrorKind error_kind = PredictionErrorKind::kOk;
    std::optional<std::string> error_detail = std::nullopt;

    [[nodiscard]] bool is_ok() const noexcept {
        return error_kind == PredictionErrorKind::kOk && !text.empty();
    }
};

// ────────── BasePredictor 统一接口 ──────────
// D-011 P0.5.6: BaselinePredictor 与 PredictorClient 都返 PredictionResult

class BasePredictor {
public:
    virtual ~BasePredictor() = default;

    virtual PredictionResult predict(std::string_view cursor_prefix) = 0;
};

// ────────── PredictorClient (HTTP 到 llama-server) ──────────

struct PredictorConfig {
    std::string server_url = "http://127.0.0.1:8088";
    int timeout_ms = 200;
    std::size_t max_context_chars = 50;
    int n_predict = 8;
    double temperature = 0.3;
    int top_k = 40;
    std::size_t cache_capacity = 128;
};

class PredictorClient : public BasePredictor {
public:
    explicit PredictorClient(PredictorConfig cfg = {});

    [[nodiscard]] bool health() const;
    PredictionResult predict(std::string_view cursor_prefix) override;
    void clear_cache();
    [[nodiscard]] std::size_t cache_size() const;

private:
    PredictorConfig cfg_;
    // 实现内部: cpp-httplib client + LRU cache
};

// ────────── BaselinePredictor (phrase_pair 链) ──────────

class BaselinePredictor : public BasePredictor {
public:
    explicit BaselinePredictor(MemoryView& store) : store_(store) {}

    PredictionResult predict(std::string_view cursor_prefix) override;

    struct Options {
        std::size_t max_chars = 8;
        std::int64_t min_count = 1;
    };
    PredictionResult predict_with(std::string_view cursor_prefix, Options opts);

private:
    MemoryView& store_;
};

// ────────── AsyncTrigger ──────────

struct TriggerConfig {
    int pause_ms = 300;
    int poll_interval_ms = 30;
    std::size_t debounce_min_chars = 2;
};

class AsyncTrigger {
public:
    using ResultCallback = std::function<void(const PredictionResult&)>;

    AsyncTrigger(BasePredictor& predictor,
                 ResultCallback on_result,
                 TriggerConfig cfg = {});
    ~AsyncTrigger();

    void start();
    void stop();
    void on_keystroke(std::string_view prefix, std::string_view context = "");
    void cancel();

private:
    BasePredictor& predictor_;
    ResultCallback on_result_;
    TriggerConfig cfg_;
    // 实现内部: std::thread + std::condition_variable + std::atomic<uint64_t> task_id
    // D-011 P1.1: task_id 必须 std::atomic<uint64_t> 单调递增（不是浮点 perf_counter）
};

}  // namespace ektro
