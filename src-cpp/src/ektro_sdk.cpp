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

}  // extern "C"
