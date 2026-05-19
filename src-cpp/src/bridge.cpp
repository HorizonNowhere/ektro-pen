// SPDX-License-Identifier: Apache-2.0
// EKTRO D-015: C++20 实现, 暴露 C++17-safe 接口 (ektro/bridge.h)。
// D-016 refactor: bridge 降级为调 C ABI 的薄适配层，不再直接使用
// EktroMemoryStore / BaselineReranker — 所有内核逻辑统一走 ektro_sdk C ABI。
#include "ektro/bridge.h"

#if defined(_MSC_VER) && defined(_DEBUG)
#error "ektro_bridge: must build Release /MT to match weasel CRT/STL ABI (D-015)"
#endif

#include "ektro/ektro_sdk.h"
#include "ektro/log.h"

#include <deque>
#include <exception>
#include <mutex>
#include <string>
#include <vector>

namespace {

struct Engine {
    ektro_ctx* ctx = nullptr;  // 唯一内核句柄，走 C ABI
    std::deque<std::string> recent;
    std::mutex mtx;
    bool tried = false;
};

Engine& eng() {
    static Engine e;
    // 注意: 单例 ctx 为进程生命周期，不在析构时调用 ektro_destroy，
    // 避免静态析构顺序问题（与原始实现行为一致）。
    return e;
}

}  // namespace

namespace ektro_bridge {

void init(const std::string& db_path) noexcept {
    auto& e = eng();
    std::lock_guard<std::mutex> lk(e.mtx);
    if (e.tried) return;
    e.tried = true;
    try {
        ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
        e.ctx = ektro_create(db_path.c_str(), &cfg);
        if (!e.ctx) {
            // SOP#8: 绝不静默吞错。
            EKTRO_LOG_ERROR("bridge",
                            "init failed, rerank disabled (passthrough): ektro_create returned null");
            return;
        }
        const char* err = ektro_last_error(e.ctx);
        if (err && err[0] != '\0') {
            // SOP#8 + 公理②: 记错误类型, 不记 db_path 内容之外的用户数据。
            EKTRO_LOG_ERROR("bridge",
                            "init failed, rerank disabled (passthrough): %s", err);
            ektro_destroy(e.ctx);
            e.ctx = nullptr;
        }
    } catch (const std::exception& ex) {
        // SOP#8: 绝不静默吞错。公理②: 不记 db_path 之外的用户内容。
        EKTRO_LOG_ERROR("bridge",
                        "init failed, rerank disabled (passthrough): %s",
                        ex.what());
        if (e.ctx) { ektro_destroy(e.ctx); e.ctx = nullptr; }
    } catch (...) {
        EKTRO_LOG_ERROR("bridge",
                        "init failed (unknown), rerank disabled");
        if (e.ctx) { ektro_destroy(e.ctx); e.ctx = nullptr; }
    }
}

std::vector<int> rerank_order(
    const std::vector<std::string>& candidates,
    const std::string& context,
    const std::vector<std::string>& recent_outputs) noexcept {
    try {
        auto& e = eng();
        // 合法 no-op (非故障): 不 log, 避免噪音。
        if (!e.ctx || candidates.size() < 2) return {};

        // 建 recent: 先拷贝 e.recent，再由 recent_outputs 覆盖（优先级与原始一致）。
        std::vector<std::string> rec;
        {
            std::lock_guard<std::mutex> lk(e.mtx);
            rec.assign(e.recent.begin(), e.recent.end());
        }
        if (!recent_outputs.empty()) rec = recent_outputs;

        // 拼接 candidates → '\n' 分隔字符串
        std::string cands_joined;
        for (std::size_t i = 0; i < candidates.size(); ++i) {
            if (i) cands_joined += '\n';
            cands_joined += candidates[i];
        }

        // 拼接 recent → '\n' 分隔字符串（空则传空串）
        std::string rec_joined;
        for (std::size_t i = 0; i < rec.size(); ++i) {
            if (i) rec_joined += '\n';
            rec_joined += rec[i];
        }

        std::vector<int> order(candidates.size());
        int n = 0;
        int rc = ektro_rerank_order(
            e.ctx,
            cands_joined.c_str(),
            context.c_str(),
            rec_joined.empty() ? nullptr : rec_joined.c_str(),
            order.data(),
            static_cast<int>(order.size()),
            &n);

        if (rc != 0) {
            // SOP#8 + design.md行153: 失败→直通(空), 但必须留痕。
            // 公理②: 只记异常类型, 不记候选文本。
            EKTRO_LOG_WARN("bridge", "rerank_order failed, passthrough: %s",
                           ektro_last_error(e.ctx));
            return {};
        }
        if (n == 0) {
            // 合法直通（候选<2 / reranker 判定不重排）: 不 log。
            return {};
        }
        if (n != static_cast<int>(candidates.size())) {
            // SOP#8: 真实内部故障 (reranker 返回数量不符), 非合法 no-op。
            EKTRO_LOG_WARN("bridge",
                           "rerank size mismatch (%d vs %zu), passthrough",
                           n, candidates.size());
            return {};
        }
        order.resize(static_cast<std::size_t>(n));
        return order;
    } catch (const std::exception& ex) {
        // SOP#8 + design.md行153: 失败→直通(空), 但必须留痕。
        // 公理②: 只记异常类型, 不记候选文本。
        EKTRO_LOG_WARN("bridge", "rerank_order failed, passthrough: %s",
                       ex.what());
        return {};
    } catch (...) {
        EKTRO_LOG_WARN("bridge",
                       "rerank_order failed (unknown), passthrough");
        return {};
    }
}

void log_commit(const std::string& output) noexcept {
    try {
        auto& e = eng();
        if (!e.ctx || output.empty()) return;
        int rc = ektro_log_commit(e.ctx, "", output.c_str(), 0);
        if (rc != 0) {
            // SOP#8: 留痕。公理②: 绝不记 output(commit) 内容, 仅异常类型。
            EKTRO_LOG_WARN("bridge", "log_commit failed (not learned): %s",
                           ektro_last_error(e.ctx));
        }
        std::lock_guard<std::mutex> lk(e.mtx);
        e.recent.push_back(output);
        while (e.recent.size() > 5) e.recent.pop_front();
    } catch (const std::exception& ex) {
        // SOP#8: 留痕。公理②: 绝不记 output(commit) 内容, 仅异常类型。
        EKTRO_LOG_WARN("bridge", "log_commit failed (not learned): %s",
                       ex.what());
    } catch (...) {
        EKTRO_LOG_WARN("bridge", "log_commit failed (unknown, not learned)");
    }
}

}  // namespace ektro_bridge
