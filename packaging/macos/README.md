# ektro-pen sync daemon · macOS LaunchAgent

把 `python -m sync daemon` 包装成 macOS LaunchAgent,登录后自动启动、崩溃自动重启。

## 前置

1. 已 clone ektro-pen 仓库
2. Python 3.10+ (stdlib only,无额外 pip 依赖)
3. 已链接到 ektroai.com:
   ```bash
   cd /path/to/ektro-pen
   PYTHONPATH=src python3 -m auth link
   ```

## 安装

```bash
cd /path/to/ektro-pen
./packaging/macos/install-launchagent.sh
```

脚本会:
- 自动检测 `python3` 路径
- 渲染 plist (替换 `{{REPO_ROOT}}` / `{{PYTHON}}` / `{{LOG_DIR}}`)
- 写入 `~/Library/LaunchAgents/com.ektro.sync.plist`
- `launchctl load` 立即启动 + 登录自动起

## 验证

```bash
# 服务状态
launchctl list | grep ektro.sync
# 应该看到: <PID>  0  com.ektro.sync

# 实时日志
tail -f ~/Library/Logs/Ektro/sync.out.log

# 当前 sync 进度
cd /path/to/ektro-pen && PYTHONPATH=src python3 -m sync pending
```

## 卸载

```bash
./packaging/macos/uninstall-launchagent.sh
```

仅卸载守护进程, **不会动**:
- 本地 SQLite 数据 (`~/Library/Application Support/Ektro/ektro.db`)
- Keychain 凭证 (Service `ektro-pen`)
- 日志 (`~/Library/Logs/Ektro/`)

彻底解绑账号请额外跑 `python3 -m auth unlink --confirm`.

## 文件清单

| 文件 | 用途 |
|------|------|
| `com.ektro.sync.plist.template` | LaunchAgent plist 模板 (含占位符) |
| `install-launchagent.sh` | 渲染 + 装载 |
| `uninstall-launchagent.sh` | 卸载 + 提示数据保留 |

## 设计要点

- **KeepAlive 仅崩溃时拉起**: `SuccessfulExit=false` + `Crashed=true` —
  daemon 主动退出 (链接失效) 时不无脑重启,避免反复打错码
- **ThrottleInterval=30s**: 防 fast-cycle
- **ProcessType=Background**: 不抢前台资源
- **PATH 含 /opt/homebrew/bin**: 适配 Apple Silicon Homebrew python3

## Troubleshoot

**服务装了但 PID 是 `-`**: daemon 启动后立即退出. 通常因为未链接.
```bash
tail -20 ~/Library/Logs/Ektro/sync.err.log
PYTHONPATH=src python3 -m auth status
```

**修改 plist 后不生效**: launchctl 需要 unload + load 重新读
```bash
launchctl unload ~/Library/LaunchAgents/com.ektro.sync.plist
launchctl load ~/Library/LaunchAgents/com.ektro.sync.plist
```

**端口/资源占用**: sync daemon 不监听任何端口 — 它是纯 HTTP 客户端 (只对外发请求)
