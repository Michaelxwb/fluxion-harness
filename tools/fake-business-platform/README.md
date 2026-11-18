# Fake Business Platform

独立 HTTP 模拟器，用于平台会话与 MCP 的联调/冒烟：不依赖 session-manager 或 Runtime，
只用标准库，shell、pytest、真实 Skill 脚本都能直接驱动。

- `smoke_platform`（默认）：Cookie 登录（`POST /login`、`GET /api/me`、`GET /health/session`），对应旧版 Cookie 会话。
- `mssw`：贴近 MSS ADS/gateway 契约，驱动真实的 `MSSAdapter`
  （`packages/platform-sdk/src/muad_platform_sdk/adapter/mss.py`）：SigV4 风格 AK/SK 登录、CSRF cookie/header、健康检查、Traceid 回显。

两种模式下 `POST /mcp` 都额外提供 Streamable HTTP MCP 子集。

## 一键启停

`services.sh` 管理多份实例（PID/日志/就绪信息在 `.data/fake-business-platform/`，已被 `.gitignore` 覆盖）：

| 服务 | 默认端口 | 用途 |
|---|---|---|
| `platform` | 18080 | mssw 模式业务平台（Skill / MSSM 适配器走这条） |
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
`PLATFORM_PORT` / `MCP_PORT` / `MCP_HEADERS_PORT` / `PLATFORM_MODE` / `FAKE_AK` / `FAKE_SK` /
`MCP_TOKEN` / `MCP_APP_ID` / `MCP_PLATFORM_UID` / `MCP_API_KEY` / `HOST` / `PYTHON`。

## 手动启动

```bash
# mssw 模式（smoke_platform 样例 Skill 用这条）
python3 tools/fake-business-platform/server.py \
  --platform mssw --host 0.0.0.0 --port 18080 \
  --ak test-ak --sk test-sk
```

stdout 首行是 JSON 就绪信息（`baseUrl`、AK/SK、`csrfEnabled`、`sessionEndpoint`、`healthEndpoint`）。

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
uv run python tools/fake-business-platform/async_batch_check.py
```

`smoke_check.py` 自行拉起 server，跑真实 `MSSAdapter.authenticate` 登录、写执行态会话文件、以该状态执行
`skills/smoke-platform-sample/scripts/run.py`，并断言健康检查 + CSRF 保护的业务调用 + Traceid 回显。

`async_batch_check.py` 验证 `skills/fake-platform-async-batch`：父执行从 `/api/v1/targets`
生成 BATCH 计划，三个子执行各写 JSON/CSV 到
`.data/fake-business-platform/async-batch-demo/<target>/`；真实 Worker fan-out 见
`tests/acceptance/task_schedule/test_fake_platform_async_batch.py`。退出码 `0` 表示链路可用。

## 文件

- `server.py` — 模拟器（仅标准库）。
- `smoke_check.py` — 上节的本地端到端冒烟。
- `async_batch_check.py` — ASYNC BATCH Skill 的本地端到端（父计划 + 子文件）。
- `services.sh` — 本地启停脚本（platform / mcp / mcp-headers）。
