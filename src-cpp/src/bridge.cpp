// SPDX-License-Identifier: Apache-2.0
// EKTRO D-015: C++20 实现, 暴露 C++17-safe 接口 (ektro/bridge.h)。
#include "ektro/bridge.h"

#include "ektro/memory_store.h"
#include "ektro/reranker.h"
#include "ektro/log.h"

#include <deque>
#include <exception>
#include <memory>
#include <mutex>
#include <span>

namespace {

struct Engine {
    std::unique_ptr<ektro::EktroMemoryStore> store;
    std::unique_ptr<ektro::BaselineReranker> reranker;
    std::deque<std::string> recent;
    std::mutex mtx;
    bool tried = false;
};

Engine& eng() {
    static Engine e;
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
        e.store = std::make_unique<ektro::EktroMemoryStore>(db_path);
        e.reranker = std::make_unique<ektro::BaselineReranker>(*e.store);
    } catch (const std::exception& ex) {
        // SOP#8: 绝不静默吞错。公理②: 不记 db_path 之外的用户内容。
        EKTRO_LOG_ERROR("bridge",
                        "init failed, rerank disabled (passthrough): %s",
                        ex.what());
        e.store.reset();
        e.reranker.reset();
    } catch (...) {
        EKTRO_LOG_ERROR("bridge",
                        "init failed (unknown), rerank disabled");
        e.store.reset();
        e.reranker.reset();
    }
}

std::vector<int> rerank_order(
    const std::vector<std::string>& candidates,
    const std::string& context,
    const std::vector<std::string>& recent_outputs) noexcept {
    try {
        auto& e = eng();
        // 合法 no-op (非故障): 不 log, 避免噪音。
        if (!e.reranker || candidates.size() < 2) return {};
        std::vector<std::string> rec;
        {
            std::lock_guard<std::mutex> lk(e.mtx);
            rec.assign(e.recent.begin(), e.recent.end());
        }
        if (!recent_outputs.empty()) rec = recent_outputs;
        auto ranked = e.reranker->rerank(
            std::span<const std::string>(candidates.data(), candidates.size()),
            context,
            std::span<const std::string>(rec.data(), rec.size()));
        if (ranked.size() != candidates.size()) {
            // SOP#8: 真实内部故障 (reranker 返回数量不符), 非合法 no-op。
            EKTRO_LOG_WARN("bridge",
                           "rerank size mismatch (%zu vs %zu), passthrough",
                           ranked.size(), candidates.size());
            return {};
        }
        const std::size_t n = candidates.size();
        std::vector<int> order(n, -1);
        for (const auto& rc : ranked) {
            if (rc.base_rank >= n || rc.new_rank >= n ||
                order[rc.new_rank] != -1) {
                // SOP#8: reranker 产生非法排列, 留痕后直通。
                EKTRO_LOG_WARN("bridge",
                               "invalid permutation, passthrough");
                return {};
            }
            order[rc.new_rank] = static_cast<int>(rc.base_rank);
        }
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
        if (!e.store || output.empty()) return;
        ektro::EktroMemoryStore::LogCommitArgs args;
        args.input_raw = "";
        args.output = output;
        args.user_picked = true;
        e.store->log_commit(args);
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
