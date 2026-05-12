// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/predictor/trigger.py AsyncTrigger
//
// D-009 P0.3: 回调异常不再吞
// D-009 P0.5.1: 不派发 !is_ok 的结果到 UI
// D-011 P1.1: task_id 用 std::atomic<uint64_t> (替 perf_counter 浮点)

#include "ektro/predictor.h"
#include "ektro/log.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <thread>
#include <unordered_map>

namespace ektro {
namespace {

// 模块级单调 task_id (D-011 P1.1)
// 注: 多个 AsyncTrigger 实例共享此计数器是有意的——避免 test 中实例 ID 重叠
std::atomic<std::uint64_t> g_task_id_counter{1};

struct PendingTask {
    std::string prefix;
    std::string context;
    std::uint64_t task_id;
    std::chrono::steady_clock::time_point submitted_at;
};

// 文件级状态 - 在 production 应该用 pImpl
// 本骨架先用 static-per-instance-this 的简化版本
// (Cycle 2 集成时改 unique_ptr<Impl_>)
struct TriggerState {
    std::mutex mu;
    std::condition_variable cv;
    std::optional<PendingTask> pending;
    std::uint64_t latest_task_id = 0;
    std::uint64_t last_completed_task_id = 0;
    std::chrono::steady_clock::time_point last_keystroke;
    std::atomic<bool> stop_flag{false};
    std::thread worker;
};

// 简化: 每个 AsyncTrigger 用全局 map<this*, TriggerState> 维护
std::mutex g_state_mu;
std::unordered_map<void*, TriggerState*> g_states;

TriggerState& get_state(void* key) {
    std::lock_guard<std::mutex> g(g_state_mu);
    auto it = g_states.find(key);
    if (it == g_states.end()) {
        auto* s = new TriggerState();
        g_states[key] = s;
        return *s;
    }
    return *it->second;
}

void drop_state(void* key) {
    std::lock_guard<std::mutex> g(g_state_mu);
    auto it = g_states.find(key);
    if (it != g_states.end()) {
        delete it->second;
        g_states.erase(it);
    }
}

}  // anonymous namespace

AsyncTrigger::AsyncTrigger(BasePredictor& predictor,
                           ResultCallback on_result,
                           TriggerConfig cfg)
    : predictor_(predictor), on_result_(std::move(on_result)), cfg_(cfg) {}

AsyncTrigger::~AsyncTrigger() {
    stop();
    drop_state(this);
}

void AsyncTrigger::start() {
    auto& s = get_state(this);
    if (s.worker.joinable()) return;

    s.stop_flag = false;
    s.worker = std::thread([this, &s]() {
        using namespace std::chrono;
        while (!s.stop_flag.load()) {
            std::this_thread::sleep_for(milliseconds(cfg_.poll_interval_ms));

            std::optional<PendingTask> task;
            {
                std::lock_guard<std::mutex> g(s.mu);
                if (!s.pending) continue;
                auto idle_ms = duration_cast<milliseconds>(
                    steady_clock::now() - s.last_keystroke).count();
                if (idle_ms < cfg_.pause_ms) continue;
                if (s.last_completed_task_id == s.pending->task_id) continue;
                task = *s.pending;
                s.last_completed_task_id = task->task_id;
            }

            // 跑预测（释放锁）
            PredictionResult result = predictor_.predict(task->prefix);

            // 完成后再检查：用户是否又打字了？
            {
                std::lock_guard<std::mutex> g(s.mu);
                if (task->task_id < s.latest_task_id) continue;  // 过时
                if (!s.pending) continue;
            }

            // D-009 P0.5.1: 不派发 !is_ok 的 result 到 UI
            if (!result.is_ok()) {
                EKTRO_LOG_DEBUG("predictor.trigger",
                    "suppress non-ok result kind=%d", static_cast<int>(result.error_kind));
                continue;
            }

            try {
                on_result_(result);
            } catch (...) {
                EKTRO_LOG_ERROR("predictor.trigger", "on_result callback threw");
            }
        }
    });
}

void AsyncTrigger::stop() {
    auto& s = get_state(this);
    s.stop_flag = true;
    if (s.worker.joinable()) s.worker.join();
}

void AsyncTrigger::on_keystroke(std::string_view prefix, std::string_view context) {
    auto& s = get_state(this);
    auto now = std::chrono::steady_clock::now();
    std::lock_guard<std::mutex> g(s.mu);
    s.last_keystroke = now;
    if (prefix.size() < cfg_.debounce_min_chars) {
        s.pending.reset();
        return;
    }
    auto id = g_task_id_counter.fetch_add(1);
    s.latest_task_id = id;
    s.pending = PendingTask{std::string{prefix}, std::string{context}, id, now};
}

void AsyncTrigger::cancel() {
    auto& s = get_state(this);
    std::lock_guard<std::mutex> g(s.mu);
    s.pending.reset();
}

}  // namespace ektro
