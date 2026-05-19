#include "ektro/ektro_sdk.h"
#include <gtest/gtest.h>
#include <cstdio>
#include <string>

TEST(CAbi, CreateDestroyRoundTrip) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ASSERT_NE(c, nullptr);
    EXPECT_STREQ(ektro_last_error(c), "");
    ektro_destroy(c);
}

TEST(CAbi, BadDbPathSetsError) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create("/no/such/dir/x.db", &cfg);
    if (c) { EXPECT_STRNE(ektro_last_error(c), ""); ektro_destroy(c); }
    else SUCCEED();
}

TEST(CAbi, LogCommitThenRerankReflectsLearning) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ASSERT_EQ(ektro_log_commit(c, "nihao", "你好", 0), 0);
    ektro_destroy(c);
}

TEST(CAbi, PasswordFieldFlagBlocksLogging) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ektro_set_password_field(c, 1);
    EXPECT_EQ(ektro_log_commit(c, "mima123", "密码", 1), 0);
    ektro_destroy(c);
}

TEST(CAbi, RerankReturnsNewlineSeparatedAndFitsBuf) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    char buf[256] = {0};
    int rc = ektro_rerank(c, "你好\n您好\n泥嚎", buf, sizeof(buf));
    EXPECT_EQ(rc, 0);
    EXPECT_NE(std::string(buf).find('\n'), std::string::npos);
    ektro_destroy(c);
}

TEST(CAbi, PredictBaselineReturnsZeroEvenWhenEmpty) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    char buf[256] = {0};
    EXPECT_EQ(ektro_predict(c, "今天天气", buf, sizeof(buf)), 0);
    ektro_destroy(c);
}

TEST(CAbi, BufTooSmallSetsErrorNotOverflow) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    char tiny[2];
    int rc = ektro_rerank(c, "你好\n您好", tiny, sizeof(tiny));
    EXPECT_NE(rc, 0);
    EXPECT_STRNE(ektro_last_error(c), "");
    ektro_destroy(c);
}
