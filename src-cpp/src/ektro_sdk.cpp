// SPDX-License-Identifier: Apache-2.0
#include "ektro/ektro_sdk.h"
#include "ektro/memory_store.h"
#include <memory>
#include <string>

struct ektro_ctx {
    std::unique_ptr<ektro::EktroMemoryStore> store;
    ektro_predictor_kind predictor = EKTRO_PREDICTOR_BASELINE;
    std::string llama_url;
    int password_field = 0;
    std::string last_error;
};

extern "C" {

ektro_ctx* ektro_create(const char* db_path, const ektro_config* cfg) {
    auto* c = new ektro_ctx();
    try {
        c->store = std::make_unique<ektro::EktroMemoryStore>(db_path ? db_path : ":memory:");
        if (cfg) { c->predictor = cfg->predictor;
                   c->llama_url = cfg->llama_server_url ? cfg->llama_server_url : ""; }
    } catch (const std::exception& e) {
        c->last_error = e.what();
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
    try {
        ektro::EktroMemoryStore::LogCommitArgs a;
        a.input_raw = input_raw ? input_raw : "";
        a.output = output_cjk ? output_cjk : "";
        a.is_password_field = is_password_field || c->password_field;
        c->store->log_commit(a);
        return 0;
    } catch (const std::exception& e) {
        c->last_error = e.what();
        return 1;
    }
}

}  // extern "C"
