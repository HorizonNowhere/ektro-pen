// SPDX-License-Identifier: Apache-2.0
// EKTRO 简易日志实现

#include "ektro/log.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <sstream>

#ifdef _WIN32
#include <windows.h>
#include <shlobj.h>
#endif

namespace ektro::log {

namespace {

std::atomic<Level> g_level{Level::kInfo};
std::mutex g_file_mu;
std::ofstream g_file;
bool g_initialized = false;
std::string g_log_path;

std::string default_log_dir() {
#ifdef _WIN32
    char buf[MAX_PATH] = {0};
    if (SUCCEEDED(SHGetFolderPathA(nullptr, CSIDL_LOCAL_APPDATA, nullptr, 0, buf))) {
        return std::string(buf) + "\\Ektro\\logs";
    }
#endif
    // POSIX / fallback
    const char* xdg = std::getenv("XDG_DATA_HOME");
    if (xdg) return std::string(xdg) + "/ektro/logs";
    return "/tmp/ektro-logs";
}

void ensure_init() {
    if (g_initialized) return;
    std::lock_guard<std::mutex> g(g_file_mu);
    if (g_initialized) return;

    // 解析日志级别
    if (const char* env_lv = std::getenv("EKTRO_LOG_LEVEL")) {
        std::string s = env_lv;
        for (auto& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        if (s == "DEBUG") g_level = Level::kDebug;
        else if (s == "INFO") g_level = Level::kInfo;
        else if (s == "WARNING" || s == "WARN") g_level = Level::kWarning;
        else if (s == "ERROR") g_level = Level::kError;
    }

    std::string dir = g_log_path.empty() ? default_log_dir() : g_log_path;
    try {
        std::filesystem::create_directories(dir);
        g_file.open(dir + "/ektro.log", std::ios::app);
    } catch (...) {
        // D-009 P1.5 fallback: 写不了文件就只走 stderr
        std::fprintf(stderr, "[ektro] failed to open log dir %s, stderr only\n", dir.c_str());
    }
    g_initialized = true;
}

const char* level_name(Level lv) {
    switch (lv) {
        case Level::kDebug:   return "DEBUG";
        case Level::kInfo:    return "INFO ";
        case Level::kWarning: return "WARN ";
        case Level::kError:   return "ERROR";
    }
    return "?";
}

std::string format_now() {
    using namespace std::chrono;
    auto now = system_clock::now();
    auto t = system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &tm);
    return buf;
}

}  // anonymous namespace

void set_level(Level lv) { g_level = lv; }
Level get_level() { return g_level; }

void set_log_dir(std::string_view dir) { g_log_path = std::string(dir); }

void log_msg(Level lv, const char* module, const char* fmt, ...) {
    if (lv < g_level) return;
    ensure_init();

    char msg[1024];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);

    std::string line = format_now();
    line += ' ';
    line += level_name(lv);
    line += " ektro.";
    line += (module ? module : "?");
    line += " | ";
    line += msg;
    line += '\n';

    {
        std::lock_guard<std::mutex> g(g_file_mu);
        if (g_file.is_open()) {
            g_file << line;
            g_file.flush();
        }
    }
    if (lv >= Level::kWarning) {
        std::fputs(line.c_str(), stderr);
    }
}

}  // namespace ektro::log
