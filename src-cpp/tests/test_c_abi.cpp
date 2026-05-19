#include "ektro/ektro_sdk.h"
#include <gtest/gtest.h>
#include <cstdio>

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
