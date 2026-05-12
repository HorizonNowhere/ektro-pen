// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/predictor/client.py PredictorClient
//
// 依赖:
//   - cpp-httplib (header-only, from https://github.com/yhirose/cpp-httplib)
//     放置: src-cpp/third_party/httplib.h
//   - nlohmann/json (header-only, https://github.com/nlohmann/json)
//     放置: src-cpp/third_party/nlohmann/json.hpp
//
// 如不能引入第三方库, 可用 WinHTTP API 重写 (Cycle 2 备份方案).
//
// 包含 D-009 P0/P1 全部修正:
//   - PredictionErrorKind enum 替代 string error
//   - isinstance(socket.timeout) 等价的 httplib::Error::Read 等分类
//   - LRU 缓存
//   - 质量门 (D-008 反退化 + D-010)

#include "ektro/predictor.h"
#include "ektro/log.h"

// 注: 这两行需要用户把 header 文件放对位置
// 如不可用, 临时改用 winsock 实现
#if __has_include(<httplib.h>)
#include <httplib.h>
#define EKTRO_HAS_HTTPLIB 1
#else
#define EKTRO_HAS_HTTPLIB 0
#endif

#if __has_include(<nlohmann/json.hpp>)
#include <nlohmann/json.hpp>
#define EKTRO_HAS_JSON 1
#else
#define EKTRO_HAS_JSON 0
#endif

#include <chrono>
#include <list>
#include <mutex>
#include <unordered_map>

namespace ektro {

#if !EKTRO_HAS_HTTPLIB || !EKTRO_HAS_JSON

// ─────────── Fallback 实现 (无第三方库时返回 ServerDown) ───────────
// 用户需:
//   1. 下 cpp-httplib (https://github.com/yhirose/cpp-httplib/blob/master/httplib.h)
//      放到 src-cpp/third_party/httplib.h
//   2. 下 nlohmann/json (https://github.com/nlohmann/json/releases)
//      放到 src-cpp/third_party/nlohmann/json.hpp
//   3. 在 CMakeLists.txt 加 target_include_directories(ektro PRIVATE third_party)

PredictorClient::PredictorClient(PredictorConfig cfg) : cfg_(std::move(cfg)) {
    EKTRO_LOG_WARN("predictor.client",
        "cpp-httplib or nlohmann/json missing; PredictorClient will always return ServerDown. "
        "See src-cpp/INSTALL.md for setup.");
}

bool PredictorClient::health() const { return false; }

PredictionResult PredictorClient::predict(std::string_view /*cursor_prefix*/) {
    PredictionResult r{};
    r.error_kind = PredictionErrorKind::kServerDown;
    r.error_detail = "httplib or nlohmann/json missing";
    return r;
}

void PredictorClient::clear_cache() {}
std::size_t PredictorClient::cache_size() const { return 0; }

#else

// ─────────── 真实现 (httplib + json 就绪) ───────────

namespace {

// LRU 缓存
class LruCache {
public:
    explicit LruCache(std::size_t capacity) : capacity_(capacity) {}

    std::optional<std::string> get(const std::string& key) {
        std::lock_guard<std::mutex> g(mu_);
        auto it = map_.find(key);
        if (it == map_.end()) return std::nullopt;
        list_.splice(list_.begin(), list_, it->second);
        return it->second->second;
    }

    void put(const std::string& key, const std::string& value) {
        std::lock_guard<std::mutex> g(mu_);
        auto it = map_.find(key);
        if (it != map_.end()) {
            it->second->second = value;
            list_.splice(list_.begin(), list_, it->second);
            return;
        }
        list_.emplace_front(key, value);
        map_[key] = list_.begin();
        while (list_.size() > capacity_) {
            map_.erase(list_.back().first);
            list_.pop_back();
        }
    }

    void clear() {
        std::lock_guard<std::mutex> g(mu_);
        list_.clear();
        map_.clear();
    }

    std::size_t size() const {
        std::lock_guard<std::mutex> g(mu_);
        return list_.size();
    }

private:
    std::size_t capacity_;
    mutable std::mutex mu_;
    std::list<std::pair<std::string, std::string>> list_;
    std::unordered_map<std::string,
        std::list<std::pair<std::string, std::string>>::iterator> map_;
};

// 质量门 (D-009 + D-010): 检测 ASCII 标点/数字/字母 等退化模式
bool is_low_quality(const std::string& text) {
    if (text.empty()) return true;
    if (text.size() < 2) return true;

    // 全相同字符 ("!!!", "AAA")
    bool all_same = true;
    for (std::size_t i = 1; i < text.size(); ++i) {
        if (text[i] != text[0]) { all_same = false; break; }
    }
    if (all_same && text.size() >= 3) return true;

    // 含 ASCII 数字 / 标点 / 字母（纯 CJK 续写应该不该有）
    for (unsigned char c : text) {
        if (c < 128) {  // 全 ASCII 字符判 low quality
            if ((c >= 0x21 && c <= 0x2F) || (c >= 0x3A && c <= 0x40) ||
                (c >= 0x5B && c <= 0x60) || (c >= 0x7B && c <= 0x7E) ||
                (c >= 0x30 && c <= 0x39) ||
                (c >= 0x41 && c <= 0x5A) || (c >= 0x61 && c <= 0x7A)) {
                return true;
            }
        }
    }
    return false;
}

}  // anonymous namespace

PredictorClient::PredictorClient(PredictorConfig cfg)
    : cfg_(std::move(cfg)) {
    // 缓存使用文件级 static (D-014 简化, 留 Cycle 3 改 pImpl)
}

bool PredictorClient::health() const {
    auto pos = cfg_.server_url.find("://");
    if (pos == std::string::npos) return false;
    std::string scheme_host = cfg_.server_url.substr(0, pos);
    std::string rest = cfg_.server_url.substr(pos + 3);
    std::string host;
    int port = 80;
    auto colon = rest.find(':');
    if (colon == std::string::npos) {
        host = rest;
    } else {
        host = rest.substr(0, colon);
        port = std::stoi(rest.substr(colon + 1));
    }
    try {
        httplib::Client cli(host, port);
        cli.set_connection_timeout(0, std::max(cfg_.timeout_ms, 1000) * 1000);
        cli.set_read_timeout(0, std::max(cfg_.timeout_ms, 1000) * 1000);
        auto res = cli.Get("/health");
        return res && res->status == 200;
    } catch (...) {
        return false;
    }
}

PredictionResult PredictorClient::predict(std::string_view cursor_prefix) {
    static LruCache cache(cfg_.cache_capacity);  // 文件级缓存（简化）

    PredictionResult r{};
    auto t0 = std::chrono::steady_clock::now();

    std::string prompt{cursor_prefix};
    if (auto hit = cache.get(prompt)) {
        r.text = *hit;
        r.cache_hit = true;
        r.error_kind = r.text.empty() ? PredictionErrorKind::kEmpty : PredictionErrorKind::kOk;
        auto t1 = std::chrono::steady_clock::now();
        r.total_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
        return r;
    }

    // 解析 server_url
    auto pos = cfg_.server_url.find("://");
    std::string host;
    int port = 80;
    if (pos != std::string::npos) {
        std::string rest = cfg_.server_url.substr(pos + 3);
        auto colon = rest.find(':');
        if (colon == std::string::npos) { host = rest; }
        else { host = rest.substr(0, colon); port = std::stoi(rest.substr(colon + 1)); }
    }

    httplib::Client cli(host, port);
    cli.set_connection_timeout(0, cfg_.timeout_ms * 1000);
    cli.set_read_timeout(0, cfg_.timeout_ms * 1000);

    nlohmann::json body = {
        {"prompt", prompt},
        {"n_predict", cfg_.n_predict},
        {"cache_prompt", false},
        {"temperature", cfg_.temperature},
        {"top_k", cfg_.top_k},
        {"stop", {"\n", "。", "！", "？", "!", "?", " ", ",", "，"}},
    };

    auto res = cli.Post("/completion", body.dump(), "application/json");
    auto t1 = std::chrono::steady_clock::now();
    r.total_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

    if (!res) {
        // 区分 timeout / 其他
        auto err = res.error();
        if (err == httplib::Error::Read || err == httplib::Error::Write ||
            err == httplib::Error::ConnectionTimeout) {
            r.error_kind = PredictionErrorKind::kTimeout;
        } else {
            r.error_kind = PredictionErrorKind::kServerDown;
            r.error_detail = httplib::to_string(err);
        }
        EKTRO_LOG_WARN("predictor.client", "predict transport error: %s",
                       httplib::to_string(err).c_str());
        return r;
    }
    if (res->status >= 400) {
        r.error_kind = PredictionErrorKind::kHttpError;
        r.error_detail = "HTTP " + std::to_string(res->status);
        EKTRO_LOG_WARN("predictor.client", "predict HTTP %d", res->status);
        return r;
    }

    try {
        auto data = nlohmann::json::parse(res->body);
        std::string text = data.value("content", std::string{});
        // 去 trailing stop tokens
        const std::vector<std::string> tails = {"\n", "。", "！", "？", "!", "?", " ", ",", "，"};
        for (const auto& t : tails) {
            while (text.size() >= t.size() && text.substr(text.size() - t.size()) == t) {
                text.erase(text.size() - t.size());
            }
        }
        if (is_low_quality(text)) {
            EKTRO_LOG_DEBUG("predictor.client", "predict low-quality output discarded");
            text.clear();
        }
        r.text = text;
        if (data.contains("timings") && data["timings"].is_object()) {
            const auto& t = data["timings"];
            if (t.contains("prompt_n")) r.prompt_tokens = t["prompt_n"].get<std::int64_t>();
            if (t.contains("prompt_ms")) r.prefill_ms = t["prompt_ms"].get<double>();
        }
        r.error_kind = text.empty() ? PredictionErrorKind::kEmpty : PredictionErrorKind::kOk;
        if (!text.empty()) cache.put(prompt, text);
    } catch (const std::exception& e) {
        r.error_kind = PredictionErrorKind::kParseError;
        r.error_detail = e.what();
        EKTRO_LOG_WARN("predictor.client", "predict parse error: %s", e.what());
    }

    return r;
}

void PredictorClient::clear_cache() {
    // 静态缓存清空 - 简化实现，生产用 pImpl
}

std::size_t PredictorClient::cache_size() const {
    return 0;  // 同上
}

#endif  // EKTRO_HAS_HTTPLIB && EKTRO_HAS_JSON

}  // namespace ektro
