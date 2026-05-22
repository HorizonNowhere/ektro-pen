# EKTRO C++ 模块独立构建（Cycle 2 Day 1-2）
# 集成进 weasel 时会被合并进 weasel CMakeLists.txt

cmake_minimum_required(VERSION 3.16)
project(ektro C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# D-014: MSVC 全局 UTF-8 (源码含中文注释/string, 防代码页错乱)
if(MSVC)
    add_compile_options(/utf-8)
endif()

# D-015: 静态 CRT (/MT) 以匹配 weasel —— 集成进 weaselx64.dll 时
# CRT 必须一致 (/MT vs /MD ABI 不兼容会 LNK2038 / 跨堆崩溃)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

option(EKTRO_BUILD_TESTS "Build cross-check unit tests" ON)

set(EKTRO_PLATFORM "windows" CACHE STRING "windows|macos|android|ios")
message(STATUS "EKTRO_PLATFORM=${EKTRO_PLATFORM}")
# macOS/iOS 前端用 Swift, 需要 C ABI 静态库; Android 需要 .so
if(EKTRO_PLATFORM STREQUAL "android")
    set(BUILD_SHARED_LIBS ON)
endif()

# ────────── 依赖 ──────────
find_package(SQLite3 QUIET)
if(SQLite3_FOUND)
    set(SQLITE3_LIB SQLite::SQLite3)
    set(EKTRO_USES_AMALGAMATION FALSE)
elseif(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/third_party/sqlite3.c)
    # 使用 vendored amalgamation (D-014)
    message(STATUS "EKTRO: building SQLite3 from vendored amalgamation")
    add_library(ektro_sqlite3 STATIC third_party/sqlite3.c)
    target_include_directories(ektro_sqlite3 PUBLIC third_party)
    target_compile_definitions(ektro_sqlite3 PRIVATE SQLITE_THREADSAFE=1)
    if(MSVC)
        target_compile_options(ektro_sqlite3 PRIVATE /utf-8 /W0)
    endif()
    set(SQLITE3_LIB ektro_sqlite3)
    set(EKTRO_USES_AMALGAMATION TRUE)
else()
    message(STATUS "SQLite3 not found and no amalgamation; using 'sqlite3' link name")
    set(SQLITE3_LIB sqlite3)
    set(EKTRO_USES_AMALGAMATION FALSE)
endif()

# 可选: cpp-httplib + nlohmann/json
# 用户从以下地址下载并放置:
#   third_party/httplib.h            (https://github.com/yhirose/cpp-httplib)
#   third_party/nlohmann/json.hpp    (https://github.com/nlohmann/json)
set(THIRD_PARTY_DIR ${CMAKE_CURRENT_SOURCE_DIR}/third_party)

# ────────── ektro 静态库 ──────────
add_library(ektro STATIC
    src/schema.cpp
    src/memory_store.cpp
    src/baseline_reranker.cpp
    src/baseline_predictor.cpp
    src/context_builder.cpp
    src/log.cpp
    src/predictor_client.cpp
    src/async_trigger.cpp
    src/ektro_sdk.cpp
    src/bridge.cpp
)
target_include_directories(ektro PUBLIC include)

# 第三方 header-only 库（如已下载）
if(EXISTS ${THIRD_PARTY_DIR}/httplib.h AND EXISTS ${THIRD_PARTY_DIR}/nlohmann/json.hpp)
    target_include_directories(ektro PRIVATE ${THIRD_PARTY_DIR})
    target_compile_definitions(ektro PRIVATE EKTRO_HAS_HTTPLIB=1 EKTRO_HAS_JSON=1)
    message(STATUS "EKTRO: cpp-httplib + nlohmann/json found (PredictorClient enabled)")
else()
    message(WARNING "EKTRO: cpp-httplib or nlohmann/json missing in third_party/; PredictorClient will return kServerDown stub. See INSTALL.md.")
endif()

target_link_libraries(ektro PUBLIC ${SQLITE3_LIB})

# vendored amalgamation 时, ektro 直接看到 third_party/ 头
if(EKTRO_USES_AMALGAMATION)
    target_include_directories(ektro PRIVATE third_party)
endif()

if(WIN32)
    target_link_libraries(ektro PUBLIC shell32)  # SHGetFolderPath
endif()

if(MSVC)
    # /utf-8: 让 MSVC 用 UTF-8 解析源码 (D-014 修复 — 中文注释/字符串不再乱码)
    # /W4 /WX-: 高警告但不当错
    target_compile_options(ektro PRIVATE /utf-8 /W4 /WX-)
else()
    target_compile_options(ektro PRIVATE -Wall -Wextra -Wpedantic)
endif()

# ────────── GoogleTest cross-check 测试 ──────────
if(EKTRO_BUILD_TESTS)
    enable_testing()

    # 优先用系统包，否则 FetchContent 自动下
    find_package(GTest QUIET)
    if(NOT GTest_FOUND)
        message(STATUS "EKTRO: GoogleTest not found locally, fetching via FetchContent...")
        include(FetchContent)
        FetchContent_Declare(
            googletest
            URL https://codeload.github.com/google/googletest/zip/refs/tags/v1.14.0
            DOWNLOAD_EXTRACT_TIMESTAMP TRUE
        )
        set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
        FetchContent_MakeAvailable(googletest)
    endif()

    add_executable(ektro_tests
        tests/test_memory_store.cpp
        tests/test_baseline_reranker.cpp
        tests/test_baseline_predictor.cpp
        tests/test_c_abi.cpp
        tests/test_bridge.cpp
        tests/test_schema_v2.cpp
    )
    target_link_libraries(ektro_tests PRIVATE ektro GTest::gtest GTest::gtest_main)
    add_test(NAME ektro_tests COMMAND ektro_tests)
    message(STATUS "EKTRO: GoogleTest ready, ektro_tests added")
endif()

message(STATUS "EKTRO C++ build configured. See INSTALL.md for next steps.")
