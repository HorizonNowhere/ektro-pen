// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/shared/protocols.py Reranker
//            + src/rerank/baseline.py EktroBaselineReranker
// D-009 P1.5 + D-011 P0.5.5

#pragma once

#include "memory_view.h"

#include <span>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace ektro {

// ────────── 对应 Python RankedCandidate ──────────

struct RankedCandidate {
    std::string candidate;
    double score;
    std::size_t base_rank;
    std::size_t new_rank;
    // C++ 端不必带 features dict（D-007: debug 用 Python 跑即可）
};

// ────────── Reranker 接口 ──────────

class Reranker {
public:
    virtual ~Reranker() = default;

    virtual std::vector<RankedCandidate> rerank(
        std::span<const std::string> candidates,
        std::string_view context = "",
        std::span<const std::string> recent_outputs = {}
    ) = 0;
};

// ────────── RerankerConfig ──────────
// 对应 Python: rerank/baseline.py RerankerConfig

struct RerankerConfig {
    double w_user_freq = 1.0;
    double w_bigram = 2.0;
    double w_context_overlap = 0.5;
    double w_rime_prior = 3.0;
    double smoothing = 1.0;
    std::size_t max_context_chars = 80;
};

// ────────── BaselineReranker 实现 ──────────
// 4 特征加权 reranker；实现见 src-cpp/src/baseline_reranker.cpp (待写)

class BaselineReranker : public Reranker {
public:
    BaselineReranker(MemoryView& store, RerankerConfig cfg = {});

    std::vector<RankedCandidate> rerank(
        std::span<const std::string> candidates,
        std::string_view context = "",
        std::span<const std::string> recent_outputs = {}
    ) override;

private:
    MemoryView& store_;
    RerankerConfig cfg_;
};

}  // namespace ektro
