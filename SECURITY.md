# Security Policy / 安全策略

## Security model / 安全模型

ektro-pen is a **local-first** input-method memory SDK. Its security posture:

ektro-pen 是一个**本地优先**的输入法记忆 SDK,安全设计如下:

- **Your typing memory stays on your machine.** All `commit_log` data is written to a
  local SQLite database. Nothing is uploaded unless you explicitly link an account and
  enable sync.
  **打字记忆默认只在本机** —— 所有 `commit_log` 写入本地 SQLite,不链接账号、不开同步就不上传。

- **Credentials never touch the repo or plaintext files.** OAuth `access_token` /
  `refresh_token` are stored in the OS keyring (macOS `security`, Linux `secret-tool`),
  **never** in SQLite or config files.
  **凭证绝不进仓库、不进明文文件** —— OAuth token 存 OS keyring,从不落 SQLite/配置。

- **No client secret.** Account linking uses the OAuth 2.0 Device Authorization Grant
  (RFC 8628) with a public `client_id`. As a native/public client, ektro-pen ships **no**
  client secret.
  **无 client secret** —— 链接走 OAuth Device Grant(RFC 8628),native app 只用公开
  `client_id`,不内置任何密钥。

- **Password fields are never recorded.** When secure input is active
  (`IsSecureEventInputEnabled()` on macOS), commits are dropped before they reach storage,
  with a second defensive check inside the C++ SDK.
  **密码框输入永不入库** —— 检测到系统安全输入态时,提交在落库前即丢弃,C++ SDK 内还有第二层防御。

- **No secrets in this repository.** No API keys, tokens, production hostnames, or
  `.env` files are committed. `.gitignore` blocks `.env*`, `secrets/`, and `*.db*`.
  **仓库内无任何密钥** —— 不含 API key / token / 生产主机名 / `.env`;`.gitignore` 已挡
  `.env*` / `secrets/` / `*.db*`。

## Reporting a vulnerability / 报告漏洞

Please **do not** open a public issue for security problems.

请**不要**用公开 issue 报告安全问题。

Use GitHub's private vulnerability reporting:
**Security** tab → **Report a vulnerability**. We aim to acknowledge within a few days.

走 GitHub 私密漏洞报告:仓库 **Security** 标签页 → **Report a vulnerability**,我们会尽快确认。

If you find a leaked credential or personal data in the git history, please report it the
same way so we can rotate/remediate.

若你在 git 历史里发现泄露的凭证或个人数据,也请同样上报,我们会轮换/补救。
