"""
HTTP 客户端 — urllib stdlib 封装,不引 requests。

设计:
- JSON 请求/响应统一处理
- Bearer / x-api-key 等 header 注入
- 超时 (默认 30s)
- 错误码 → ApiError 异常 (status / code / message / retry_after)
- 不重试 (策略由调用方实现 — sync_worker 自己控指数退避)

不引外部依赖：
- urllib.request (stdlib)
- json (stdlib)
- ssl 默认走系统证书

详见 docs/ime-ingest-contract.md §9 错误码总表
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_TIMEOUT = 30.0  # 秒


@dataclass(frozen=True)
class ApiError(Exception):
    """统一 API 错误。

    Attributes:
        status: HTTP 状态码 (0 = 网络层错误,如 timeout/dns)
        code: 服务端 error.code (如 'invalid_token' / 'rate_limit') 或 'network' / 'parse'
        message: 错误描述
        retry_after: 服务端 Retry-After 秒数（限流时存在）
    """
    status: int
    code: str
    message: str
    retry_after: int | None = None

    def __str__(self) -> str:
        ra = f" (retry_after={self.retry_after}s)" if self.retry_after else ""
        return f"[{self.status} {self.code}] {self.message}{ra}"


@dataclass(frozen=True)
class ApiResponse:
    """成功响应。data 已解析 JSON,headers 字典化。"""
    status: int
    data: Any
    headers: dict[str, str]

    @property
    def payload(self) -> Any:
        """返回 ok({...}) 包装下的 .data 字段(若有);否则原 body。

        服务端 src/shared/utils/api-response.ts ok(x) 返回 {ok:true, data: x}.
        """
        if isinstance(self.data, dict) and self.data.get("ok") and "data" in self.data:
            return self.data["data"]
        return self.data


def _build_request(
    method: str,
    url: str,
    *,
    body: Any | None,
    bearer_token: str | None,
    extra_headers: dict[str, str] | None,
) -> urllib.request.Request:
    headers: dict[str, str] = {
        "User-Agent": "ektro-pen/0.4 (sync-client)",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    data_bytes: bytes | None = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data_bytes = bytes(body)
        else:
            data_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

    return urllib.request.Request(url=url, data=data_bytes, method=method.upper(), headers=headers)


def _parse_error_body(body_bytes: bytes) -> tuple[str, str, int | None]:
    """解析错误 body,返回 (code, message, retry_after)。

    服务端 err({code, message, status, ...}) 返回 {ok:false, error:{code, message, ...}}.
    兼容 plain text / 非 JSON。
    """
    try:
        body = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ("parse", body_bytes.decode("utf-8", errors="replace")[:200], None)

    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            code = str(err.get("code", "unknown"))
            msg = str(err.get("message", "no message"))
            ra_raw = err.get("retry_after")
            ra = int(ra_raw) if isinstance(ra_raw, (int, float, str)) and str(ra_raw).isdigit() else None
            return code, msg, ra
    return ("unknown", str(body)[:200], None)


def request(
    method: str,
    url: str,
    *,
    body: Any | None = None,
    bearer_token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ApiResponse:
    """发起 HTTP 请求,JSON 序列化/反序列化。

    Raises:
        ApiError: 任何非 2xx 状态码 / 网络错误 / 解析错误
    """
    req = _build_request(method, url, body=body, bearer_token=bearer_token, extra_headers=headers)

    # 系统证书 — 不绕过 TLS 校验
    ctx = ssl.create_default_context()

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            resp_headers = dict(resp.headers.items())
            if not raw:
                parsed: Any = None
            else:
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    raise ApiError(
                        status=resp.status,
                        code="parse",
                        message=f"Non-JSON response: {e}",
                    ) from e
            return ApiResponse(status=resp.status, data=parsed, headers=resp_headers)

    except urllib.error.HTTPError as e:
        body_bytes = e.read() if e.fp else b""
        code, msg, retry_after = _parse_error_body(body_bytes)
        # 优先用响应 header 的 Retry-After
        ra_hdr = e.headers.get("Retry-After") if e.headers else None
        if ra_hdr and ra_hdr.isdigit():
            retry_after = int(ra_hdr)
        raise ApiError(status=e.code, code=code, message=msg, retry_after=retry_after) from e

    except urllib.error.URLError as e:
        raise ApiError(status=0, code="network", message=str(e.reason)) from e

    except TimeoutError as e:
        raise ApiError(status=0, code="timeout", message=str(e) or "request timed out") from e


def get(url: str, **kwargs: Any) -> ApiResponse:
    return request("GET", url, **kwargs)


def post(url: str, body: Any = None, **kwargs: Any) -> ApiResponse:
    return request("POST", url, body=body, **kwargs)


def delete(url: str, **kwargs: Any) -> ApiResponse:
    return request("DELETE", url, **kwargs)
