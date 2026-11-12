---
design_template: full
---

# MCP HTTP 认证与本地联调服务 后端设计

> **文档编号**: MOD-MCP-HTTP-AUTH-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-12
> **文档状态**: 设计评审中

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | MCP Server 管理、Console 发现、Runtime 执行、开发联调服务 |
| Owner | muad-console-platform（catalog/认证契约）；muad-agent-runtime（执行） |
| 数据 Owner | control |
| 前置模块 | 06-mcp-management、agent-runtime MCP adapter |
| 建议代码位置 | `apps/console-platform/backend/.../application/mcp_service.py`、`infrastructure/mcp_client.py`、`apps/agent-runtime/.../application/mcp_runtime_adapter.py`、`tools/fake-business-platform/server.py` |

### 1.1 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v1.0 | 2026-11-12 | fluxion-harness | 初始设计：自定义请求头、结构化 auth_config、本地假 MCP |

## 2. 需求分析

### 2.1 需求概述

MCP V1 仍只支持 Streamable HTTP。本次把认证从固定 `Authorization: Bearer` 扩展为服务级三选一：无认证、Bearer、自定义请求头（一个密钥头 + 多个非密静态头）。Console 的发现/测试与 Runtime 的 `tools/call` 必须使用同一套头组装规则；密钥仍只存 `mcp_server.auth_secret`，不进入 `auth_config`、审计、日志或 Snapshot。

同时为本地联调提供一份真实的 Streamable HTTP 假 MCP（`tools/fake-business-platform`），覆盖两种认证模式与 READ/WRITE/DESTRUCTIVE/`isError` 工具。

### 2.2 功能方案

| 功能ID | 功能名称 | 来源 |
|---|---|---|
| FEAT-01 | 结构化认证配置与校验 | PRD |
| FEAT-02 | Console 发现/测试发送自定义头 | PRD |
| FEAT-03 | Runtime 执行发送自定义头 | PRD |
| FEAT-05 | 本地假 MCP | PRD |
| FEAT-07 | 规范与文档 | PRD |

### 2.3 范围与边界

- In：HTTP Streamable、服务级认证、单密钥、非密静态头、发现/测试/执行、假 MCP。
- Out：stdio、OAuth、mTLS、多密钥、Tool 级授权、MCP 代理、出站 URL 白名单。

### 2.4 验收条件

引用 `mcp-http-auth.scenarios.md` 的 S-/E-/B- 场景；自动化断言见第 3.5 节与任务文档。

## 3. 技术设计

### 3.1 方案选型

| 方案 | 结论 |
|---|---|
| 继续让用户填原始 JSON | 否：无法校验、易把密钥写进审计，前端复杂度高 |
| 多个独立密钥字段 | 否：本期只需要一个；扩展成本高 |
| `auth_config={headers, secret_header}` + 单个 `auth_secret` | **采用**：语义清晰、可校验、与现有 secret 模型一致 |
| 新增 MCP 网关/代理 | 否：HTTP 直连已满足；不新增部署单元 |

### 3.2 架构设计

```text
Console discover/test ──> McpClient(endpoint, auth_secret, headers, secret_header)
Runtime tools/call   ──> ResolvedMcpServer(headers, secret_header) + API-09 auth_secret
                          └─> McpServerDefinition ─> _McpSession(headers)
```

- `auth_config` 是非密配置，随 MCP Server 行持久化；`auth_secret` 仍是唯一密钥列。
- 解析定义（`/internal/runtime/resolve-definition`）把 `headers`/`secret_header` 放进 `ResolvedMcpServer`；Snapshot 不写这些字段（与 endpoint 同等处理）。
- API-09 继续按 `mcp_server_id` 返回 `auth_secret`；密钥轮换对下一次调用即时生效。
- 发现失败保留上一成功 Catalog，沿用现有 `DISCOVERY_FAILED` 路径。

### 3.3 数据设计

`control.mcp_server.auth_config_json` 结构：

```json
{
  "headers": {"X-App-Id": "sdspmcp", "X-Platform-Uid": "53842"},
  "secret_header": "X-Api-Key"
}
```

规则：

- 缺省或 `{}` = 无自定义头、无密钥头。
- `secret_header` 为空 = `auth_secret` 走 `Authorization: Bearer <secret>`。
- `secret_header` 非空 = `auth_secret` 以原始值写入该头（不追加 `Bearer `）。
- 静态头不得包含 `secret_header`，不得使用敏感名称（`token`/`secret`/`password`/`credential`/`api-key`/`apikey`/`private-key`），防止进入 `config_audit_log`。
- 保留头禁止：`host`、`content-length`、`transfer-encoding`、`connection`、`accept`、`content-type`、`mcp-session-id`、`mcp-protocol-version`、`authorization`、`cookie`。
- 头名按 RFC 7230 token 校验；大小写不敏感去重；单值 ≤ 4 KiB，总数 ≤ 32，总大小 ≤ 16 KiB；禁止 CR/LF/NUL。
- `auth_config` 变更进入 `config_audit_log`；因此校验必须保证其中不含密钥值。

**迁移**：Alembic 数据迁移把历史 `{"header": "X-Api-Key"}` 规范为 `{"secret_header": "X-Api-Key"}`；其他无法识别的结构归 `{}`。该字段此前未被消费，迁移只做意图保留，不做兼容层。

### 3.4 接口设计

#### API-01/02 MCP CRUD

- 请求 DTO 仍接收 `auth_config: dict[str, Any] | None`，由服务层 `parse_auth_config()` 校验并转换为 `McpAuthConfig`；校验失败抛 `MCP_CONFIG_INVALID`（避免 FastAPI 422 与既有错误目录不一致）。
- `McpServerDetail.auth_config` 返回 `McpAuthConfig` 结构化对象；仍只回 `auth_secret_configured`。
- 非法配置不落库、不写审计成功事件。
- `auth_secret` 增加有界长度校验；更新时传空字符串表示清除（前端"无认证"使用）。

```python
class McpAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headers: dict[str, str] = Field(default_factory=dict)
    secret_header: str | None = None


def parse_auth_config(raw: dict[str, Any] | None) -> McpAuthConfig:
    """校验并归一化；失败抛 AppError(MCP_CONFIG_INVALID)。"""
```

#### API-03 连接测试

`McpService.test_connection` 构造 `McpClient` 时传入 `headers` 与 `secret_header`；失败分类保持 `connect | timeout | protocol`。

#### API-04 工具发现

`McpService.discover_tools` 使用同一构造；catalog hash/revision 逻辑不变。

#### 内部解析契约

`ResolvedMcpServer` 增加：

```python
headers: dict[str, str] = Field(default_factory=dict)
secret_header: str | None = None
```

`AgentMcpService.effective_mcp_servers` 从 `server.auth_config_json` 填入；Runtime `_mcp_server_definition` 透传到 `McpServerDefinition`。

### 3.5 质量实现方案

**安全**

- `McpClient._headers` 与 `_McpSession._headers` 只从已校验的 `headers` 合并；`Authorization` 仅在 Bearer 模式添加。
- 错误与日志不打印头值；假 MCP 的 `_log_headers` 对 `authorization` 与配置的密钥头脱敏。
- 测试断言：`config_audit_log`、`tool_call_audit`、`egress_audit`、`runtime_snapshot` 不含密钥值。

**可靠性**

- 发现失败仍用独立事务写 `DISCOVERY_FAILED`；不得因头配置触发新的异常类型。
- 头组装失败（配置脏数据）按 `MCP_CONFIG_INVALID` 在写入前拦截；读取侧对旧行做迁移后不存在该路径。

**可观测性**

- 现有 `egress_audit` 的 target 不含 endpoint/头值；可新增非密指标维度 `auth_mode`（none/bearer/headers），不得记录头名以外的值。

**测试建议**

| 层 | 用例 |
|---|---|
| unit | `McpAuthConfig` 校验：保留头、CRLF、重名、敏感名、密钥头冲突、长度上限 |
| unit | `McpClient` 头组装：none/bearer/headers 三态；密钥原值；无凭据不发 Authorization |
| integration | Console 创建 → 测试 → 发现（Bearer + headers 两种假 MCP） |
| integration | Runtime 解析定义 → `tools/call`（headers）→ `isError` 传播 → 审计脱敏 |
| API | 非法 `auth_config` 返回 `MCP_CONFIG_INVALID`，DB 不变 |
| fixture | 假 MCP 三种认证模式 + 工具 effect + 会话头 |

### 3.6 部署与运维

- 不新增部署单元；无新依赖。
- 迁移随 Console 启动前 Alembic Job 执行。
- 回滚：代码回滚后旧行仍为合法 JSON；新结构在旧代码中 `auth_config` 不参与请求，不会报错。

## 4. 风险与依赖

| 风险 | 缓解 |
|---|---|
| SSRF/内网探测 | 本期记录，后续单独做出站白名单 |
| 静态头误填密钥 | 敏感头名校验 + 前端分离 + 审计断言 |
| 明文 HTTP | 文档提示；后续 HTTPS 策略 |
| Console/Runtime 规则漂移 | 两端共用同一校验/组装语义，契约测试覆盖 |

## 5. 需求追溯

| FEAT | 设计 | 测试 |
|---|---|---|
| FEAT-01 | 3.3 / 3.4 | unit + API |
| FEAT-02 | 3.2 / 3.4 | integration |
| FEAT-03 | 3.2 / 3.4 | integration |
| FEAT-05 | 3.7 | fixture |
| FEAT-07 | 全篇 | 文档检查 |

### 3.7 本地假 MCP 设计

文件：`tools/fake-business-platform/server.py`

- 新增 CLI：`--mcp-auth {none,bearer,headers}`、`--mcp-token`、`--mcp-header NAME=VALUE`（可重复）、`--mcp-path`（默认 `/mcp`）。
- `POST /mcp` 实现 JSON-RPC 子集：
  - `initialize`：返回 `protocolVersion=2025-03-26`、`capabilities.tools`、`serverInfo`，并下发 `Mcp-Session-Id`。
  - `notifications/initialized`：202 空响应。
  - `tools/list`：返回全部工具，无分页。
  - `tools/call`：按工具名返回文本内容；`platform.fail` 返回 `isError: true`。
  - 未知方法：`-32601`。
- 会话：initialize 生成随机 session id；后续请求必须携带，否则 401。
- 工具：
  - `platform.ping`（readOnlyHint）→ `pong`
  - `platform.targets.list`（readOnlyHint）→ 三个 target
  - `platform.echo`（无 annotations → WRITE）→ 回显 `message`
  - `platform.targets.delete`（destructiveHint）→ 返回删除 id
  - `platform.fail` → `isError: true`
- 认证：
  - `none`：不校验。
  - `bearer`：校验 `Authorization: Bearer <--mcp-token>`。
  - `headers`：校验每个 `--mcp-header`；未配置时启动失败。
- 就绪 JSON 增加 `mcpUrl`、`mcpAuth`、`mcpTools`（不回密钥值；测试令牌除外）。
- `GET /mcp` 返回 405 + `Allow: POST`；`_log_headers` 对密钥头脱敏。
- 不引入第三方 MCP SDK，保持单文件、零新依赖。

## 附录：术语表

| 术语 | 定义 |
|---|---|
| 静态头 | 非密、固定值的请求头 |
| secret_header | 承载 `auth_secret` 的请求头 |
| 冻结定义 | Run 内使用的 `ResolvedMcpServer`，不含密钥 |
