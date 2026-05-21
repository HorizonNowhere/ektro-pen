"""
ektro-pen sync 管理 CLI。

用法:
    python -m sync sync-now                立刻触发一次增量 sync
    python -m sync backfill <mode>         首次回填 (mode: full / aggregate / none)
    python -m sync pending                 查看待上传条数 + cursor 状态
    python -m sync heartbeat               立刻发一次心跳 + 拉删除通告

详见 docs/ime-ingest-contract.md。
"""
from __future__ import annotations

import argparse
import os
import platform
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth.token_manager import LinkInvalidError, NotLinkedError, TokenManager
from memory import schema
from memory.link_store import LinkStore
from sync import backfill as backfill_module
from sync import uploader as uploader_module
from sync.sync_client import SyncClient


def _default_db_path() -> Path:
    sys_name = platform.system()
    env = os.environ.get("EKTRO_DB_PATH")
    if env:
        return Path(env).expanduser()
    if sys_name == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Ektro" / "ektro.db"
    if sys_name == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home()))
        return Path(appdata) / "Rime" / "ektro" / "ektro.db"
    return Path.home() / ".local" / "share" / "ektro" / "ektro.db"


def _open_full() -> tuple[sqlite3.Connection, threading.Lock, LinkStore, TokenManager, SyncClient]:
    db_path = _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.init_db(conn)

    lock = threading.Lock()
    link_store = LinkStore(conn, lock)
    if (endpoint := os.environ.get("EKTRO_ENDPOINT")):
        link_store.set_ektro_endpoint(endpoint)
    tm = TokenManager(link_store)
    sc = SyncClient(link_store, tm)
    return conn, lock, link_store, tm, sc


def _fmt_ts(ms: int | None) -> str:
    if ms is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))


# ───────── 子命令 ─────────

def cmd_sync_now(args: argparse.Namespace) -> int:
    conn, lock, link_store, tm, sc = _open_full()
    if not tm.is_linked():
        print("未链接 — 先跑 `python -m auth link`", file=sys.stderr)
        return 1

    outcome = uploader_module.sync_once(
        conn=conn, lock=lock,
        link_store=link_store, token_manager=tm, sync_client=sc,
    )
    if outcome.error == "link_invalid":
        print("✗ 链接失效 — 重新链接: python -m auth link", file=sys.stderr)
        return 4
    if outcome.error == "rate_limit":
        print(f"✗ 限流,建议 {outcome.rate_limited_for}s 后重试", file=sys.stderr)
        return 5
    if outcome.error:
        print(f"✗ 错误: {outcome.error}", file=sys.stderr)
        return 2

    print(f"✓ 拉 {outcome.pulled} 条 / 上传 {outcome.uploaded} / 去重 {outcome.deduplicated}")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    if args.mode not in ("full", "aggregate", "none"):
        print(f"无效 mode: {args.mode} (应为 full / aggregate / none)", file=sys.stderr)
        return 1

    conn, lock, link_store, tm, sc = _open_full()
    if not tm.is_linked():
        print("未链接 — 先跑 `python -m auth link`", file=sys.stderr)
        return 1

    if not args.confirm and args.mode != "none":
        # full / aggregate 涉及上传,警告
        count = conn.execute(
            "SELECT count(*) FROM commit_log"
            if args.mode == "full"
            else "SELECT (SELECT count(*) FROM word_freq) + (SELECT count(*) FROM phrase_pair)"
        ).fetchone()[0]
        print(f"⚠ 即将以 {args.mode} 模式上传约 {count} 条数据", file=sys.stderr)
        print("  添加 --confirm 确认", file=sys.stderr)
        return 1

    print(f"启动 {args.mode} 回填...")
    result = backfill_module.run_backfill(
        conn=conn, lock=lock, link_store=link_store,
        token_manager=tm, sync_client=sc, mode=args.mode,
    )
    if not result.completed:
        print(f"✗ 失败: {result.error}", file=sys.stderr)
        if result.rate_limited_for:
            print(f"  建议 {result.rate_limited_for}s 后再跑 python -m sync backfill {args.mode}",
                  file=sys.stderr)
        else:
            print(f"  再跑 python -m sync backfill {args.mode} 续传", file=sys.stderr)
        return 2

    print(f"✓ 完成 — 拉 {result.total_pulled} / 上传 {result.total_uploaded}")
    print(f"  继续增量 sync: python -m sync sync-now")
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    conn, lock, link_store, _, _ = _open_full()
    cursor = link_store.get_sync_cursor()

    # 实算待上传 (cursor 后的 commit_log 行数)
    pending_actual = conn.execute(
        "SELECT count(*) FROM commit_log WHERE id > ?",
        (cursor.last_synced_commit_id,),
    ).fetchone()[0]

    print(f"待上传:        {pending_actual} 条")
    print(f"已上传累计:    {cursor.total_uploaded} 条")
    print(f"Cursor:        {cursor.last_synced_commit_id}")
    print(f"上次同步:      {_fmt_ts(cursor.last_sync_at)}")
    print(f"上次尝试:      {_fmt_ts(cursor.last_attempt_at)}")
    if cursor.last_error:
        print(f"上次错误:      {cursor.last_error}")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """常驻守护进程 — 与 IME 主进程并行跑,后台 sync。

    生命周期:
    - SIGINT / SIGTERM 优雅停止 (等当前 sync 周期完成)
    - 凭证失效 (link_invalid) 自动退出 (TokenManager 已清状态)
    - 用 nohup / systemd / launchctl 启动持久化

    NOTE: 这是 v0.4 release 之前的临时方式. 正式打包后改为
          launchctl plist (macOS) / systemd service (Linux) / Windows service.
    """
    import signal

    conn, lock, link_store, tm, sc = _open_full()
    if not tm.is_linked():
        print("未链接 — 先跑 `python -m auth link`", file=sys.stderr)
        return 1

    daemon = uploader_module.UploaderDaemon(
        conn=conn, lock=lock,
        link_store=link_store, token_manager=tm, sync_client=sc,
        sync_interval=args.interval,
        heartbeat_interval=args.heartbeat_interval,
    )

    def _shutdown(signum, frame):
        print(f"\n收到信号 {signum},优雅停止...")
        daemon.stop(timeout=15)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"ektro-pen sync daemon 启动")
    print(f"  device:   {link_store.get_device_link().device_id}")
    print(f"  endpoint: {link_store.get_device_link().ektro_endpoint}")
    print(f"  sync:     每 {args.interval}s")
    print(f"  heartbeat:每 {args.heartbeat_interval}s")
    print(f"  ctrl-c 退出")

    daemon.start()
    try:
        # 主线程等子线程自然结束 (link_invalid 时子线程自己退)
        while daemon._thread and daemon._thread.is_alive():
            daemon._thread.join(timeout=1)
        print("\nsync daemon 退出 (可能链接已失效,跑 python -m auth status 查状态)")
        return 0
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)
        return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    _, _, link_store, tm, sc = _open_full()
    if not tm.is_linked():
        print("未链接", file=sys.stderr)
        return 1

    resp = uploader_module.heartbeat_once(link_store=link_store, sync_client=sc)
    if resp is None:
        print("✗ heartbeat 失败 (网络 / 凭证 / 服务端不可达)", file=sys.stderr)
        return 2

    print(f"服务端已收:    {resp['server_total_received']} 条")
    print(f"设备状态:      {resp['device_status']}")
    notices = resp.get("deletion_notices") or []
    if notices:
        print(f"删除通告 ({len(notices)} 条):")
        for n in notices:
            r_from = n.get("range_from_ms")
            r_to = n.get("range_to_ms")
            kind = n.get("initiated_by", "?")
            print(f"  [{kind}] {_fmt_ts(r_from)} ~ {_fmt_ts(r_to)} · {n.get('deleted_count')} 条")
    else:
        print("无新删除通告")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m sync",
        description="ektro-pen sync 管理 — 旁路同步本机记忆到 ektroai.com",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync-now", help="立刻触发一次增量 sync")

    pb = sub.add_parser("backfill", help="首次回填 (full / aggregate / none)")
    pb.add_argument("mode", choices=["full", "aggregate", "none"])
    pb.add_argument("--confirm", action="store_true", help="确认上传 (full/aggregate)")

    sub.add_parser("pending", help="查看待上传数 + cursor")
    sub.add_parser("heartbeat", help="立刻发一次心跳 + 拉删除通告")

    pd = sub.add_parser("daemon", help="常驻守护进程 (与 IME 并行跑后台 sync)")
    pd.add_argument("--interval", type=int, default=300,
                    help="sync 周期秒 (默认 300=5min)")
    pd.add_argument("--heartbeat-interval", type=int, default=3600,
                    help="heartbeat 周期秒 (默认 3600=1h)")

    return p


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "sync-now": cmd_sync_now,
        "backfill": cmd_backfill,
        "pending": cmd_pending,
        "heartbeat": cmd_heartbeat,
        "daemon": cmd_daemon,
    }
    try:
        return handlers[args.cmd](args)
    except NotLinkedError as e:
        print(f"未链接: {e}", file=sys.stderr)
        return 3
    except LinkInvalidError as e:
        print(f"链接失效: {e}\n  → python -m auth link 重新链接", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("\n中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
