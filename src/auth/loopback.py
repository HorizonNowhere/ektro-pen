"""
OAuth Loopback callback receiver — RFC 8252 §7.3.

启 127.0.0.1:0 (OS 分配端口) 临时 HTTP server,接收单次 /callback,
校验 state (CSRF),返回 code 给调用线程,然后关停。

设计:
- 60s 超时 (与服务端 ime_auth_codes 60s expiry 对齐)
- 单次接收 — 收到任何 /callback 就关停
- 浏览器看到的回包：纯 HTML "可以关闭本页" + 自动 close (window.close 在大多数浏览器需用户首次操作触发,放在 HTML 上算尽力)
- 不与主线程共享状态除了 _CallbackHolder
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_TIMEOUT = 60.0  # 秒,与服务端 code 过期对齐
DEFAULT_HOST = "127.0.0.1"


SUCCESS_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>EKTRO 链接成功</title>
<style>body{font-family:-apple-system,sans-serif;background:#050510;color:#fff;text-align:center;padding:80px 20px}
.box{max-width:480px;margin:0 auto;background:rgba(124,58,237,.08);border:1px solid rgba(124,58,237,.3);border-radius:12px;padding:32px}
h1{font-size:20px;margin:0 0 12px}p{color:#9ca3af;line-height:1.6;margin:8px 0}
.dot{display:inline-block;width:10px;height:10px;background:#10b981;border-radius:50%;margin-right:8px}</style></head>
<body><div class="box"><h1><span class="dot"></span>链接成功</h1>
<p>EKTRO 输入法已链接到你的 ektro 账号。</p>
<p>可以关闭本页,回到 ektro-pen 继续使用。</p></div>
<script>setTimeout(()=>{try{window.close()}catch(e){}},800)</script></body></html>"""

ERROR_HTML_TPL = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>EKTRO 链接失败</title>
<style>body{{font-family:-apple-system,sans-serif;background:#050510;color:#fff;text-align:center;padding:80px 20px}}
.box{{max-width:480px;margin:0 auto;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);border-radius:12px;padding:32px}}
h1{{font-size:20px;margin:0 0 12px;color:#fca5a5}}p{{color:#9ca3af;line-height:1.6;margin:8px 0;word-break:break-word}}</style></head>
<body><div class="box"><h1>链接失败</h1><p>{reason}</p>
<p>关闭本页,回到 ektro-pen 重新发起链接。</p></div></body></html>"""


@dataclass
class CallbackResult:
    """Loopback 收到的回调结果。"""
    code: str | None
    state: str | None
    error: str | None  # 'state_mismatch' / 'access_denied' / 'timeout' / 'no_code'
    error_description: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.code is not None


class _Holder:
    """跨线程共享的 callback 结果容器。"""

    def __init__(self):
        self.result: CallbackResult | None = None
        self.event = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    expected_state: str = ""
    holder: _Holder | None = None

    def log_message(self, fmt, *args):
        return  # 静默,不污染 stdout

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/callback") and parsed.path != "/":
            self.send_response(404)
            self.end_headers()
            return

        params = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
        state = params.get("state")
        code = params.get("code")
        error = params.get("error")
        error_desc = params.get("error_description")

        # ── state 校验 (CSRF) ──
        if state != self.expected_state:
            self._respond_error("state mismatch — 可能遭遇 CSRF")
            self._publish(CallbackResult(None, state, "state_mismatch", "state mismatch"))
            return

        # ── 用户拒绝 ──
        if error:
            self._respond_error(f"用户拒绝或服务端错误: {error}")
            self._publish(CallbackResult(None, state, error, error_desc or error))
            return

        # ── 缺 code ──
        if not code:
            self._respond_error("回调缺少 code 参数")
            self._publish(CallbackResult(None, state, "no_code", "code missing"))
            return

        # ── 成功 ──
        self._respond_html(200, SUCCESS_HTML)
        self._publish(CallbackResult(code, state, None))

    def _respond_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 安全:loopback 不应允许 iframe 等
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _respond_error(self, reason: str) -> None:
        # 防 HTML 注入:仅替换占位符,reason 不含 markup
        safe = reason.replace("<", "&lt;").replace(">", "&gt;")
        self._respond_html(400, ERROR_HTML_TPL.format(reason=safe))

    def _publish(self, result: CallbackResult) -> None:
        if self.holder and self.holder.result is None:
            self.holder.result = result
            self.holder.event.set()


def _allocate_port(host: str = DEFAULT_HOST) -> int:
    """bind :0 让 OS 分配可用端口,立刻关闭释放给主 server 用。

    注:这有 TOCTOU race 风险 (端口被别人抢),但实际几乎不发生。
    """
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def wait_for_callback(
    expected_state: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    port: int | None = None,
) -> tuple[str, CallbackResult]:
    """启 loopback server,阻塞等回调。

    Args:
        expected_state: CSRF state token,必须匹配回调里的
        timeout: 最大等待秒数,默认 60s
        port: 显式端口;None=OS 分配

    Returns:
        (redirect_uri, result):
            redirect_uri 是要传给 /me/ime-link 的完整 loopback URL
            result.ok=True 时 result.code 可用

    Side effects:
        函数返回后 server 已关停,端口已释放
    """
    if port is None:
        port = _allocate_port()
    redirect_uri = f"http://{DEFAULT_HOST}:{port}/callback"

    holder = _Holder()

    # 子类 handler 注入 state + holder (HTTPServer API 不支持构造参数)
    handler_cls = type(
        "_BoundCallbackHandler",
        (_CallbackHandler,),
        {"expected_state": expected_state, "holder": holder},
    )

    server = HTTPServer((DEFAULT_HOST, port), handler_cls)
    server.timeout = 1  # poll 间隔
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        if holder.event.wait(timeout=timeout):
            return redirect_uri, holder.result  # type: ignore[return-value]
        return redirect_uri, CallbackResult(None, None, "timeout", f"no callback in {timeout}s")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
