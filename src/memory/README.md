# EKTRO Memory Module (Python Reference Implementation)

> 单点真相（**Source of Truth**）for the EKTRO memory subsystem.
> C++ port for weasel integration **必须逐字段对照本模块**。

---

## 为什么是 Python？

[CLAUDE.md](../../CLAUDE.md) 公理 ② 要求"不离开磁盘"。底层引擎将集成进 librime/weasel (C++)，但本 Python 模块作为：

1. **Schema 定义的唯一权威**：[`schema.py`](./schema.py) 是 SQLite DDL，C++ 集成时必须复制相同表结构
2. **用户面板的实际后端**：[`__main__.py`](./__main__.py) 提供 ektro-cli，用户随时可读/导出/清空自己的数据
3. **数据迁移工具**：未来 schema v1→v2 升级时本模块带 migration 函数
4. **测试基线**：C++ 实现完成后用 [`tests/memory/`](../../tests/memory/) 同一套测试做 cross-check

设计哲学：**Python 是测试和工具的快车道，C++ 是运行时的高速公路**。

---

## 模块结构

```
src/memory/
├── __init__.py         # package marker
├── schema.py           # SQLite DDL + 版本管理
├── store.py            # EktroMemoryStore 主类（线程安全）
├── __main__.py         # ektro-cli 命令行工具
└── README.md           # 本文件

tests/memory/
├── test_store.py             # 28 个单元测试
├── bench_memory.py           # 性能 benchmark
├── generate_mock_data.py     # 真实品质模拟数据生成器
└── mock.db / bench.db        # 测试 DB（git ignore）
```

---

## 性能数据（实测）

测试环境：i5-12400F, Windows 11, Python 3.11, sqlite3 内置, WAL 模式

| 操作 | P50 | P99 | SLO 预算 | 余量 |
|------|-----|-----|----------|------|
| `log_commit` (写) | 0.125 ms | 4.2 ms | 5 ms | 1.2x |
| `recent_outputs(20)` | 0.062 ms | 0.15 ms | 3 ms | **20x** |
| `recent_outputs(200)` | 0.436 ms | 1.36 ms | 3 ms | 2.2x |
| `word_freq_lookup(20)` | 0.011 ms | 0.05 ms | 3 ms | **60x** |
| `top_words(30)` | 0.015 ms | 0.03 ms | N/A | — |
| `export_all` (5000 条) | 15.3 ms | 28.3 ms | N/A | — |

DB 体积：5000 commits = **0.62 MB**（含 word_freq + phrase_pair 索引）

**结论**：记忆系统在性能上不是瓶颈。 EktroRerankFilter 30ms 预算里 SQLite 查询占 ≤1ms，剩 29ms 给 GRU 推理。

---

## 快速上手

### 作为 Python 库

```python
from memory.store import EktroMemoryStore

store = EktroMemoryStore("user.db")

# IME 集成调用点（design.md §5 学习闭环）
store.log_commit(
    input_raw="nihao",
    output="你好",
    app_name="Code.exe",
    user_picked=False,    # 用户没按 Tab
    duration_ms=350,      # 从首字到 commit 的耗时
)

# Rerank filter 查询（design.md §3）
recent = store.recent_outputs(limit=20)  # 上下文
freq = store.word_freq_lookup(["你", "好", "世"])  # 个性化打分
pairs = store.phrase_pair_lookup("你")  # 二元组 fallback

store.close()
```

### 作为 CLI 工具

```bash
cd E:\CLAUDE\EKTRO输入法
set PYTHONPATH=src

# 看现状
python -m memory status

# 看最近 20 条输入
python -m memory recent 20

# 看高频字
python -m memory top 30

# 隐私：把某个 app 加入排除
python -m memory exclude add Notepad.exe "私密笔记"
python -m memory exclude list
python -m memory exclude remove Notepad.exe

# 数据导出（备份）
python -m memory export --out backup.json

# 全部清空（destructive，必须加 --confirm）
python -m memory clear --confirm

# 配置
python -m memory config list
python -m memory config set theme dark
python -m memory config get predictor_delay_ms
```

默认 DB 路径：`%APPDATA%\EKTRO\user.db`（可用 `--db` 覆盖，或环境变量 `EKTRO_DB`）

---

## Schema 速览

完整 DDL 见 [`schema.py`](./schema.py)，本节是高层视图：

```
commit_log              ← 每次 commit 一条
├ id PK                   行 id
├ timestamp INT          unix ms
├ input_raw TEXT         拼音原文
├ output TEXT            commit 的中文
├ app_name TEXT          焦点应用
├ context_id INT         会话 id
├ user_picked BOOL       是否按 Tab 切换
└ duration_ms INT        打字节奏

word_freq               ← 字粒度频率
├ word TEXT PK           单字
├ count INT              出现次数
└ last_used INT          unix ms

phrase_pair             ← 二元组 (前字, 当前字)
├ prev TEXT
├ curr TEXT
├ count INT
└ PK (prev, curr)

privacy_exclude         ← 不落库的应用 / scope
├ pattern TEXT PK
├ reason TEXT
└ created_at INT

config                  ← ≤6 项核心设置
├ key TEXT PK
└ value TEXT
```

---

## 隐私边界（design.md §5）

`log_commit` 的多层拦截，全部返回 `None`（不落库）：

1. **`is_password_field=True`** —— TSF 检测到 `TF_ATTR_INPUTSCOPE` 含 `IS_PASSWORD`
2. **正则匹配**：
   - 8-64 字符的字母数字+符号样式 → 视为密码
   - 16-19 位连续数字 → 视为银行卡号
   - 17 位数字 + 末尾校验 → 中国身份证
   - email 格式 → 视为联系方式
3. **`privacy_exclude` 表命中**：用户主动标记的 app/scope

**正常中文输入永不误杀**（测试用例 `test_normal_chinese_not_rejected`）。

---

## 测试

```bash
cd E:\CLAUDE\EKTRO输入法
set PYTHONPATH=src
python -m unittest tests.memory.test_store -v
```

应该看到 `Ran 28 tests in 0.3s OK`。

性能 benchmark：
```bash
python tests/memory/bench_memory.py
```

---

## 移植到 C++ 的检查清单（Week 4-5 用）

集成进 weasel/librime 时：

- [ ] 复制 [`schema.py`](./schema.py) 的 SQL 字符串到 C++ const char*
- [ ] 用 SQLite C API 调用 `sqlite3_exec` 跑 schema
- [ ] 把 `EktroMemoryStore::log_commit` 移植成 C++ 类（线程安全 mutex）
- [ ] 隐私正则用 C++ `std::regex` 或 RE2（推荐 RE2 防 ReDoS）
- [ ] 在 weasel commit 钩子里调用 `log_commit`
- [ ] **运行 `tests/memory/test_store.py` 的等价 C++ 测试**确保字段一一对应
- [ ] 用户面板（Week 3）从 SQLite 查询，与本 Python CLI 输出对照验证

---

## 相关文档

- 顶层产品宪法：[CLAUDE.md](../../CLAUDE.md)
- 设计细化：[openspec/changes/ektro-mvp/design.md](../../openspec/changes/ektro-mvp/design.md) §3 §5 §8
- 任务清单：[openspec/changes/ektro-mvp/tasks.md](../../openspec/changes/ektro-mvp/tasks.md) Week 2 / 4
- 决策日志：[docs/decisions.md](../../docs/decisions.md)
