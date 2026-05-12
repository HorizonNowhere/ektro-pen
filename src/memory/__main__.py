"""
ektro-cli — 用户面板（命令行版）

设计原则（CLAUDE.md 公理 ③）：
- 6 个核心子命令，每个一行帮助
- 默认不彩色（终端兼容）
- 所有 destructive 操作要 --confirm

用法:
    python -m memory <command> [args]

示例:
    python -m memory status
    python -m memory recent 20
    python -m memory top 30
    python -m memory exclude add Notepad.exe "private notes"
    python -m memory exclude list
    python -m memory exclude remove Notepad.exe
    python -m memory export --out backup.json
    python -m memory clear --confirm
    python -m memory config get theme
    python -m memory config set theme dark
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
from pathlib import Path

# Force UTF-8 stdout (Windows defaults to GBK)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make memory module importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.store import EktroMemoryStore  # noqa: E402

DEFAULT_DB = Path(os.environ.get("EKTRO_DB", str(Path.home() / "AppData" / "Roaming" / "EKTRO" / "user.db")))


def fmt_ts(ms: int | None) -> str:
    if ms is None:
        return "-"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def cmd_status(store: EktroMemoryStore, _args) -> int:
    s = store.stats()
    cfg = store.all_config()
    print(f"=== EKTRO Memory Store ===")
    print(f"DB path:           {store.db_path}")
    print(f"DB size:           {fmt_bytes(s['db_size_bytes'])}")
    print(f"Total commits:     {s['total_commits']:,}")
    print(f"Unique chars:      {s['unique_chars']:,}")
    print(f"Unique pairs:      {s['unique_phrase_pairs']:,}")
    print(f"First commit:      {fmt_ts(s['first_commit_ms'])}")
    print(f"Last commit:       {fmt_ts(s['last_commit_ms'])}")
    print(f"\n=== Config ===")
    for k, v in cfg.items():
        print(f"  {k:25s} = {v}")
    excluded = store.list_excluded_apps()
    if excluded:
        print(f"\n=== Privacy Excluded ({len(excluded)}) ===")
        for pattern, reason, ts in excluded:
            print(f"  {pattern:30s}  [{fmt_ts(ts)}]  {reason}")
    return 0


def cmd_recent(store: EktroMemoryStore, args) -> int:
    rows = store.recent_outputs(limit=args.n)
    if not rows:
        print("(no commits yet)")
        return 0
    print(f"{'TIME':20s} {'APP':20s} {'INPUT':25s} OUTPUT")
    print("-" * 90)
    for r in rows:
        app = (r.app_name or "")[:20]
        inp = (r.input_raw or "")[:25]
        mark = " *" if r.user_picked else "  "
        print(f"{fmt_ts(r.timestamp):20s} {app:20s} {inp:25s} {r.output}{mark}")
    print(f"\n({len(rows)} rows · * = user-picked)")
    return 0


def cmd_top(store: EktroMemoryStore, args) -> int:
    rows = store.top_words(limit=args.n)
    if not rows:
        print("(no data yet)")
        return 0
    print(f"{'RANK':5s} {'CHAR':6s} {'COUNT':10s} LAST USED")
    print("-" * 50)
    for i, w in enumerate(rows, 1):
        print(f"{i:5d} {w.word:6s} {w.count:10d} {fmt_ts(w.last_used)}")
    return 0


def cmd_exclude(store: EktroMemoryStore, args) -> int:
    if args.action == "add":
        store.add_excluded_app(args.pattern, args.reason or "")
        print(f"Added: {args.pattern}")
    elif args.action == "list":
        rows = store.list_excluded_apps()
        if not rows:
            print("(no exclusions)")
            return 0
        for p, r, ts in rows:
            print(f"  {p}  -- {r}  ({fmt_ts(ts)})")
    elif args.action == "remove":
        store.remove_excluded_app(args.pattern)
        print(f"Removed: {args.pattern}")
    return 0


def cmd_export(store: EktroMemoryStore, args) -> int:
    data = store.export_all()
    out_path = Path(args.out) if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Exported {len(data['commits'])} commits → {out_path}")
    else:
        # 默认 stdout（pipe 友好）
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    return 0


def cmd_clear(store: EktroMemoryStore, args) -> int:
    if not args.confirm:
        print("ERROR: clear is destructive. Pass --confirm to proceed.")
        return 1
    stats_before = store.stats()
    store.clear_all(confirm=True)
    print(f"Cleared {stats_before['total_commits']} commits, {stats_before['unique_chars']} chars.")
    print("(Privacy exclusions and config preserved.)")
    return 0


def cmd_config(store: EktroMemoryStore, args) -> int:
    if args.action == "list":
        for k, v in store.all_config().items():
            print(f"{k} = {v}")
    elif args.action == "get":
        v = store.get_config(args.key)
        if v is None:
            print(f"(not set: {args.key})")
            return 1
        print(v)
    elif args.action == "set":
        store.set_config(args.key, args.value)
        print(f"OK: {args.key} = {args.value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ektro-cli",
        description="EKTRO memory store admin tool — your data, your control.",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"DB path (default: {DEFAULT_DB})")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show DB stats + config")

    pr = sub.add_parser("recent", help="show recent commits")
    pr.add_argument("n", type=int, nargs="?", default=20)

    pt = sub.add_parser("top", help="show top frequent chars")
    pt.add_argument("n", type=int, nargs="?", default=30)

    pe = sub.add_parser("exclude", help="manage privacy exclusions")
    pe_sub = pe.add_subparsers(dest="action", required=True)
    pe_add = pe_sub.add_parser("add", help="add an app to exclusion")
    pe_add.add_argument("pattern")
    pe_add.add_argument("reason", nargs="?", default="")
    pe_sub.add_parser("list", help="list exclusions")
    pe_rm = pe_sub.add_parser("remove", help="remove an exclusion")
    pe_rm.add_argument("pattern")

    px = sub.add_parser("export", help="export everything as JSON")
    px.add_argument("--out", help="output file (default: stdout)")

    pc = sub.add_parser("clear", help="delete all commits/words/pairs (destructive)")
    pc.add_argument("--confirm", action="store_true", help="required to proceed")

    pcfg = sub.add_parser("config", help="get/set/list config")
    pcfg_sub = pcfg.add_subparsers(dest="action", required=True)
    pcfg_sub.add_parser("list")
    pcfg_get = pcfg_sub.add_parser("get")
    pcfg_get.add_argument("key")
    pcfg_set = pcfg_sub.add_parser("set")
    pcfg_set.add_argument("key")
    pcfg_set.add_argument("value")

    return p


def main():
    """
    CLI 主入口 (D-011 P0.5.3: 顶层异常拦截，不打用户脸)。

    任何未预期异常都打印简短错误 + 写日志，退出码 2。
    日志路径用户可看（status 命令会展示）。
    """
    import sqlite3
    try:
        args = build_parser().parse_args()
    except SystemExit:
        raise   # argparse 的正常退出（--help / 错误参数）

    rc = 0
    store = None
    try:
        store = EktroMemoryStore(args.db)
        cmd = {
            "status": cmd_status,
            "recent": cmd_recent,
            "top": cmd_top,
            "exclude": cmd_exclude,
            "export": cmd_export,
            "clear": cmd_clear,
            "config": cmd_config,
        }[args.cmd]
        rc = cmd(store, args)
    except (sqlite3.Error, OSError) as e:
        # 让用户看到友好错误而不是 Python traceback
        sys.stderr.write(f"ERROR: {e}\n")
        sys.stderr.write(f"详细信息见日志: %LOCALAPPDATA%\\Ektro\\logs\\ektro.log\n")
        try:
            from common.logging import get_logger
            get_logger(__name__).exception("CLI fatal error")
        except Exception:
            pass  # logging 也挂了 → 至少 stderr 有原始错误
        rc = 2
    except KeyboardInterrupt:
        sys.stderr.write("\n(用户中断)\n")
        rc = 130
    finally:
        if store is not None:
            store.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
