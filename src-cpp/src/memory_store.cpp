// SPDX-License-Identifier: Apache-2.0
// 对应 Python: src/memory/store.py EktroMemoryStore
//
// D-009 P0/P1 + D-011 P0.5 全部修正:
//   - P0.1 隐私只检测 input_raw, output 不再做正则
//   - P0.2 logger (这里用 std::cerr stub, 真正集成时换 spdlog)
//   - P0.4 LogResult enum 返回
//   - P0.8 clear_all 先 commit 再 VACUUM
//   - P0.5.2 executemany 消串行 INSERT (D-011)
//   - P1.3 phrase_pair_batch_lookup (D-011)

#include "ektro/memory_store.h"
#include "ektro/schema.h"

#include <sqlite3.h>

#include <algorithm>
#include <chrono>
#include <iostream>
#include <regex>
#include <set>

namespace ektro {

namespace {

inline std::int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

// 隐私正则 (D-009 P0.1: 只用于 input_raw)
// 注: 生产建议换 RE2 防 ReDoS, 这里用 std::regex 与 Python re 行为对齐
const std::regex& re_bankcard() {
    static const std::regex r(R"(\b\d{16,19}\b)");
    return r;
}
const std::regex& re_idcard_cn() {
    static const std::regex r(R"(\b\d{17}[\dXx]\b)");
    return r;
}
const std::regex& re_email() {
    static const std::regex r(R"(\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b)");
    return r;
}

inline void log_debug(const char* msg) {
    // TODO: 接 spdlog (D-009 P0.2)
    std::cerr << "[ektro debug] " << msg << "\n";
}

inline void log_error(const char* msg) {
    std::cerr << "[ektro error] " << msg << "\n";
}

// UTF-8 按字符迭代 (粗略实现，覆盖 BMP + surrogate pair)
// 输出: 每个字符是一个 std::string (1-4 字节)
std::vector<std::string> utf8_chars(std::string_view s) {
    std::vector<std::string> out;
    out.reserve(s.size());
    for (std::size_t i = 0; i < s.size();) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        std::size_t len = 1;
        if ((c & 0x80) == 0)        len = 1;
        else if ((c & 0xE0) == 0xC0) len = 2;
        else if ((c & 0xF0) == 0xE0) len = 3;
        else if ((c & 0xF8) == 0xF0) len = 4;
        if (i + len > s.size()) len = s.size() - i;
        out.emplace_back(s.substr(i, len));
        i += len;
    }
    return out;
}

}  // anonymous namespace

EktroMemoryStore::EktroMemoryStore(const std::string& db_path) : db_path_(db_path) {
    if (sqlite3_open(db_path_.c_str(), &conn_) != SQLITE_OK) {
        log_error("sqlite3_open failed");
        if (conn_) sqlite3_close(conn_);
        conn_ = nullptr;
        throw std::runtime_error("EktroMemoryStore: failed to open SQLite");
    }

    sqlite3_exec(conn_, "PRAGMA journal_mode = WAL", nullptr, nullptr, nullptr);
    sqlite3_exec(conn_, "PRAGMA synchronous = NORMAL", nullptr, nullptr, nullptr);
    sqlite3_exec(conn_, "PRAGMA foreign_keys = ON", nullptr, nullptr, nullptr);

    std::lock_guard<std::mutex> g(mu_);
    if (init_db(conn_) <= 0) {
        sqlite3_close(conn_);
        conn_ = nullptr;
        throw std::runtime_error("EktroMemoryStore: schema init failed");
    }
    seed_default_config(conn_);
}

EktroMemoryStore::~EktroMemoryStore() {
    std::lock_guard<std::mutex> g(mu_);
    if (conn_) sqlite3_close(conn_);
}

// ─── 隐私 ───

bool EktroMemoryStore::is_sensitive_input(std::string_view input_raw) const {
    if (input_raw.empty()) return false;
    std::string s{input_raw};
    if (std::regex_search(s, re_bankcard())) return true;
    if (std::regex_search(s, re_idcard_cn())) return true;
    if (std::regex_search(s, re_email())) return true;
    return false;
}

bool EktroMemoryStore::is_excluded_app(std::optional<std::string_view> app_name) const {
    if (!app_name || app_name->empty()) return false;
    std::lock_guard<std::mutex> g(mu_);
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_,
            "SELECT 1 FROM privacy_exclude WHERE pattern = ? LIMIT 1",
            -1, &stmt, nullptr) != SQLITE_OK) return false;
    sqlite3_bind_text(stmt, 1, app_name->data(), static_cast<int>(app_name->size()), SQLITE_TRANSIENT);
    bool hit = sqlite3_step(stmt) == SQLITE_ROW;
    sqlite3_finalize(stmt);
    return hit;
}

// ─── 写入 ───

LogOutcome EktroMemoryStore::log_commit(const LogCommitArgs& a) {
    if (a.is_password_field) {
        log_debug("log_commit skipped: password field");
        return {LogResult::kSkippedPassword};
    }
    if (is_sensitive_input(a.input_raw)) {
        log_debug("log_commit skipped: sensitive input");
        return {LogResult::kSkippedSensitive};
    }
    if (a.app_name && is_excluded_app(*a.app_name)) {
        log_debug("log_commit skipped: excluded app");
        return {LogResult::kSkippedApp};
    }

    const std::int64_t ts = a.timestamp_ms.value_or(now_ms());

    std::lock_guard<std::mutex> g(mu_);
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_,
            "INSERT INTO commit_log (timestamp, input_raw, output, app_name, "
            "context_id, user_picked, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            -1, &stmt, nullptr) != SQLITE_OK) {
        log_error("prepare INSERT failed");
        return {LogResult::kDbError, std::nullopt, std::string{sqlite3_errmsg(conn_)}};
    }
    sqlite3_bind_int64(stmt, 1, ts);
    sqlite3_bind_text(stmt, 2, a.input_raw.data(), static_cast<int>(a.input_raw.size()), SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, a.output.data(), static_cast<int>(a.output.size()), SQLITE_TRANSIENT);
    if (a.app_name) sqlite3_bind_text(stmt, 4, a.app_name->c_str(), -1, SQLITE_TRANSIENT);
    else sqlite3_bind_null(stmt, 4);
    if (a.context_id) sqlite3_bind_int64(stmt, 5, *a.context_id);
    else sqlite3_bind_null(stmt, 5);
    sqlite3_bind_int(stmt, 6, a.user_picked ? 1 : 0);
    if (a.duration_ms) sqlite3_bind_int(stmt, 7, *a.duration_ms);
    else sqlite3_bind_null(stmt, 7);

    int rc = sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    if (rc != SQLITE_DONE) {
        return {LogResult::kDbError, std::nullopt, std::string{sqlite3_errmsg(conn_)}};
    }

    std::int64_t row_id = sqlite3_last_insert_rowid(conn_);
    update_word_freq_locked_(a.output, ts);
    update_phrase_pairs_locked_(a.output);
    return {LogResult::kCommitted, row_id};
}

void EktroMemoryStore::update_word_freq_locked_(std::string_view output, std::int64_t ts) {
    if (output.empty()) return;
    sqlite3_stmt* stmt = nullptr;
    const char* sql = "INSERT INTO word_freq (word, count, last_used) VALUES (?, 1, ?) "
                      "ON CONFLICT(word) DO UPDATE SET "
                      "count = count + 1, last_used = excluded.last_used";
    if (sqlite3_prepare_v2(conn_, sql, -1, &stmt, nullptr) != SQLITE_OK) return;

    sqlite3_exec(conn_, "BEGIN", nullptr, nullptr, nullptr);
    for (const auto& ch : utf8_chars(output)) {
        sqlite3_bind_text(stmt, 1, ch.data(), static_cast<int>(ch.size()), SQLITE_TRANSIENT);
        sqlite3_bind_int64(stmt, 2, ts);
        sqlite3_step(stmt);
        sqlite3_reset(stmt);
    }
    sqlite3_exec(conn_, "COMMIT", nullptr, nullptr, nullptr);
    sqlite3_finalize(stmt);
}

void EktroMemoryStore::update_phrase_pairs_locked_(std::string_view output) {
    auto chars = utf8_chars(output);
    if (chars.size() < 2) return;
    const char* sql = "INSERT INTO phrase_pair (prev, curr, count) VALUES (?, ?, 1) "
                      "ON CONFLICT(prev, curr) DO UPDATE SET count = count + 1";
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_, sql, -1, &stmt, nullptr) != SQLITE_OK) return;

    sqlite3_exec(conn_, "BEGIN", nullptr, nullptr, nullptr);
    for (std::size_t i = 0; i + 1 < chars.size(); ++i) {
        sqlite3_bind_text(stmt, 1, chars[i].data(), static_cast<int>(chars[i].size()), SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 2, chars[i+1].data(), static_cast<int>(chars[i+1].size()), SQLITE_TRANSIENT);
        sqlite3_step(stmt);
        sqlite3_reset(stmt);
    }
    sqlite3_exec(conn_, "COMMIT", nullptr, nullptr, nullptr);
    sqlite3_finalize(stmt);
}

// ─── 读 ───

std::vector<CommitRecord> EktroMemoryStore::recent_outputs(
    std::size_t limit, std::optional<std::int64_t> since_ms) const {
    std::lock_guard<std::mutex> g(mu_);
    std::vector<CommitRecord> out;
    out.reserve(limit);

    sqlite3_stmt* stmt = nullptr;
    const char* sql_a = "SELECT id, timestamp, input_raw, output, app_name, context_id, "
                        "user_picked, duration_ms FROM commit_log "
                        "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?";
    const char* sql_b = "SELECT id, timestamp, input_raw, output, app_name, context_id, "
                        "user_picked, duration_ms FROM commit_log "
                        "ORDER BY timestamp DESC LIMIT ?";

    if (since_ms) {
        if (sqlite3_prepare_v2(conn_, sql_a, -1, &stmt, nullptr) != SQLITE_OK) return out;
        sqlite3_bind_int64(stmt, 1, *since_ms);
        sqlite3_bind_int64(stmt, 2, static_cast<sqlite3_int64>(limit));
    } else {
        if (sqlite3_prepare_v2(conn_, sql_b, -1, &stmt, nullptr) != SQLITE_OK) return out;
        sqlite3_bind_int64(stmt, 1, static_cast<sqlite3_int64>(limit));
    }

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        CommitRecord r{};
        r.id = sqlite3_column_int64(stmt, 0);
        r.timestamp_ms = sqlite3_column_int64(stmt, 1);
        r.input_raw = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
        r.output = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 3));
        if (sqlite3_column_type(stmt, 4) != SQLITE_NULL)
            r.app_name = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
        if (sqlite3_column_type(stmt, 5) != SQLITE_NULL)
            r.context_id = sqlite3_column_int64(stmt, 5);
        r.user_picked = sqlite3_column_int(stmt, 6) != 0;
        if (sqlite3_column_type(stmt, 7) != SQLITE_NULL)
            r.duration_ms = sqlite3_column_int(stmt, 7);
        out.push_back(std::move(r));
    }
    sqlite3_finalize(stmt);
    return out;
}

std::unordered_map<std::string, std::int64_t> EktroMemoryStore::word_freq_lookup(
    std::span<const std::string> words) const {
    std::unordered_map<std::string, std::int64_t> out;
    if (words.empty()) return out;
    for (const auto& w : words) out[w] = 0;

    std::lock_guard<std::mutex> g(mu_);
    std::string sql = "SELECT word, count FROM word_freq WHERE word IN (";
    for (std::size_t i = 0; i < words.size(); ++i) {
        sql += (i == 0 ? "?" : ",?");
    }
    sql += ")";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK) return out;
    for (std::size_t i = 0; i < words.size(); ++i) {
        sqlite3_bind_text(stmt, static_cast<int>(i + 1),
                          words[i].c_str(), -1, SQLITE_TRANSIENT);
    }
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        std::string w = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        out[w] = sqlite3_column_int64(stmt, 1);
    }
    sqlite3_finalize(stmt);
    return out;
}

std::vector<std::pair<std::string, std::int64_t>>
EktroMemoryStore::phrase_pair_lookup(std::string_view prev) const {
    std::vector<std::pair<std::string, std::int64_t>> out;
    if (prev.empty()) return out;
    std::lock_guard<std::mutex> g(mu_);
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_,
            "SELECT curr, count FROM phrase_pair WHERE prev = ? ORDER BY count DESC",
            -1, &stmt, nullptr) != SQLITE_OK) return out;
    sqlite3_bind_text(stmt, 1, prev.data(), static_cast<int>(prev.size()), SQLITE_TRANSIENT);
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        out.emplace_back(
            reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0)),
            sqlite3_column_int64(stmt, 1)
        );
    }
    sqlite3_finalize(stmt);
    return out;
}

std::unordered_map<std::string, std::vector<std::pair<std::string, std::int64_t>>>
EktroMemoryStore::phrase_pair_batch_lookup(std::span<const std::string> prev_chars) const {
    std::unordered_map<std::string, std::vector<std::pair<std::string, std::int64_t>>> out;
    if (prev_chars.empty()) return out;
    // 去重
    std::set<std::string> uniq(prev_chars.begin(), prev_chars.end());
    if (uniq.empty()) return out;

    std::lock_guard<std::mutex> g(mu_);
    std::string sql = "SELECT prev, curr, count FROM phrase_pair WHERE prev IN (";
    for (std::size_t i = 0; i < uniq.size(); ++i) sql += (i == 0 ? "?" : ",?");
    sql += ") ORDER BY prev, count DESC";

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_, sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK) return out;
    int idx = 1;
    for (const auto& p : uniq) {
        sqlite3_bind_text(stmt, idx++, p.c_str(), -1, SQLITE_TRANSIENT);
    }
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        std::string prev = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        std::string curr = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        std::int64_t count = sqlite3_column_int64(stmt, 2);
        out[prev].emplace_back(std::move(curr), count);
    }
    sqlite3_finalize(stmt);
    return out;
}

// ─── 隐私管理 ───

void EktroMemoryStore::add_excluded_app(std::string_view app_name, std::string_view reason) {
    std::lock_guard<std::mutex> g(mu_);
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_,
            "INSERT OR REPLACE INTO privacy_exclude (pattern, reason, created_at) VALUES (?, ?, ?)",
            -1, &stmt, nullptr) != SQLITE_OK) return;
    sqlite3_bind_text(stmt, 1, app_name.data(), static_cast<int>(app_name.size()), SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, reason.data(), static_cast<int>(reason.size()), SQLITE_TRANSIENT);
    sqlite3_bind_int64(stmt, 3, now_ms());
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
}

void EktroMemoryStore::remove_excluded_app(std::string_view app_name) {
    std::lock_guard<std::mutex> g(mu_);
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_, "DELETE FROM privacy_exclude WHERE pattern = ?",
                           -1, &stmt, nullptr) != SQLITE_OK) return;
    sqlite3_bind_text(stmt, 1, app_name.data(), static_cast<int>(app_name.size()), SQLITE_TRANSIENT);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
}

std::vector<std::tuple<std::string, std::string, std::int64_t>>
EktroMemoryStore::list_excluded_apps() const {
    std::lock_guard<std::mutex> g(mu_);
    std::vector<std::tuple<std::string, std::string, std::int64_t>> out;
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_,
            "SELECT pattern, reason, created_at FROM privacy_exclude ORDER BY created_at DESC",
            -1, &stmt, nullptr) != SQLITE_OK) return out;
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        out.emplace_back(
            std::string{reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0))},
            std::string{sqlite3_column_type(stmt, 1) == SQLITE_NULL ? "" :
                        reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1))},
            sqlite3_column_int64(stmt, 2)
        );
    }
    sqlite3_finalize(stmt);
    return out;
}

// ─── 配置 ───

std::optional<std::string> EktroMemoryStore::get_config(std::string_view key) const {
    std::lock_guard<std::mutex> g(mu_);
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_, "SELECT value FROM config WHERE key = ?",
                           -1, &stmt, nullptr) != SQLITE_OK) return std::nullopt;
    sqlite3_bind_text(stmt, 1, key.data(), static_cast<int>(key.size()), SQLITE_TRANSIENT);
    std::optional<std::string> result;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        result = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
    }
    sqlite3_finalize(stmt);
    return result;
}

void EktroMemoryStore::set_config(std::string_view key, std::string_view value) {
    std::lock_guard<std::mutex> g(mu_);
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(conn_,
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            -1, &stmt, nullptr) != SQLITE_OK) return;
    sqlite3_bind_text(stmt, 1, key.data(), static_cast<int>(key.size()), SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT);
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
}

// ─── 维护 ───

EktroMemoryStore::Stats EktroMemoryStore::stats() const {
    std::lock_guard<std::mutex> g(mu_);
    Stats s{};
    sqlite3_stmt* stmt;
    auto count_one = [&](const char* sql) -> std::int64_t {
        std::int64_t v = 0;
        if (sqlite3_prepare_v2(conn_, sql, -1, &stmt, nullptr) == SQLITE_OK) {
            if (sqlite3_step(stmt) == SQLITE_ROW) v = sqlite3_column_int64(stmt, 0);
            sqlite3_finalize(stmt);
        }
        return v;
    };
    s.total_commits = count_one("SELECT COUNT(*) FROM commit_log");
    s.unique_chars = count_one("SELECT COUNT(*) FROM word_freq");
    s.unique_phrase_pairs = count_one("SELECT COUNT(*) FROM phrase_pair");

    if (sqlite3_prepare_v2(conn_, "SELECT MIN(timestamp), MAX(timestamp) FROM commit_log",
                           -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            if (sqlite3_column_type(stmt, 0) != SQLITE_NULL)
                s.first_commit_ms = sqlite3_column_int64(stmt, 0);
            if (sqlite3_column_type(stmt, 1) != SQLITE_NULL)
                s.last_commit_ms = sqlite3_column_int64(stmt, 1);
        }
        sqlite3_finalize(stmt);
    }
    return s;
}

void EktroMemoryStore::clear_all(bool confirm) {
    if (!confirm) throw std::invalid_argument("clear_all requires confirm=true");
    std::lock_guard<std::mutex> g(mu_);
    sqlite3_exec(conn_, "DELETE FROM commit_log", nullptr, nullptr, nullptr);
    sqlite3_exec(conn_, "DELETE FROM word_freq", nullptr, nullptr, nullptr);
    sqlite3_exec(conn_, "DELETE FROM phrase_pair", nullptr, nullptr, nullptr);
    // D-009 P0.8: VACUUM 必须在事务外
    sqlite3_exec(conn_, "COMMIT", nullptr, nullptr, nullptr);  // 关闭任何隐式 tx
    sqlite3_exec(conn_, "VACUUM", nullptr, nullptr, nullptr);
}

}  // namespace ektro
