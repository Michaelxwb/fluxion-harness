# PRD: MCP HTTP 接入增强与本地联调服务

> **文档编号**: PRD-2026-11-12-01
> **文档版本**: v1.0
> **创建日期**: 2026-11-12
> **产品负责人**: fluxion-harness
> **状态**: 已批准

**评审边界说明**:
- **需求基线**: 第 2-8 章 → 通过后锁定
- **设计输入**: 第 3-8 章 → 为 `cf-task:align` 提供 US / FEAT / NFR / 范围 / Spec refs

**ID 体系**: US（用户故事）、FEAT（功能）、NFR（非功能指标）、Spec/Rule（已有规范引用）

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 产品经理 | fluxion-harness | 需求定义、业务验收 |
| 开发负责人 | fluxion-harness | 技术方案确认 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-11-12 | fluxion-harness | 初始草稿 |
| v1.0 | 2026-11-12 | fluxion-harness | 范围锁定：仅 HTTP、自定义请求头、表单简化、本地假 MCP |

---

## 2. 背景与目标

### 2.1 问题陈述

| 维度 | 内容 |
|------|------|
| **问题描述** | 当前 MCP 仅支持 `Authorization: Bearer`。真实 MCP Server 常要求多个自定义请求头（如 `X-Api-Key`、`X-App-Id`、`X-Platform-Uid`），现有实现无法接入；同时注册表单暴露只读的传输协议、连接超时、缓存 TTL 和一段从未生效的原始 `auth_config` JSON，普通用户难以填写。 |
| **当前替代方案** | 把密钥塞进 `auth_secret` 只能发出 `Authorization: Bearer`，对自定义头服务无效；`auth_config` 字段只存不读，属于误导性配置。 |
| **触发原因** | 需要接入 `mss-knowledge`（Bearer）与 `sdsp-pms`（三个自定义头）两类真实服务，并希望本地有可验证的 HTTP MCP 服务。 |

### 2.2 目标与价值

| 维度 | 内容 |
|------|------|
| **核心目标** | 让 HTTP MCP 支持「无认证 / Bearer / 自定义请求头」三种服务级认证，并把注册表单收敛到最少必要字段。 |
| **预期价值** | 真实内网 MCP 可直接接入；注册操作不再需要理解 JSON 或内部字段；本地可端到端验证发现与调用。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|------|--------|--------|----------|
| 支持的 HTTP 认证形态 | 1（Bearer） | 3（无 / Bearer / 自定义头） | API + 运行时用例 |
| 注册表单常显字段 | 10 | ≤ 6（其余收进高级设置） | 前端契约测试 |
| 本地可用的 HTTP MCP | 无 | 1 个（双认证模式 + 多工具） | `tools/fake-business-platform` 冒烟测试 |

---

## 3. 用户与场景

### 3.1 目标用户

| 维度 | 内容 |
|------|------|
| **用户画像** | Console 的 Builder / Admin（注册与维护 MCP）；Runtime/前端开发者（本地联调）。 |
| **使用场景** | 管理员拿到第三方 MCP 的连接信息（URL + Bearer 或 URL + 自定义头），在 Console 注册并发现工具；开发者本地起假 MCP，验证发现、执行、审计与错误路径。 |

### 3.2 用户故事

| 编号 | 用户故事 | 优先级 |
|------|---------|--------|
| US-01 | 作为 Builder，我希望用 URL + Token 注册 Bearer 认证的 MCP，以便接入现有服务。 | P0 |
| US-02 | 作为 Builder，我希望用 URL + 多个自定义请求头（其中一个是密钥）注册 MCP，以便接入 `sdsp-pms` 这类服务。 | P0 |
| US-03 | 作为 Builder，我希望注册表单只展示必要字段，认证配置不用写 JSON，以便快速完成注册。 | P0 |
| US-04 | 作为开发者，我希望本地有一份支持 Bearer 与自定义头的 HTTP MCP 假服务，以便验证发现、调用和错误路径。 | P0 |
| US-05 | 作为 Builder，我希望粘贴 Claude/Cursor 的 `mcpServers` 配置即可自动填充表单，以便减少手工输入。 | P1 |

---

## 4. 功能需求

### 4.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源用户故事 |
|--------|---------|---------|--------|-------------|
| FEAT-01 | 结构化认证配置 | `auth_config` 改为 `{headers, secret_header}`；服务端校验头名、保留头、长度与密钥位置。 | P0 | US-02 |
| FEAT-02 | Console 发现与测试支持自定义头 | `discover-tools` 与连接测试按配置发送静态头和密钥头；Bearer 行为不变。 | P0 | US-02 |
| FEAT-03 | Runtime 执行支持自定义头 | 冻结定义携带非密 headers/secret_header；`tools/call` 按同一规则发送；密钥仍走 API-09 实时读取。 | P0 | US-02 |
| FEAT-04 | 注册表单简化 | 去掉只读传输协议、常显超时/TTL、原始 JSON；新增「认证方式」与请求头编辑器；高级参数折叠。 | P0 | US-03 |
| FEAT-05 | 本地假 MCP | `tools/fake-business-platform` 新增 `/mcp` Streamable HTTP 子集，支持无认证/Bearer/自定义头与 4-5 个工具。 | P0 | US-04 |
| FEAT-06 | mcpServers 配置导入 | 粘贴 `{"mcpServers": {...}}` 自动填充 URL、请求头和 Bearer 密钥；仅支持 HTTP 条目。 | P1 | US-05 |
| FEAT-07 | 规范与字段文档 | 更新 `harness-mcp` 约定、字段词典、API 错误与 README。 | P0 | US-01/02/03 |

#### FEAT-01: 结构化认证配置

- **描述**: 服务端只接受 `{"headers": {"头名": "非密值"}, "secret_header": "头名或 null"}`；空对象表示无自定义头。
- **验收标准**:
  - [ ] `secret_header` 为空时，`auth_secret` 按 Bearer 发送；非空时，密钥以原始值写入该头，不额外加 `Bearer `。
  - [ ] 静态头名称合法、无 CRLF/NUL、数量与总长度有上限；保留头（`Host`、`Content-Length`、`Accept`、`Content-Type`、`Mcp-Session-Id`、`MCP-Protocol-Version`、`Authorization`、`Cookie`）被拒绝。
  - [ ] `secret_header` 不得同时出现在 `headers`；静态头不得使用敏感名称（`token`/`secret`/`password`/`credential`/`api-key`/`apikey`/`private-key`）；敏感值只能进 `auth_secret`。
  - [ ] 非法配置返回 `MCP_CONFIG_INVALID`，不落库。

#### FEAT-02: Console 发现与测试支持自定义头

- **描述**: `McpClient` 接收静态头与密钥头，连接测试与工具发现共用同一构造逻辑。
- **验收标准**:
  - [ ] Bearer 服务：请求头仍为 `Authorization: Bearer <secret>`。
  - [ ] 自定义头服务：静态头原样发送，密钥头为原始密钥值。
  - [ ] 无凭据服务：不发送 `Authorization`，也不发送空密钥头。
  - [ ] 失败路径沿用既有语义：连接/超时/协议错误分类不变；发现失败保留上一成功 Catalog。

#### FEAT-03: Runtime 执行支持自定义头

- **描述**: `ResolvedMcpServer` 增加非密 `headers` 与 `secret_header`；Runtime 会话按同一规则组装请求头。
- **验收标准**:
  - [ ] 解析定义返回 headers/secret_header；Task snapshot 不包含认证配置，Run snapshot 可冻结非密 headers/secret_header 以保证 resume 一致；密钥永不进入。
  - [ ] `tools/call` 与 `initialize` 使用相同的头集合；会话重建后仍正确。
  - [ ] 密钥轮换后新调用使用 API-09 返回的新值；密钥不出现在日志/审计/Snapshot/`tool_call_audit`。
  - [ ] `isError: true` 仍按工具错误传播，不伪报成功。

#### FEAT-04: 注册表单简化

- **描述**: 常显字段收敛为：名称、标识、服务地址、认证方式、认证凭据、用户范围、启用状态；超时/TTL 收进「高级设置」折叠。
- **验收标准**:
  - [ ] 传输协议不再作为输入框；页面以静态文案说明仅支持 Streamable HTTP。
  - [ ] 「认证方式」三选一：无认证 / Bearer Token / 自定义请求头。
  - [ ] 自定义头模式提供增删行、头名唯一性校验、密钥头与密钥值分离输入。
  - [ ] 编辑已有服务时密钥不回显；留空表示不修改。
  - [ ] 关闭/取消不丢失已填内容；提交失败保持表单并给出字段级错误。

#### FEAT-05: 本地假 MCP

- **描述**: 在现有假业务平台上增加 `/mcp`，实现 `initialize`、`notifications/initialized`、`tools/list`、`tools/call` 子集，并支持三种认证模式。
- **验收标准**:
  - [ ] 启动参数可选择 `none`/`bearer`/`headers`；`headers` 模式校验配置的头名与值。
  - [ ] 工具覆盖 READ、WRITE、DESTRUCTIVE 三种 effect，并提供一个 `isError` 工具。
  - [ ] 初始化返回 `Mcp-Session-Id`，后续请求校验；`GET /mcp` 明确不支持。
  - [ ] README 给出 Bearer 与自定义头两种启动方式及 Console 配置示例。
  - [ ] 不引入新运行时依赖；日志对密钥头脱敏。

#### FEAT-06: mcpServers 配置导入（P1）

- **描述**: 表单提供「从配置导入」，解析单个 HTTP 条目并填充字段；无法识别的字段给出提示，不静默丢弃。
- **验收标准**:
  - [ ] `Authorization: Bearer x` 导入为 Bearer 模式。
  - [ ] 其他 headers 进入自定义头列表；用户可选择其中一个作为密钥头。
  - [ ] `command`/`args`（stdio）条目明确提示当前不支持。

#### FEAT-07: 规范与字段文档

- **描述**: 更新 MCP Spec 约定、Console 字段词典、错误码文档与假平台 README。
- **验收标准**:
  - [ ] `harness-mcp` 记录 `auth_config` 结构、密钥放置与审计边界。
  - [ ] 文档明确 V1 仍仅支持 Streamable HTTP，stdio 不在范围。
  - [ ] README 的两种启动示例可直接复制执行。

---

### 4.2 边缘情况

| 场景 | 预期行为 |
|------|----------|
| 自定义头重名（大小写不同） | 拒绝并提示头名重复 |
| 请求头值含换行/回车 | 拒绝，返回 `MCP_CONFIG_INVALID` |
| 密钥头同时出现在静态头 | 拒绝，提示密钥只能填在密钥值 |
| 切换到「无认证」 | 清空密钥与认证配置（显式提交空值） |
| 旧数据 `auth_config={"header":"X-Api-Key"}` | 迁移为 `{"secret_header":"X-Api-Key"}`；其他未知结构归零 |
| 发现失败 | 保留上一成功 Catalog，状态 `DISCOVERY_FAILED` |
| MCP Server 返回 `isError` | 工具调用失败，不写成功审计 |
| 端点不可达 | 连接测试返回 `UNAVAILABLE`，注册仍可保存 |

---

## 5. 非功能需求

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-SEC-01 | 安全 | `auth_secret` 不得进入 API 响应、日志、`config_audit_log`、Snapshot、`tool_call_audit`、`egress_audit`；静态头只允许非密值。 |
| NFR-SEC-02 | 安全 | 请求头校验覆盖 CRLF/NUL 注入、保留头、重复头与长度上限，避免请求走私。 |
| NFR-REL-01 | 可靠性 | 发现失败必须保留上一成功 Catalog；新增头配置不得改变既有失败分类。 |
| NFR-COMPAT-01 | 兼容性 | 既有 Bearer 配置与无认证配置行为不变；`auth_config={}` 视为无自定义头。 |
| NFR-OBS-01 | 可观测性 | 认证模式与头数量可观测，但不得记录头值或密钥。 |

---

## 6. 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | Streamable HTTP MCP；服务级认证（无/Bearer/自定义头）；单个密钥；非密静态头；Console 发现/测试；Runtime 执行；注册表单简化；本地假 MCP；配置导入（P1）。 |
| **非范围（Out of Scope）** | stdio / `command` / `args`；OAuth、动态客户端注册、mTLS；多个独立密钥；Tool 级授权/启停；MCP 代理网关；出站 URL 白名单（作为风险记录，单独需求）。 |
| **前置假设** | MCP Server 可由 Console 与 Runtime 网络访问；服务端接受 JSON 响应或 SSE；密钥可由管理员提供。 |

---

## 7. 依赖与风险

### 7.1 项目依赖

| 依赖方 | 依赖内容 | 最晚交付时间 | 风险等级 |
|--------|----------|--------------|---------|
| 无外部依赖 | 仅使用现有 `httpx` 与标准库 | — | 低 |

### 7.2 风险识别

| 风险ID | 描述 | 影响 | 缓解方案 |
|--------|------|------|----------|
| RISK-01 | MCP endpoint 由用户填写，存在 SSRF/内网探测面 | 可访问内网服务或元数据地址 | 记录为后续需求；本期至少保持审计与错误分类，不扩大权限 |
| RISK-02 | 用户把密钥填进静态头，进入 `config_audit_log` | 密钥泄漏 | 后端敏感头名校验 + 前端密钥输入分离 + 测试断言 |
| RISK-03 | 旧 `auth_config` 数据形状不一致 | 编辑报错或静默失效 | 数据迁移：`header` → `secret_header`，未知结构归零 |
| RISK-04 | 内网服务使用明文 HTTP | 凭据在链路上明文传输 | 文档提示；后续支持 HTTPS 强制策略 |
| RISK-05 | 自定义头在 Runtime 与 Console 之间漂移 | 发现成功、执行失败 | 共用同一解析/组装函数与契约测试 |

---

## 8. Existing Spec Constraints

| Spec/Rule | 约束 | 对范围/验收的影响 | 状态 |
|-----------|------|------------------|------|
| `mcp/harness-mcp#RULE-mcp-001` | V1 仅 Streamable HTTP；Catalog 由 `discover-tools` 维护；Server 级范围；Tool 必须进 ToolRegistry | 本期不引入 stdio；不放宽 Tool 级授权；FEAT-02/03/05 验收 | applied |
| `secret/harness-secret#RULE-secret-001` | 密钥明文存 Owner 表，不得进日志/审计/Snapshot/API 响应 | FEAT-01/03、NFR-SEC-01 | applied |
| `arch/harness-arch#RULE-arch-001` | 固定四个逻辑部署单元 | 本期不新增部署单元；假 MCP 是开发工具 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| MCP | Model Context Protocol |
| Streamable HTTP | MCP 的 HTTP 传输，JSON-RPC over POST，可返回 JSON 或 SSE |
| secret_header | 承载 `auth_secret` 的请求头名称；为空时使用 `Authorization: Bearer` |
| 静态头 | 非密、固定值的请求头，如 `X-App-Id` |

---

*文档结束*
