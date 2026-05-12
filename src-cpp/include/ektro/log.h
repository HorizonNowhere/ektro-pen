// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/common/logging.py
//
// 简易日志门面：默认写 %LOCALAPPDATA%\Ektro\logs\ektro.log
// 公理 ②：日志中绝不写 commit 内容
//
// 默认实现: stderr + 文件。生产建议替换为 spdlog (header-only)。

#pragma once

#include <cstdarg>
#include <string>
#include <string_view>

namespace ektro::log {

enum class Level {
    kDebug = 0,
    kInfo = 1,
    kWarning = 2,
    kError = 3,
};

// 设置/获取日志级别（默认从 EKTRO_LOG_LEVEL 环境变量读，缺省 Info）
void set_level(Level lv);
Level get_level();

// 设置日志目录（默认 %LOCALAPPDATA%\Ektro\logs）
// 如不可写则 fallback 到 tempdir（D-009 P1.5 提醒）
void set_log_dir(std::string_view dir);

// 主入口
void log_msg(Level lv, const char* module, const char* fmt, ...);

// 便利宏 - 注意：永远不要把 commit 内容传给这些宏
#define EKTRO_LOG_DEBUG(mod, fmt, ...) \
    ::ektro::log::log_msg(::ektro::log::Level::kDebug, mod, fmt, ##__VA_ARGS__)
#define EKTRO_LOG_INFO(mod, fmt, ...) \
    ::ektro::log::log_msg(::ektro::log::Level::kInfo, mod, fmt, ##__VA_ARGS__)
#define EKTRO_LOG_WARN(mod, fmt, ...) \
    ::ektro::log::log_msg(::ektro::log::Level::kWarning, mod, fmt, ##__VA_ARGS__)
#define EKTRO_LOG_ERROR(mod, fmt, ...) \
    ::ektro::log::log_msg(::ektro::log::Level::kError, mod, fmt, ##__VA_ARGS__)

}  // namespace ektro::log
