// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/rerank/baseline.py EktroBaselineReranker
//
// 4 特征加权:
//   F1 user_freq, F2 bigram, F3 context_overlap, F4 rime_prior
// D-011 P0.5.5: BatchMemoryView 用于消 N+1 查询

#include "ektro/reranker.h"

#include <algorithm>
#include <cmath>
#include <set>
#include <span>

namespace ektro {

namespace {

// UTF-8 字符迭代（重复 memory_store.cpp 的逻辑，未来抽 utils.h）
std::vector<std::string> utf8_chars(std::string_view s) {
    std::vector<std::string> out;
    for (std::size_t i = 0; i < s.size();) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        std::size_t len = 1;
        if ((c & 0x80) == 0)        len = 1;
        else if ((c & 0xE0) == 0xC0) len = 2;
        else if ((c & 0xF0) == 0xE0) len = 3;
        else if ((c & 0xF8) == 0xF0) len = 4;
        if (i + len > s.size()) len = s.size() - i;
        out.emplace_back(s.substr(i, len));
        i += len;
    }
    return out;
}

inline std::string last_utf8_char(std::string_view s) {
    auto chars = utf8_chars(s);
    return chars.empty() ? std::string{} : chars.back();
}

inline std::string first_utf8_char(std::string_view s) {
    auto chars = utf8_chars(s);
    return chars.empty() ? std::string{} : chars.front();
}

}  // anonymous namespace

BaselineReranker::BaselineReranker(MemoryView& store, RerankerConfig cfg)
    : store_(store), cfg_(cfg) {}

std::vector<RankedCandidate> BaselineReranker::rerank(
    std::span<const std::string> candidates,
    std::string_view context,
    std::span<const std::string> /* recent_outputs */
) {
    std::vector<RankedCandidate> result;
    if (candidates.empty()) return result;
    if (candidates.size() == 1) {
        return {{std::string{candidates[0]}, 1.0, 0, 0}};
    }

    // 限制 context 长度（按 UTF-8 字符截尾）
    // D-013 修复 Day 2 UB: 用 owned std::string 而非临时 string_view
    std::string context_owned;
    {
        auto cchars = utf8_chars(context);
        if (cchars.size() > cfg_.max_context_chars) {
            for (std::size_t i = cchars.size() - cfg_.max_context_chars; i < cchars.size(); ++i)
                context_owned += cchars[i];
            context = context_owned;  // 现在引用 owned string, 函数全程有效
        }
    }

    // 收集所有候选字符做字频批查
    std::set<std::string> all_chars;
    for (const auto& cand : candidates) {
        for (const auto& ch : utf8_chars(cand)) all_chars.insert(ch);
    }
    std::vector<std::string> chars_vec(all_chars.begin(), all_chars.end());
    auto freq_table = store_.word_freq_lookup(chars_vec);

    // 二元组批查（D-011 P0.5.5）
    std::string prev_char = last_utf8_char(context);
    std::unordered_map<std::string, std::vector<std::pair<std::string, std::int64_t>>> pair_table;
    if (!prev_char.empty()) {
        // 尝试用 BatchMemoryView; 退回到单次查询
        if (auto* batch = dynamic_cast<BatchMemoryView*>(&store_)) {
            std::vector<std::string> prevs = {prev_char};
            pair_table = batch->phrase_pair_batch_lookup(prevs);
        } else {
            pair_table[prev_char] = store_.phrase_pair_lookup(prev_char);
        }
    }

    // 算分
    struct Tmp { std::string cand; double score; std::size_t base_rank; };
    std::vector<Tmp> tmp;
    tmp.reserve(candidates.size());

    for (std::size_t i = 0; i < candidates.size(); ++i) {
        const auto& cand = candidates[i];
        double score = 0.0;

        // F1 user_freq (log + smoothing)
        auto cand_chars = utf8_chars(cand);
        double uf = 0.0;
        if (!cand_chars.empty()) {
            for (const auto& ch : cand_chars) {
                auto it = freq_table.find(ch);
                std::int64_t cnt = (it != freq_table.end()) ? it->second : 0;
                uf += std::log(static_cast<double>(cnt) + cfg_.smoothing);
            }
            uf /= static_cast<double>(cand_chars.size());
        }
        score += cfg_.w_user_freq * uf;

        // F2 bigram (context末字 → cand 首字)
        if (!prev_char.empty() && !cand_chars.empty()) {
            const std::string& curr_char = cand_chars.front();
            std::int64_t pair_count = 0;
            auto it = pair_table.find(prev_char);
            if (it != pair_table.end()) {
                for (const auto& [c, cnt] : it->second) {
                    if (c == curr_char) { pair_count = cnt; break; }
                }
            }
            score += cfg_.w_bigram *
                     std::log(static_cast<double>(pair_count) + cfg_.smoothing);
        }

        // F3 context_overlap (cand 中有多少字出现在 context recent)
        // 简化: 用 context 自身做近似（生产实现应该传 recent_outputs span）
        if (!cand_chars.empty() && !context.empty()) {
            int hit = 0;
            for (const auto& ch : cand_chars) {
                if (context.find(ch) != std::string_view::npos) ++hit;
            }
            score += cfg_.w_context_overlap *
                     (static_cast<double>(hit) / cand_chars.size());
        }

        // F4 rime_prior (exponential decay)
        score += cfg_.w_rime_prior * std::exp(-static_cast<double>(i) / 5.0);

        tmp.push_back({cand, score, i});
    }

    // 按 score 降序
    std::stable_sort(tmp.begin(), tmp.end(),
        [](const Tmp& a, const Tmp& b) { return a.score > b.score; });

    result.reserve(tmp.size());
    for (std::size_t new_rank = 0; new_rank < tmp.size(); ++new_rank) {
        result.push_back({tmp[new_rank].cand, tmp[new_rank].score,
                          tmp[new_rank].base_rank, new_rank});
    }
    return result;
}

}  // namespace ektro
