# Fake Business Platform

独立 HTTP 模拟器，用于平台会话与 MCP 的联调/冒烟：不依赖 session-manager 或 Runtime，
只用标准库，shell、pytest、真实 Skill 脚本都能直接驱动。

- `smoke_platform`（默认）：Cookie 登录（`POST /login`、`GET /api/me`、`GET /health/session`），对应旧版 Cookie 会话。
- `mssw`：贴近 MSS ADS/gateway 契约，驱动真实的 `MSSAdapter`
  （`packages/platform-sdk/src/muad_platform_sdk/adapter/mss.py`）：SigV4 风格 AK/SK 登录、CSRF cookie/header、健康检查、Traceid 回显。

两种模式都在**同一个进程/端口**上提供 MSSP/SOAR 端点，驱动 `daily-duty-report-mssp`
同形态的 Skill：Host 路由、固定 `X-CSRFToken`、`X-MSSW-Company-Id`，以及数据湖/事件/
资产/XDR 端点。这样 Skill 直接复用 Console 里已有的 Smoke 测试平台 base URL，不需要
再建一个平台配置。

`POST /mcp` 与 `--platform` 无关，额外提供 Streamable HTTP MCP 子集。

## 一键启停

`services.sh` 管理多份实例（PID/日志/就绪信息在 `.data/fake-business-platform/`，已被 `.gitignore` 覆盖）：

| 服务 | 默认端口 | 用途 |
|---|---|---|
| `platform` | 18080 | 共享业务平台：Smoke 测试平台 / MSSW / MSSP 端点同一进程 |
| `mcp` | 18081 | MCP Streamable HTTP，`Authorization: Bearer` |
| `mcp-headers` | 18082 | MCP Streamable HTTP，自定义请求头（含一个 secret 头） |

```bash
./tools/fake-business-platform/services.sh start          # 全部启动
./tools/fake-business-platform/services.sh start mcp      # 只启动指定的
./tools/fake-business-platform/services.sh start --force  # 连非本工具的端口占用者一起杀（危险）
./tools/fake-business-platform/services.sh status
./tools/fake-business-platform/services.sh logs mcp       # 跟踪日志
./tools/fake-business-platform/services.sh stop           # 全部停止
```

重复 `start` 幂等；`stop` 只杀本次拉起的 `server.py`（pid 复用不会误杀）。端口被占时 `start`
会先收走**本工具遗留的 `server.py`**（旧实例/手工起的实例，pid 文件里记的端口变了也会自动重启）；
被别人占用则报错并打印占用者、不动它——确需强杀加 `--force`。可覆盖的环境变量：
`PLATFORM_PORT` / `PLATFORM_MODE` / `PLATFORM_WECOM_WRITE` / `MCP_PORT` /
`MCP_HEADERS_PORT` / `FAKE_AK` / `FAKE_SK` / `MCP_TOKEN` / `MCP_APP_ID` /
`MCP_PLATFORM_UID` / `MCP_API_KEY` / `HOST` / `PYTHON`。

## 手动启动

```bash
# Smoke 测试平台 / mssw 共用同一端口；MSSP 端点默认也在里面
python3 tools/fake-business-platform/server.py \
  --platform smoke_platform --host 0.0.0.0 --port 18080

# 或者使用本仓库的默认 platform 服务（mssw 模式）
./tools/fake-business-platform/services.sh start platform
```

stdout 首行是 JSON 就绪信息（`baseUrl`、`mssp.hostHeader`/`mssp.csrfToken`、
`mssp.companies`、`mssp.endpoints` 等）。

Runtime 在容器/Pod 内时，`baseUrl` 必须在该网络命名空间可达：用 `host.docker.internal`
（Docker/OrbStack）或节点 IP / 集群别名。该值属于部署环境，配在 Console 平台设置里，不写进 Skill。

## MCP 模式（Streamable HTTP）

`POST /mcp` 提供 MCP Streamable HTTP 的 JSON-RPC 子集，供 Console 的发现/测试与 Runtime 的
`tools/call` 打真实 HTTP 端点；与 `--platform` 无关，仅用标准库。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/mcp` | `initialize`、`notifications/initialized`、`tools/list`、`tools/call` |
| GET | `/mcp` | `405` + `Allow: POST`（本子集不提供 SSE 流） |

会话：`initialize` 下发随机 `Mcp-Session-Id`，后续请求必须带回，否则 `401`。

### 认证

| `--mcp-auth` | 必需请求头 | 启动校验 |
|---|---|---|
| `none`（默认） | 无 | — |
| `bearer` | `Authorization: Bearer <--mcp-token>` | `--mcp-token` 不能为空 |
| `headers` | 全部 `--mcp-header NAME=VALUE`（可重复） | 至少一个 `--mcp-header` |

```bash
# Bearer
python3 tools/fake-business-platform/server.py \
  --host 127.0.0.1 --port 18081 \
  --mcp-auth bearer --mcp-token test-mcp-token

# 自定义请求头（一个 secret 头 + 静态头）
python3 tools/fake-business-platform/server.py \
  --host 127.0.0.1 --port 18081 \
  --mcp-auth headers \
  --mcp-header X-App-Id=sdspmcp \
  --mcp-header X-Platform-Uid=53842 \
  --mcp-header X-Api-Key=test-key
```

`--mcp-path` 改端点路径（默认 `/mcp`）。就绪 JSON 里额外带 `mcpUrl`、`mcpAuth`、`mcpTools`，
**不回显** token 与 header 值；请求日志脱敏 `Authorization`、`Cookie` 与所有 `--mcp-header` 值。

### 工具

| 工具 | 效果（`annotations`） | 结果 |
|---|---|---|
| `platform.ping` | READ（`readOnlyHint`） | `pong` |
| `platform.targets.list` | READ（`readOnlyHint`） | 三个假目标 |
| `platform.echo` | WRITE（无 annotations） | 回显 `message` |
| `platform.targets.delete` | DESTRUCTIVE（`destructiveHint`） | `{"deleted": "<id>"}` |
| `platform.fail` | WRITE（无 annotations） | `isError: true` |

### Console 配置

Bearer（无 `auth_config`，密钥存 MCP Server secret）：

```json
{
  "name": "Fake MCP (Bearer)",
  "key": "fake-mcp-bearer",
  "endpoint": "http://host.docker.internal:18081/mcp",
  "auth_secret": "test-mcp-token",
  "auth_config": {}
}
```

自定义头（静态头在 `auth_config`，密钥值在 `auth_secret`，头名在 `secret_header`）：

```json
{
  "name": "Fake MCP (headers)",
  "key": "fake-mcp-headers",
  "endpoint": "http://host.docker.internal:18081/mcp",
  "auth_secret": "test-key",
  "auth_config": {
    "headers": { "X-App-Id": "sdspmcp", "X-Platform-Uid": "53842" },
    "secret_header": "X-Api-Key"
  }
}
```

`auth_secret` 是平台侧的 MCP 密钥：不得写入 Skill、README 副本、日志或快照。

## MSSP 端点（合并到共享 platform）

共享 `platform` 服务同时模拟 `daily-duty-report-mssp` 使用的 MSSP/SOAR 网关。数据来自
`fixtures/mssp.json`，不访问内网。Console 里的 Smoke 测试平台继续用原来的
`http://127.0.0.1:18080`，不需要新增平台。

```bash
./tools/fake-business-platform/services.sh start platform

# 需要测试 Skill 自己经 MSSP 上传/发送时显式开启（默认关闭）
PLATFORM_WECOM_WRITE=1 ./tools/fake-business-platform/services.sh restart platform
```

其它参数：`--mssp-fixtures`（fixture JSON）、`--mssp-default-company`、`--mssp-host-header`、
`--mssp-csrf-token`。

### 请求头

| 头 | 要求 | 说明 |
|---|---|---|
| `Host` | 必填 | 默认 `soar-inner.sangfor.com.cn`，可用 `--mssp-host-header` 改；不匹配返回 HTTP 404 / `code=4004` |
| `X-CSRFToken` | POST 必填 | 固定值 `6c5c52cd-3ebb-47e2-946c-15c2569836e7`（可用 `--mssp-csrf-token` 改）；缺失/错误返回 HTTP 403 / `code=9451` |
| `X-MSSW-Company-Id` | api-proxy 必填 | `/gateway/api-proxy/*` 缺失返回 HTTP 400 / `code=1003` |
| `Cookie` | 不需要 | fake 只模拟只读业务面，不实现 MSSP 登录态 |

业务码与 HTTP 状态分离：参数类错误用 HTTP 200 + `code=9064/9001/1001`；
网关/路由/鉴权失败才用 4xx。apex/linkage 成功码是字符串 `"Success"`，XDR 响应没有
顶层 `code`，与真实平台一致。

### 只读端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/order/v1/user/company_simple_info` | 客户列表（`keyword` 过滤） |
| POST | `/gateway/customer-mgr-service/order/v1/project/service_info/company_id` | 服务项；全资产判定 |
| POST | `/gateway/log-search-center-service/datalake/v1/ckTableQuery` | 表清单（6408/6409/6410） |
| POST | `/gateway/log-search-center-service/datalake/v1/ckCount` | 日志/告警计数 |
| POST | `/gateway/log-search-center-service/datalake/v1/ckQueryList` | 明细分页（缺 `pageNo/pageSize/fieldList` → 9001） |
| POST | `/gateway/log-search-center-service/datalake/v1/ckQuery` | 固定 9002（真实平台权限不足） |
| POST | `/gateway/event-mgr/external/event_table` | 事件分页/状态/场景/服务类型过滤 |
| POST | `/gateway/event-mgr/external/event_table/remark` | 事件备注（含「技术误报」样例） |
| POST | `/gateway/event-mgr/external/event_table/event` | 事件详情 |
| POST | `/gateway/event-mgr/external/event_grading_type` | 风险等级 facet（带分页参数 → 9064） |
| POST | `/gateway/event-mgr/external/manage_type` | 一级场景 facet（带 `manage_type` → 9064） |
| POST | `/gateway/asset-mgr-service/order/v1/asset` | 资产台账（`offset`/`limit`、`keyword`/`ip_url_keyword`、`first_time` 毫秒窗口；`9001` 支持批量资产） |
| POST | `/gateway/customer-mgr-service/order/v1/branch/dev` | 组件清单（`type`/`status`） |
| POST | `/order/v1/whitelist/alert_filter_list` | 白名单（`valid_time` 毫秒窗口） |
| GET | `/gateway/api-proxy/api/apex/linkage/v1/autodispose/config/getresponsemode` | security / mock / disabled 三态 |
| POST | `/gateway/api-proxy/api/apex/linkage/v1/threatentity/statistics` | 威胁实体四项统计 |
| POST | `/gateway/api-proxy/api/apex/linkage/v1/threatobjects` | 威胁实体明细（`blockStatus`/`gptBlockResult`，pageSize>100 → `InternalError.InvalidParameter`） |
| POST | `/gateway/api-proxy/ngsoc/INCIDENT/api/v1/table/query/alertTableQueryHandler` | XDR 告警明细 |
| POST | `/gateway/api-proxy/ngsoc/INCIDENT/api/v1/table/count/alertTableQueryHandler` | XDR 告警计数 |

### fixtures

| 客户 ID | 用途 |
|---|---|
| `1001` | 全资产；日志 2000；告警 24（上期 12）；有效事件 5；security 威胁实体 |
| `1002` | 非全资产；日志 400；告警 8（持平）；有效事件 5；mock 威胁实体 |
| `1003` | 空数据：计数/事件/实体全 0，报告仍可生成 |
| `1004` | 智能对抗未开通（disabled） |
| `9001` | 批量资产客户：`asset_bulk` 生成 1200 条确定性资产，用于截断、分页和完整 CSV 导出 |
| `2101`..`2111` | 场景 1-11：新增资产/组件、原有/新增攻击源、平稳、类型变化、组件离线、新增白名单、下降、上期为零、原因不足 |

fixtures 的 `report_date` 固定为 `2026-11-18`（上期 `2026-11-17`）。场景 1/2 的
资产 `first_time`、组件 `create_time` 也落在该日，便于确定性断言。

批量资产用 `asset_bulk` 声明，避免在 JSON 里写上千行：

```json
"asset_bulk": {"count": 1200, "prefix": "10.60"}
```

服务启动/查询时按 `prefix.<third>.<fourth>` 确定性展开；`9001` 使用该配置。

### 控制面（测试用）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/__stats` | 按路径请求计数、上传/发送/重复发送记录 |
| GET | `/__fixtures` | fixture 客户清单 |
| POST | `/__control/faults` | 注入 `{path, http_status, business_code, msg, delay_ms, times}`；`times=-1` 常驻 |
| POST | `/__control/wecom` | `{"enabled": true/false}` 运行时开关企微写操作 |
| POST | `/__control/reset` | 清空计数、fault、上传/发送记录 |

写操作默认关闭：企微 query/upload/send 都返回 HTTP 403 / `code=40300`。开启后：

- 上传按内容 sha256 去重，同一文件重复上传返回同一个 `_id`；
- 发送按 `Traceid`（优先）或 payload 指纹幂等，同一键只记录一次发送；
- 发送响应里的 `code=0` 只代表平台受理，不代表已送达。

## mssw 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/certification/get_token` | 下发 `csrf_token` cookie |
| POST | `/gateway/mss-auth-acl-service/v1/certification/login_agent` | 校验 SigV4 `Authorization`、`x-csrftoken`；下发 `fake_session` + `csrf_token` cookie |
| GET | `/v1/rtt` | 健康检查，需要会话 cookie |
| GET | `/api/v1/ping` | 需要会话 + CSRF 头，回显 `Traceid` |
| GET | `/api/v1/targets` | 同上，返回三个假目标 |
| GET | `/__stats` | 按路径的请求计数（断言用） |
| GET | `/__stats/reset` | 清零计数 |

`POST /login`（默认模式）恒为 HTTP 200，结果在 JSON `code` 里（业务码分类用）：
`0` ok、`1001` 参数、`1002` 认证、`1003` 锁定、`1004` 限流、`1005` 服务错误。

## `smoke_platform` 的 Console 平台设置

平台 key 为 `smoke_platform`（是平台 key，不是 Skill 名）：

```json
{
  "resolver_type": "BASE_URL",
  "resolver_config": { "base_url": "http://host.docker.internal:18080" },
  "adapter_key": "mss-session",
  "adapter_schema_version": "2",
  "credential_mode": "SHARED_ONLY",
  "adapter_config": {
    "auth_mode": "hmac",
    "csrf_enabled": true,
    "csrf_endpoint": "/v1/certification/get_token",
    "session_endpoint": "/gateway/mss-auth-acl-service/v1/certification/login_agent",
    "health_endpoint": "/v1/rtt",
    "login_cookie_name": "fake_session",
    "csrf_header": "X-CSRFToken",
    "branch_tag": "MSSW-ADAPTER",
    "timezone": "+08:00",
    "timeout_seconds": 10,
    "session_ttl_seconds": 900
  },
  "credential": { "ak": "test-ak", "sk": "test-sk" }
}
```

`credential` 存为平台共享 Secret（`SHARED_ONLY`），不得写入 Skill 包、README、日志或快照。

## 本地冒烟（无需 Console / Runtime）

```bash
uv run python tools/fake-business-platform/smoke_check.py

# ASYNC BATCH 使用 services.sh 管理的 platform 服务（不会另起 server）
./tools/fake-business-platform/services.sh async-batch
# 或先启动、再单独跑检查
./tools/fake-business-platform/services.sh start
uv run python tools/fake-business-platform/async_batch_check.py
```

`smoke_check.py` 自行拉起 server，跑真实 `MSSAdapter.authenticate` 登录、写执行态会话文件、以该状态执行
`skills/smoke-platform-sample/scripts/run.py`，并断言健康检查 + CSRF 保护的业务调用 + Traceid 回显。

`async_batch_check.py` 读取 `platform.ready.json`，验证 `skills/fake-platform-async-batch`：父执行从
`/api/v1/targets` 生成 BATCH 计划，三个子执行各写 JSON/CSV 到
`.data/fake-business-platform/async-batch-demo/<target>/`；真实 Worker fan-out 见
`tests/acceptance/task_schedule/test_fake_platform_async_batch.py`。退出码 `0` 表示链路可用。

## 文件

- `server.py` — 模拟器（仅标准库）。
- `mssp.py` — MSSP 端点的路由/鉴权/fixtures/故障注入实现（由 `platform` 服务挂载）。
- `real_skill_smoke.py` — 真实内网 `daily-duty-report-mssp` 对共享 Fake 平台的手动验证入口。
- `fixtures/mssp.json` — MSSP 确定性 fixtures（全资产/非全资产/空数据/场景 1-11）。
- `smoke_check.py` — 上节的本地端到端冒烟。
- `async_batch_check.py` — ASYNC BATCH Skill 的本地端到端（父计划 + 子文件）。
- `services.sh` — 本地启停脚本（platform / mcp / mcp-headers，以及 `async-batch`、`real-skill` 命令）。

MSSP 模式的回归用例：

```bash
uv run python -m pytest -q tests/tools/test_fake_mssp_platform.py tests/tools/test_fake_mssp_scenarios.py
uv run python -m pytest -q tests/agent_worker/test_local_report_skill.py
```

本地回归 Skill 在 `skills/daily-duty-report-mssp-local/`（SKILL.md 内有
argv/stdin 双入口、fixtures 说明，以及手动导入平台后验证 WebChat / WECOM 投递的步骤）。
`./tools/fake-business-platform/services.sh start` 启动共享 `platform`（含 MSSP 端点）；
`./tools/fake-business-platform/services.sh real-skill 1001` 只启动 `platform` 并跑真实包。

## 用真实内网 Skill 手动验证共享 Fake 平台

`real_skill_smoke.py` 直接运行用户真实包 `/Users/jahan/share/daily-duty-report-mssp`，
只把 base URL 指向共享 Fake 平台，并用临时 `session-manager` 提供假登录态；业务脚本、
场景判定和 xlsx 生成都不复制、不改写：

```bash
# 一条命令：启动 Fake 平台 + 跑真实 Skill + 把报告留在 .data/real-mssp-skill/
uv run python tools/fake-business-platform/real_skill_smoke.py --company-id 1001 --date 2026-11-18

# 场景矩阵
uv run python tools/fake-business-platform/real_skill_smoke.py --company-id 2101

# 跑已启动的共享 platform（不再自动起进程）
uv run python tools/fake-business-platform/real_skill_smoke.py \
  --base-url http://127.0.0.1:18080 --company-id 1002

# 保留临时 Skill 副本，便于对照
uv run python tools/fake-business-platform/real_skill_smoke.py --company-id 2107 --keep-skill
```

输出目录：`.data/real-mssp-skill/<company>/<date>/`，含正文、stderr、xlsx 附件。默认
`--no-wecom-push`，不会触碰企微发送；`--with-wecom-query` 只做只读查群。

> 这条路径用于验证“真实内网包 + fake 平台”的协议/场景兼容；平台调度的 SKILL_PLAN
> argv/stdin 回归仍用 `skills/daily-duty-report-mssp-local/`，两者不要混为
> 一谈。真实包需要 `session-manager`，平台手工导入真实包时仍必须配置对应的平台会话；
> 本地快速验证用上面的 smoke 命令即可。
