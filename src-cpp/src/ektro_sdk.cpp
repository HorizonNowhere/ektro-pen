// SPDX-License-Identifier: Apache-2.0
#include "ektro/ektro_sdk.h"
#include "ektro/memory_store.h"
#include "ektro/log_result.h"
#include "ektro/reranker.h"
#include "ektro/predictor.h"
#include <memory>
#include <new>
#include <span>
#include <string>
#include <sstream>
#include <cstring>
#include <cstdio>
#include <optional>
#include <vector>

struct ektro_ctx {
    std::unique_ptr<ektro::EktroMemoryStore> store;
    ektro_predictor_kind predictor = EKTRO_PREDICTOR_BASELINE;
    std::string llama_url;
    int password_field = 0;
    std::string last_error;
};

extern "C" {

ektro_ctx* ektro_create(const char* db_path, const ektro_config* cfg) {
    // FIX M-2: 用 nothrow 分配，OOM 时安全返回 nullptr 而不是让异常穿越 extern "C"
    auto* c = new (std::nothrow) ektro_ctx();
    if (!c) return nullptr;
    try {
        c->store = std::make_unique<ektro::EktroMemoryStore>(db_path ? db_path : ":memory:");
        if (cfg) { c->predictor = cfg->predictor;
                   c->llama_url = cfg->llama_server_url ? cfg->llama_server_url : ""; }
    } catch (const std::exception& e) {
        // FIX I-1: 构造失败时保留句柄，让调用方能用 ektro_last_error 探知失败原因
        c->last_error = e.what();
    } catch (...) {
        c->last_error = "unknown error during init";
    }
    return c;
}

void ektro_destroy(ektro_ctx* c) { delete c; }

const char* ektro_last_error(ektro_ctx* c) { return c ? c->last_error.c_str() : ""; }

void ektro_set_password_field(ektro_ctx* c, int is_password) {
    if (c) c->password_field = is_password;
}

int ektro_log_commit(ektro_ctx* c, const char* input_raw,
                     const char* output_cjk, int is_password_field) {
    if (!c || !c->store) return 1;
    c->last_error.clear();  // FIX M-3: 清除上次残留错误
    try {
        ektro::EktroMemoryStore::LogCommitArgs a;
        a.input_raw = input_raw ? input_raw : "";
        a.output = output_cjk ? output_cjk : "";
        a.is_password_field = is_password_field || c->password_field;
        // FIX I-2: 检查 LogOutcome，DB 错误上报；隐私 skip 静默成功
        ektro::LogOutcome outcome = c->store->log_commit(a);
        if (outcome.result == ektro::LogResult::kDbError) {
            c->last_error = outcome.error_detail.value_or("db error");
            return 1;
        }
        // kCommitted / kSkippedPassword / kSkippedSensitive / kSkippedApp → 全部返回 0
        return 0;
    } catch (const std::exception& e) {
        c->last_error = e.what();
        return 1;
    } catch (...) {
        c->last_error = "unknown error";  // FIX M-1
        return 1;
    }
}

// ─── 静态辅助：安全写 buf（无溢出），D-009 绝不静默 ───
static int write_buf(ektro_ctx* c, const std::string& s, char* buf, int n) {
    if (!buf || n <= 0) { c->last_error = "buf invalid"; return 2; }
    if ((int)s.size() + 1 > n) { c->last_error = "buf too small"; return 3; }
    std::memcpy(buf, s.c_str(), s.size() + 1);
    return 0;
}

// ─── ektro_rerank ───
// 适配要点：
//   BaselineReranker ctor 接受 MemoryView&（EktroMemoryStore 继承链满足）
//   rerank() 返回 vector<RankedCandidate>，需提取 .candidate 字段
//   候选以 '\n' 分隔输入，同格式输出（按 new_rank 顺序）
int ektro_rerank(ektro_ctx* c, const char* cands_in, char* buf, int buf_len) {
    if (!c || !c->store) return 1;
    c->last_error.clear();  // FIX M-3
    try {
        ektro::BaselineReranker rr(*c->store);
        std::vector<std::string> in;
        std::string s(cands_in ? cands_in : ""), tok;
        std::stringstream ss(s);
        while (std::getline(ss, tok, '\n')) if (!tok.empty()) in.push_back(tok);
        auto ranked = rr.rerank(std::span<const std::string>(in));
        // 提取 .candidate 字段拼 joined
        std::string joined;
        for (size_t i = 0; i < ranked.size(); ++i) {
            if (i) joined += '\n';
            joined += ranked[i].candidate;
        }
        return write_buf(c, joined, buf, buf_len);
    } catch (const std::exception& e) { c->last_error = e.what(); return 1; }
      catch (...) { c->last_error = "unknown error"; return 1; }  // FIX M-1
}

// ─── ektro_predict ───
// 适配要点：
//   BaselinePredictor ctor 接受 MemoryView&
//   predict() 返回 PredictionResult，需提取 .text 字段
//   即使 text 为空也返回 0（测试 PredictBaselineReturnsZeroEvenWhenEmpty）
int ektro_predict(ektro_ctx* c, const char* ctx, char* buf, int buf_len) {
    if (!c || !c->store) return 1;
    c->last_error.clear();  // FIX M-3
    try {
        ektro::BaselinePredictor p(*c->store);
        ektro::PredictionResult result = p.predict(ctx ? ctx : "");
        return write_buf(c, result.text, buf, buf_len);
    } catch (const std::exception& e) { c->last_error = e.what(); return 1; }
      catch (...) { c->last_error = "unknown error"; return 1; }  // FIX M-1
}

int ektro_memory_clear(ektro_ctx* c) {
    if (!c || !c->store) return 1;
    c->last_error.clear();  // FIX M-3
    try { c->store->clear_all(true); return 0; }
    catch (const std::exception& e) { c->last_error = e.what(); return 1; }
    catch (...) { c->last_error = "unknown error"; return 1; }  // FIX M-1
}

// ─── ektro_rerank_order ───
// 排列版 rerank：out_order[new_rank] = base_rank。
// 直通(候选<2) → *out_n=0, return 0 (合法 no-op，非错误)。
// 容量不足 / 排列非法 / 大小不符 → return 非0, *out_n=0, last_error 设置。
int ektro_rerank_order(ektro_ctx* c, const char* cands_joined, const char* context,
                       const char* recent_joined, int* out_order,
                       int order_cap, int* out_n) {
    if (out_n) *out_n = 0;
    if (!c || !c->store) return 1;
    c->last_error.clear();  // FIX M-3 一致性
    try {
        // 分割候选
        std::vector<std::string> cands;
        {
            std::string s(cands_joined ? cands_joined : ""), tok;
            std::stringstream ss(s);
            while (std::getline(ss, tok, '\n')) if (!tok.empty()) cands.push_back(tok);
        }
        // 候选<2 → 合法直通，不报错
        if (cands.size() < 2) { if (out_n) *out_n = 0; return 0; }

        // 分割最近输出
        std::vector<std::string> recent;
        if (recent_joined && recent_joined[0] != '\0') {
            std::string rs(recent_joined), tok;
            std::stringstream ss(rs);
            while (std::getline(ss, tok, '\n')) if (!tok.empty()) recent.push_back(tok);
        }

        const std::size_t n = cands.size();

        // 容量检查
        if ((int)n > order_cap) {
            c->last_error = "order buf too small";
            if (out_n) *out_n = 0;
            return 3;
        }

        ektro::BaselineReranker rr(*c->store);
        auto ranked = rr.rerank(
            std::span<const std::string>(cands.data(), cands.size()),
            context ? context : "",
            std::span<const std::string>(recent.data(), recent.size()));

        // 大小不符 → 报错
        if (ranked.size() != n) {
            c->last_error = "rerank size mismatch";
            if (out_n) *out_n = 0;
            return 4;
        }

        // 构建排列：out_order[new_rank] = base_rank
        std::vector<int> order(n, -1);
        for (const auto& rc : ranked) {
            if (rc.base_rank >= n || rc.new_rank >= n || order[rc.new_rank] != -1) {
                c->last_error = "invalid permutation";
                if (out_n) *out_n = 0;
                return 4;
            }
            order[rc.new_rank] = static_cast<int>(rc.base_rank);
        }

        // 写出
        for (std::size_t i = 0; i < n; ++i) out_order[i] = order[i];
        if (out_n) *out_n = static_cast<int>(n);
        return 0;
    } catch (const std::exception& e) { c->last_error = e.what(); if (out_n) *out_n = 0; return 1; }
      catch (...) { c->last_error = "unknown error"; if (out_n) *out_n = 0; return 1; }
}

int ektro_memory_export(ektro_ctx* c, const char* out_path) {
    if (!c || !c->store) return 1;
    c->last_error.clear();  // FIX M-3
    try {
        if (!out_path) { c->last_error = "out_path null"; return 2; }
        FILE* f = std::fopen(out_path, "w");
        if (!f) { c->last_error = "cannot open out_path"; return 3; }
        for (const auto& r : c->store->recent_outputs(100000, std::nullopt))
            std::fprintf(f, "%s\n", r.output.c_str());
        std::fclose(f);
        return 0;
    } catch (const std::exception& e) { c->last_error = e.what(); return 1; }
      catch (...) { c->last_error = "unknown error"; return 1; }  // FIX M-1
}

}  // extern "C"
