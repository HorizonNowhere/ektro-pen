// SPDX-License-Identifier: Apache-2.0
// EKTRO D-015: C++17-safe bridge for weasel integration.
// All C++20 (std::span) stays behind this; RimeWithWeasel (C++17) sees
// only std::vector / std::string. ABI 隔离, weasel 不需改 C++ 标准。
//
// ABI 契约 (swarm type-design 提醒): 跨 TU 传 std::string/std::vector
// 仅当 ektro.lib 与 weasel 用「同一 MSVC 工具集 + Release /MT」时安全
// (同 CRT / 同 _ITERATOR_DEBUG_LEVEL / 同一堆)。构建配置漂移 → 编译失败:
#pragma once

#if defined(_MSC_VER) && defined(_DEBUG)
#error "ektro_bridge: must build Release /MT to match weasel CRT/STL ABI (D-015)"
#endif

#include <string>
#include <vector>

namespace ektro_bridge {

// 初始化端侧 store + reranker (幂等; 失败 → log 并降级直通)。
// noexcept: 绝不让异常逸出到 weasel C++17 调用栈 (ABI 边界硬隔离)。
void init(const std::string& db_path) noexcept;

// 返回 *空* (直通, 用原始顺序), 或 [0, candidates.size()) 的*完整排列*,
// 其中 out[newpos] = 原始索引。调用方依赖 size()==0 或 ==candidates.size()。
// noexcept 保证。context / recent_outputs 当前预留 (调用方传空), 后续接线。
std::vector<int> rerank_order(
    const std::vector<std::string>& candidates,
    const std::string& context /* reserved */,
    const std::vector<std::string>& recent_outputs /* reserved */) noexcept;

// 落库学习 (公理② 不离开磁盘)。noexcept 保证。
void log_commit(const std::string& output) noexcept;

}  // namespace ektro_bridge
