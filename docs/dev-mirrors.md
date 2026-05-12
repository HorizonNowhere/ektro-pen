# 开发依赖镜像清单

> 国内访问国外资源在 EKTRO 开发中是隐性瓶颈。本文档记录已验证的镜像和下载策略。
> 当 6 个月后你（或新贡献者）从零搭建开发环境时，**先读这一页可省 1-2 小时**。

> Last updated: 2026-05-11 · 验证人: getwinccc

---

## llama.cpp

### 官方 release
- **源**: https://github.com/ggml-org/llama.cpp/releases
- **下载速度**: 0.13 MB/s（国内宽带，PowerShell `Invoke-WebRequest`）
- **包大小**: Windows CPU x64 zip ≈ 15 MB
- **结论**: 大小可控，**直接走 GitHub**即可，~2 分钟完成

### 关键文件
- `llama-bXXXX-bin-win-cpu-x64.zip` — 纯 CPU，含 alderlake/zen4 等架构 DLL
- `llama-bXXXX-bin-win-cuda-12.4-x64.zip` — CUDA 12.4
- `llama-bXXXX-bin-win-cuda-13.1-x64.zip` — CUDA 13.1（更新）
- `llama-bXXXX-bin-win-vulkan-x64.zip` — Vulkan，AMD/Intel GPU
- 我们 v0.1 基线用 **CPU 版本**

### 版本节奏
llama.cpp 每天有新 release（如 b9102 = 2026-05-11）。**不要锁定版本号**，CLAUDE.md 中只说"基于 b91xx 时代"。

---

## Qwen3-0.6B GGUF 模型

### 已验证下载源（按可用性排序）

| # | 源 | URL 模板 | 速度（实测） | 备注 |
|---|----|---------|------------|------|
| 1 | ModelScope (阿里) | `https://modelscope.cn/api/v1/models/Qwen/Qwen3-0.6B-GGUF/repo?Revision=master&FilePath=<file>` | ~0.1-0.5 MB/s | 仅 Q8_0；起步快，稳定后限速 |
| 2 | hf-mirror.com | `https://hf-mirror.com/unsloth/Qwen3-0.6B-GGUF/resolve/main/<file>` | ~0.1 MB/s | 量化齐全（IQ4_XS/Q4_K_M/...） |
| 3 | HuggingFace 官方 | `https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/resolve/main/<file>` | 几乎不可用 | 国内大概率超时 |

### 推荐量化选型

| 量化 | 大小 | 用途 |
|------|------|------|
| Q8_0 | 639 MB | 精度上界测试 |
| **IQ4_XS** | 368 MB | **EKTRO v0.1 默认** |
| Q4_K_M | 397 MB | 备选，与 IQ4_XS 差距小 |
| Q2_K | 296 MB | 极端精度敏感测试 |

### 加速技巧

```powershell
# 单线程慢，但稳定可靠（不依赖额外工具）
Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -TimeoutSec 1800
```

```powershell
# 用 llama-cli 内置下载（实测速度与手动相当，但管理省心）
$env:HF_ENDPOINT='https://hf-mirror.com'
.\llama-cli.exe -hf unsloth/Qwen3-0.6B-GGUF:IQ4_XS
# 会自动下载到 ~/.cache/llama.cpp/
```

```bash
# 如果未来装了 aria2c（推荐），可多线程
aria2c -x 8 -s 8 https://hf-mirror.com/unsloth/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-IQ4_XS.gguf
```

---

## RIME / weasel / librime

| 资源 | URL | 备注 |
|------|-----|------|
| rime/weasel | https://github.com/rime/weasel | git clone, 国内速度 1-2 MB/s |
| rime/librime | https://github.com/rime/librime | 同上 |
| iDvel/rime-ice (雾凇拼音) | https://github.com/iDvel/rime-ice | 词库 |
| amzxyz/rime_wanxiang (万象拼音) | https://github.com/amzxyz/rime_wanxiang | 词库 |
| amzxyz/RIME-LMDG | https://github.com/amzxyz/RIME-LMDG | 8-gram 模型 |

### 提速建议

- 用 ghproxy.com 或 mirror.ghproxy.com 镜像 GitHub：
  ```
  git clone https://ghproxy.com/https://github.com/rime/weasel.git
  ```
- 或配置 git 全局 proxy（如有）

---

## Python 包（pip）

### 镜像

```bash
# 临时
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <pkg>

# 永久
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### EKTRO 用到的关键包

- `huggingface_hub`（备用下载方式）
- `numpy`（GRU rerank 训练）
- `onnxruntime`（GRU 推理候选）
- `pytest`（测试）

---

## Rust（如果走选项 C 路线）

```powershell
# rustup 国内镜像
$env:RUSTUP_DIST_SERVER = 'https://rsproxy.cn'
$env:RUSTUP_UPDATE_ROOT = 'https://rsproxy.cn/rustup'
```

Cargo 镜像 `~/.cargo/config.toml`：

```toml
[source.crates-io]
replace-with = 'rsproxy-sparse'

[source.rsproxy-sparse]
registry = "sparse+https://rsproxy.cn/index/"
```

---

## 网络拓扑诊断

如果下载超慢，先诊断瓶颈：

```powershell
# 测延迟
Test-NetConnection hf-mirror.com -Port 443
Test-NetConnection modelscope.cn -Port 443

# 测带宽（用 small file）
$sw = [Diagnostics.Stopwatch]::StartNew()
Invoke-WebRequest 'https://hf-mirror.com/api' -OutFile $env:TEMP\test.bin
$sw.Stop()
Write-Host "$($sw.Elapsed.TotalSeconds)s"
```

---

## 最差情况预案

如果以上所有镜像都不可用：
1. **物理介质**：把模型 / 工具 zip 拷到 U 盘或别人机器上
2. **代理 / VPN**：如果有合规代理，用之
3. **替代量化**：用更小的 IQ3 或 Q2_K 跑 spike，证明能力后再换
4. **延后到 GPU 路线**：先做 RTX 4070 Ti 上的 GPU 测试（不依赖端侧 CPU 路径）

---

*相关：[cycle1-spike-day1.md](./cycle1-spike-day1.md) · [decisions.md](./decisions.md)*
