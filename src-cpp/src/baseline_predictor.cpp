// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/predictor/baseline.py BaselinePredictor
//
// 贪心 phrase_pair 链续写

#include "ektro/predictor.h"

#include <chrono>
#include <set>
#include <string>

namespace ektro {

namespace {

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

inline double now_ms_dbl() {
    using namespace std::chrono;
    return duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count() / 1000.0;
}

}  // anonymous namespace

PredictionResult BaselinePredictor::predict(std::string_view cursor_prefix) {
    return predict_with(cursor_prefix, Options{});
}

PredictionResult BaselinePredictor::predict_with(std::string_view cursor_prefix, Options opts) {
    double start = now_ms_dbl();
    PredictionResult r{};

    if (cursor_prefix.empty()) {
        r.error_kind = PredictionErrorKind::kEmpty;
        r.total_ms = now_ms_dbl() - start;
        return r;
    }

    auto pchars = utf8_chars(cursor_prefix);
    std::string curr = pchars.back();
    std::string out;
    std::set<std::string> visited;

    for (std::size_t i = 0; i < opts.max_chars; ++i) {
        auto pairs = store_.phrase_pair_lookup(curr);
        std::string chosen;
        for (const auto& [next_char, count] : pairs) {
            if (count < opts.min_count) break;
            if (visited.count(next_char)) continue;
            chosen = next_char;
            break;
        }
        if (chosen.empty()) break;
        out += chosen;
        visited.insert(chosen);
        curr = chosen;
    }

    r.text = std::move(out);
    r.total_ms = now_ms_dbl() - start;
    r.error_kind = r.text.empty() ? PredictionErrorKind::kEmpty : PredictionErrorKind::kOk;
    return r;
}

}  // namespace ektro
