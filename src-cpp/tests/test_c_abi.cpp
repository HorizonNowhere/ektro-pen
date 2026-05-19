#include "ektro/ektro_sdk.h"
#include <gtest/gtest.h>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <filesystem>

// ─── 辅助：在 OS 临时目录生成唯一路径 ───
static std::string make_temp_path(const char* suffix) {
    auto tmp = std::filesystem::temp_directory_path();
    tmp /= std::string("ektro_test_") + suffix;
    return tmp.string();
}

// ─── 保留的稳健测试 ───────────────────────────────────────────────────────────

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

// ─── 新增强化测试 (M-4 / M-5) ────────────────────────────────────────────────

// M-4a: export 写入已提交的内容可被验证读回
TEST(CAbi, ExportWritesCommittedOutput) {
    std::string out_path = make_temp_path("export_committed.txt");
    std::remove(out_path.c_str());  // 确保不存在旧文件

    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ASSERT_NE(c, nullptr);
    ASSERT_STREQ(ektro_last_error(c), "");

    ASSERT_EQ(ektro_log_commit(c, "nihao", "你好", 0), 0);

    int rc = ektro_memory_export(c, out_path.c_str());
    EXPECT_EQ(rc, 0) << "export failed: " << ektro_last_error(c);

    // 读回文件，验证包含 "你好"
    std::ifstream f(out_path);
    ASSERT_TRUE(f.is_open()) << "cannot open export file";
    std::string content((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());
    EXPECT_NE(content.find("你好"), std::string::npos)
        << "exported file does not contain committed output";

    ektro_destroy(c);
    std::remove(out_path.c_str());
}

// M-4b: export 传 nullptr 路径返回非零且 last_error 非空
TEST(CAbi, ExportNullPathReturnsError) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ASSERT_NE(c, nullptr);

    int rc = ektro_memory_export(c, nullptr);
    EXPECT_NE(rc, 0);
    EXPECT_STRNE(ektro_last_error(c), "");

    ektro_destroy(c);
}

// M-5 / 戒②端到端: 密码字段的输入不得持久化到 export，但普通 commit 仍可持久化
TEST(CAbi, PasswordInputNeverPersisted) {
    std::string pw_path  = make_temp_path("privacy_pw.txt");
    std::string ok_path  = make_temp_path("privacy_ok.txt");
    std::remove(pw_path.c_str());
    std::remove(ok_path.c_str());

    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ASSERT_NE(c, nullptr);
    ASSERT_STREQ(ektro_last_error(c), "");

    // 密码字段 commit — 应静默 skip，不写入 DB
    ektro_set_password_field(c, 1);
    EXPECT_EQ(ektro_log_commit(c, "mima123", "秘密", 1), 0)
        << "password commit must return 0 (silent skip)";
    ektro_set_password_field(c, 0);

    // 导出并验证"秘密"不在其中
    ASSERT_EQ(ektro_memory_export(c, pw_path.c_str()), 0);
    {
        std::ifstream f(pw_path);
        ASSERT_TRUE(f.is_open());
        std::string content((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());
        EXPECT_EQ(content.find("秘密"), std::string::npos)
            << "password output must NOT appear in export (戒②)";
    }

    // 普通 commit 之后导出，验证能被读回（store 仍正常工作）
    ASSERT_EQ(ektro_log_commit(c, "xihuan", "喜欢", 0), 0);
    ASSERT_EQ(ektro_memory_export(c, ok_path.c_str()), 0);
    {
        std::ifstream f(ok_path);
        ASSERT_TRUE(f.is_open());
        std::string content((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());
        EXPECT_NE(content.find("喜欢"), std::string::npos)
            << "normal output must appear in export after password commit";
    }

    ektro_destroy(c);
    std::remove(pw_path.c_str());
    std::remove(ok_path.c_str());
}
