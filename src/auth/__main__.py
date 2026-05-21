"""
ektro-pen 链接管理 CLI。

用法:
    python -m auth status              查看当前链接状态
    python -m auth link                启动浏览器 OAuth 链接流程
    python -m auth link-device         降级用 user_code (无浏览器场景)
    python -m auth unlink              解绑当前设备
    python -m auth unlink --local-only 仅清本地凭证 (服务端不调)

环境变量:
    EKTRO_DB_PATH    默认 ~/Library/Application Support/Ektro/ektro.db (macOS) /
                     %APPDATA%/Rime/ektro/ektro.db (Windows) / ~/.local/share/ektro/ektro.db (Linux)
    EKTRO_ENDPOINT   覆盖默认 https://ektroai.com
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import threading
import time
from pathlib import Path

# 让 stdlib 路径优先
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import device_grant as dg_module
from auth import link as link_module
from auth.token_manager import LinkInvalidError, NotLinkedError, TokenManager
from memory import schema
from memory.link_store import LinkStore


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


def _open_stores() -> tuple[LinkStore, TokenManager]:
    import sqlite3
    db_path = _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.init_db(conn)

    # 覆盖 endpoint (CLI 配置 / 测试)
    endpoint = os.environ.get("EKTRO_ENDPOINT")
    link_store = LinkStore(conn, threading.Lock())
    if endpoint:
        link_store.set_ektro_endpoint(endpoint)

    tm = TokenManager(link_store)
    return link_store, tm


def _fmt_ts(ms: int | None) -> str:
    if ms is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))


# ───────── 子命令 ─────────

def cmd_status(args: argparse.Namespace) -> int:
    link_store, tm = _open_stores()
    link = link_store.get_device_link()

    print(f"设备 ID:      {link.device_id}")
    print(f"设备名:       {link.device_label or '(未设置)'}")
    print(f"端点:         {link.ektro_endpoint}")
    print(f"链接状态:     {'已链接' if link.is_linked else '未链接'}")
    if link.is_linked:
        print(f"  账号:        {link.linked_user_handle or link.linked_user_id}")
        print(f"  链接时间:    {_fmt_ts(link.linked_at)}")
    elif link.revoked_at:
        print(f"  上次解绑:    {_fmt_ts(link.revoked_at)}")

    cursor = link_store.get_sync_cursor()
    print(f"\nSync 状态:")
    print(f"  已上传:      {cursor.total_uploaded} 条")
    print(f"  上次同步:    {_fmt_ts(cursor.last_sync_at)}")
    print(f"  待上传:      {cursor.pending_count}")
    if cursor.last_error:
        print(f"  上次错误:    {cursor.last_error}")

    backfill = link_store.get_backfill_state()
    if backfill.mode:
        print(f"\nBackfill:")
        print(f"  模式:        {backfill.mode}")
        print(f"  进度:        {backfill.total_uploaded}/{backfill.total_to_upload or '?'}")
        print(f"  状态:        {'已完成' if backfill.is_completed else '进行中'}")
        if backfill.error:
            print(f"  错误:        {backfill.error}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    link_store, tm = _open_stores()
    if tm.is_linked():
        print(f"已链接 {link_store.get_device_link().linked_user_handle or '账号'}", file=sys.stderr)
        print("如需重新链接,先 unlink", file=sys.stderr)
        return 1

    print(f"启动 OAuth Loopback 链接到 {link_store.get_device_link().ektro_endpoint}")
    print("浏览器将打开同意页 — 60s 内完成,否则超时...")

    result = link_module.link_account(
        link_store=link_store, token_manager=tm,
        device_label=args.label, timeout=args.timeout,
    )
    if result.ok:
        print(f"✓ 链接成功: {result.user_handle or result.user_id}")
        return 0
    print(f"✗ 链接失败: {result.error}", file=sys.stderr)
    if result.error_description:
        print(f"  {result.error_description}", file=sys.stderr)
    if result.error == "open_browser":
        print("  → 试试 device-grant 降级路径: python -m auth link-device", file=sys.stderr)
    return 2


def cmd_link_device(args: argparse.Namespace) -> int:
    link_store, tm = _open_stores()
    if tm.is_linked():
        print("已链接,先 unlink", file=sys.stderr)
        return 1

    session = dg_module.start_device_grant(
        link_store=link_store, device_label=args.label,
    )
    print()
    print(f"  ╭──────────────────────────────────────╮")
    print(f"  │  在浏览器打开:                       │")
    print(f"  │    {session.verification_uri:<34}│")
    print(f"  │                                      │")
    print(f"  │  输入这个 code:                      │")
    print(f"  │    {session.user_code:<34}│")
    print(f"  │                                      │")
    print(f"  │  (10 分钟内有效)                     │")
    print(f"  ╰──────────────────────────────────────╯")
    print()
    print("等待用户在浏览器批准...")

    result = dg_module.poll_until_complete(
        link_store=link_store, token_manager=tm, session=session,
        max_wait_seconds=args.timeout,
    )
    if result.ok:
        print(f"✓ 链接成功: {result.user_handle or result.user_id}")
        return 0
    print(f"✗ 链接失败: {result.error}", file=sys.stderr)
    return 2


def cmd_unlink(args: argparse.Namespace) -> int:
    link_store, tm = _open_stores()
    if not tm.is_linked():
        print("未链接,无需解绑")
        return 0

    if not args.confirm:
        print("⚠ 解绑后已上传的数据仍归你管理,但停止后续同步。", file=sys.stderr)
        print("  添加 --confirm 确认", file=sys.stderr)
        return 1

    handle = link_store.get_device_link().linked_user_handle or "账号"
    try:
        tm.revoke_local(call_server=not args.local_only)
    except Exception as e:
        print(f"⚠ 解绑时出错: {e}", file=sys.stderr)
        print("  本地状态仍尝试清理...", file=sys.stderr)
    print(f"✓ 已解绑 {handle}{'(仅本地)' if args.local_only else ''}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m auth",
        description="ektro-pen 链接管理 — 与 ektroai.com 之间的设备凭证",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="查看链接状态 + sync 进度")

    pl = sub.add_parser("link", help="启动浏览器 OAuth 链接 (主路径)")
    pl.add_argument("--label", help="设备显示名 (默认 hostname)")
    pl.add_argument("--timeout", type=float, default=60.0, help="等待秒数 (默认 60)")

    pd = sub.add_parser("link-device", help="Device Grant 降级 (无浏览器场景)")
    pd.add_argument("--label", help="设备显示名")
    pd.add_argument("--timeout", type=float, default=600.0, help="等待秒数 (默认 600)")

    pu = sub.add_parser("unlink", help="解绑当前设备")
    pu.add_argument("--confirm", action="store_true", help="确认解绑")
    pu.add_argument("--local-only", action="store_true",
                    help="仅清本地凭证,不调服务端 revoke (网络断时用)")

    return p


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "status": cmd_status,
        "link": cmd_link,
        "link-device": cmd_link_device,
        "unlink": cmd_unlink,
    }
    try:
        return handlers[args.cmd](args)
    except NotLinkedError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 3
    except LinkInvalidError as e:
        print(f"链接失效: {e}", file=sys.stderr)
        print("  → 重新链接: python -m auth link", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("\n中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
