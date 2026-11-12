# 出站请求统计排障 / Egress Request Monitoring

共享限流生效但概览的出站监控始终为零时，先区分 Redis 准入计量与 PostgreSQL 请求审计。监控页从 `runtime.egress_audit` 的 HTTP 终态记录聚合（不重复计算 MCP 逻辑工具审计和中间阶段）；只更新 Redis 预算不会生成监控记录。

Runtime 与 Worker 的共享 SDK 在 HTTPX 发送边界、Skill requests 的私有 IPC 边界生成请求记录。每个物理请求尝试使用独立 attempt ID，只有一个终态审计，重复写入由 `(tenant_id, attempt_id, phase)` 去重。重定向、认证和显式重试分别计数。

## 部署与本地启动

部署包含本修复的版本前，使用该环境的迁移配置执行：

```bash
uv run python migrations/db_migrate.py -c migrations/alembic.prod.ini upgrade head
```

迁移配置必须指向实际目标库。迁移 `0047` 允许没有来源 Run 的定时任务使用 `task_id` 记录审计，并扩展接口路径字段。不得为定时任务伪造 Run ID。降级遇到不能保留的任务记录或长路径时会拒绝执行。

本地 Skill 使用的 Python 解释器须安装出站启动钩子：

```bash
uv run python -m muad_egress_site --install
scripts/dev.sh start
```

协议和钩子代码需随同一版本部署；本次 IPC 完成消息增加有界 HTTP 状态和固定错误分类，协议版本由 `PROTOCOL_VERSION` 管理。升级后重启 Runtime、Worker 及其 Skill 子进程。统计只覆盖已接入的发送链路，不代表接管其他进程或任意网络请求。

## 统计口径

- 请求次数：已记录的请求尝试，包括本地限流。
- 已发送：已进入发送路径的尝试；网络连接失败也属于发送尝试。
- 已完成：响应正常完成，且 HTTP 状态小于 400。
- 失败：上游 HTTP 错误、传输失败、取消或超时；上游 429 属于失败，不计本地限流。
- 被限流：共享限额拒绝准入，业务 HTTP 不发出。

审计只保存 origin、实际接口路径、方法、状态、耗时和固定错误分类。接口按实际请求的方法与路径分组，与是否匹配限流规则无关；路径保留原值，不保存查询参数、请求头、正文或异常原文。历史缺失请求无法从当前 Redis 累计量可靠重建，不回填推测数据。

审计写入失败会记录 `egress_attempt_audit_failed`，不会重试业务请求或改变限流结果；此类故障期间监控可能缺记录，需结合该警告判断数据完整性。

## 验证

```bash
uv run pytest -q tests/sdk/test_shared_egress_audit.py \
  tests/agent_runtime/test_shared_egress_audit_store.py \
  tests/agent_runtime/test_egress_root_recovery.py \
  tests/agent_worker/test_egress_budget_lifecycle.py
```

真实浏览器验证：在已启用单接口每分钟一次规则的本地环境，让样例 Skill 连续调用两次；第一条成功，第二条返回 `IPC_LIMIT_WINDOW`。监控中该接口应为两次尝试、一次拦截，业务探针只新增收到一次请求。样例还会产生健康检查、认证等请求，因此总请求数可能大于两次。此验证不替代真实 MSSP、生产或全量验收。
