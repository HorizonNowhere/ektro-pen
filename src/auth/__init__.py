"""
ime-twin-link auth 模块 — OAuth 2.0 Loopback + PKCE 主路径 / RFC 8628 Device Grant 降级。

设计:
- pkce: code_verifier / code_challenge / state 生成（spec 严格公式）
- loopback: 本机 127.0.0.1 临时 HTTP server（待 Phase 3）
- link: 完整 OAuth handshake（待 Phase 3）
- device_grant: 降级路径（待 Phase 3）
- refresh: token 自动 refresh + 旋转（待 Phase 2）
- keyring_store: 跨平台 OS keyring（待 Phase 2）

详见 docs/ektro-link-protocol.md
"""
