// SPDX-License-Identifier: Apache-2.0
// EKTRO Core SDK — 唯一对外 C ABI 契约。所有平台前端只依赖此头。
#ifndef EKTRO_SDK_H
#define EKTRO_SDK_H
#ifdef __cplusplus
extern "C" {
#endif

typedef struct ektro_ctx ektro_ctx;

typedef enum { EKTRO_PREDICTOR_BASELINE = 0, EKTRO_PREDICTOR_LLAMA = 1 } ektro_predictor_kind;

typedef struct {
    ektro_predictor_kind predictor;   /* 桌面默认 LLAMA, 移动 BASELINE */
    const char* llama_server_url;     /* predictor=LLAMA 时用; 否则忽略 */
} ektro_config;

/* 返回码: 0=OK, 非0=错误, 详情见 ektro_last_error */
/* 注意: ektro_create 始终返回非 null 句柄，但初始化可能失败 —— 调用方必须在
   ektro_create 返回后立即检查 ektro_last_error(ctx) 为空才能确认初始化成功。
   唯一例外: OOM 导致 ctx 本身无法分配时返回 nullptr。 */
ektro_ctx* ektro_create(const char* db_path, const ektro_config* cfg);
void       ektro_destroy(ektro_ctx*);

int  ektro_log_commit(ektro_ctx*, const char* input_raw,
                       const char* output_cjk, int is_password_field);

/* rerank: cands_in 是 '\n' 分隔候选; 结果写入调用方提供的 buf(同格式) */
int  ektro_rerank(ektro_ctx*, const char* cands_in, char* buf, int buf_len);

/* rerank 排列版: cands_joined / recent_joined 为 '\n' 分隔(recent_joined 可为 NULL/空)。
   成功返回 0; 若产出完整排列则 *out_n = 候选数, out_order[new_rank] = base_rank;
   若直通(候选<2 / 无 reranker / 内部判定不重排)则返回 0 且 *out_n = 0。
   out_order 容量不足 / 内部故障 → 返回非0 且设置 last_error, *out_n = 0。 */
int ektro_rerank_order(ektro_ctx*, const char* cands_joined, const char* context,
                       const char* recent_joined, int* out_order,
                       int order_cap, int* out_n);

/* predict: ctx 上下文 → 续写写入 buf */
int  ektro_predict(ektro_ctx*, const char* ctx, char* buf, int buf_len);

void ektro_set_password_field(ektro_ctx*, int is_password);
int  ektro_memory_export(ektro_ctx*, const char* out_path);
int  ektro_memory_clear(ektro_ctx*);

const char* ektro_last_error(ektro_ctx*);  /* 绝不静默吞错 (D-009) */

#ifdef __cplusplus
}
#endif
#endif /* EKTRO_SDK_H */
