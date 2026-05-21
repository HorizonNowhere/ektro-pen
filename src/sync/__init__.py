"""
ime-twin-link sync 模块 — 把本地 commit_log 通过旁路异步同步到 ektroai.com。

设计：
- hasher: content_hash 计算（spec 公式，与服务端一致）
- uploader: 增量上传 worker（待 Phase 4）
- backfill: 三模式首次回填（待 Phase 4）
- heartbeat: 小时级心跳 + 拉删除通告（待 Phase 4）

详见 docs/ime-ingest-contract.md
"""
