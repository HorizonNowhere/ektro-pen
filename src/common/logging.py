"""
EKTRO 统一日志模块（D-009 P0.2）

设计原则:
- **日志中绝不写 commit 内容**（公理 ②，所有用户文本泄露 = 隐私事故）
- 默认 INFO 级，DEBUG 通过 EKTRO_LOG_LEVEL 环境变量打开
- 日志写到 %LOCALAPPDATA%\Ektro\logs\ektro-YYYY-MM-DD.log，每天滚动
- Python 内置 logging 模块，零第三方依赖

用法:
    from common.logging import get_logger
    logger = get_logger(__name__)

    logger.info("Started memory store at %s", db_path)
    logger.error("Failed to insert: %r", err)   # 用 %r 而非 %s 避免字符串泄露

危险用法（绝不能写）:
    logger.info(f"commit: {output}")            # ❌ 泄露 commit 内容
    logger.debug("prompt = %s", prompt)         # ❌ 泄露上下文
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


_INITIALIZED = False


def _resolve_log_dir() -> Path:
    """日志目录优先级: EKTRO_LOG_DIR > LOCALAPPDATA > tempdir"""
    env = os.environ.get("EKTRO_LOG_DIR")
    if env:
        return Path(env)
    appdata = os.environ.get("LOCALAPPDATA")
    if appdata:
        return Path(appdata) / "Ektro" / "logs"
    import tempfile
    return Path(tempfile.gettempdir()) / "ektro-logs"


def _resolve_level() -> int:
    name = os.environ.get("EKTRO_LOG_LEVEL", "INFO").upper()
    return getattr(logging, name, logging.INFO)


def _ensure_initialized() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    log_dir = _resolve_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ektro.log"

    root = logging.getLogger("ektro")
    root.setLevel(_resolve_level())
    root.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件: 按时间滚动，14 天保留
    fh = logging.handlers.TimedRotatingFileHandler(
        log_file, when="midnight", interval=1, backupCount=14, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 控制台：仅 WARNING+ 走 stderr（不污染 IME 主输出）
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    _INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """
    获取一个 logger，自动初始化日志系统。

    Args:
        name: 通常传 __name__；会被前缀化为 "ektro.<module>"

    Returns:
        logging.Logger 实例
    """
    _ensure_initialized()
    if not name.startswith("ektro"):
        name = f"ektro.{name}"
    return logging.getLogger(name)


def current_log_path() -> Path:
    """返回当前日志文件路径（用户面板用 "查看日志" 时跳转用）。"""
    _ensure_initialized()
    return _resolve_log_dir() / "ektro.log"
