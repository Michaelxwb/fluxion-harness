---
design_template: frontend
---

# MCP HTTP 认证与表单简化 前端设计

> **文档编号**: FE-MCP-HTTP-AUTH-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-12
> **文档状态**: 设计评审中

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | MCP Server 注册/编辑表单 |
| 前端目录 | `apps/console-platform/frontend/src/modules/mcp-management/` |
| 交互基线 | 现有 MCP 页面；本次只改注册/编辑弹窗与详情展示 |
| 依赖 | 后端 `auth_config={headers, secret_header}` 契约 |

### 1.1 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v1.0 | 2026-11-12 | fluxion-harness | 初始设计：认证方式、请求头编辑器、高级折叠、配置导入 |

## 2. 需求分析

### 2.1 需求概述

把注册表单从"原始 JSON + 只读协议 + 常显超时/TTL"改为"接入方式 + 认证方式 + 按需展开"的结构，同时支持 Bearer 与自定义请求头两类真实服务。

### 2.2 功能方案

| 功能ID | 功能名称 | 优先级 | 来源 |
|---|---|---|---|
| FEAT-04 | 表单简化 | P0 | US-03 |
| FEAT-01/02 | 认证方式与请求头编辑 | P0 | US-02 |
| FEAT-06 | `mcpServers` 配置导入 | P1 | US-05 |
| FEAT-07 | 文案与字段说明 | P0 | US-03 |

### 2.3 范围与边界

- In：注册/编辑弹窗、认证字段、请求头行编辑、高级折叠、配置导入、详情页认证摘要。
- Out：MCP 列表/工具/用户范围页面结构；stdio 表单；OAuth 字段。

### 2.4 验收条件

引用 `mcp-http-auth.scenarios.md` 的 S-01~S-04、E-01~E-05、B-01~B-03。

## 3. 前端技术设计

### 3.1 技术选型

沿用 React + Semi UI `Form`/`FormModal`；请求头编辑器使用受控数组 + `Form.Input` 行内渲染；不引入新表单库。

### 3.2 页面与路由结构

无新增路由。改动：

- `McpFormModal.tsx`：主表单。
- `AuthModeFields.tsx`（新增）：认证方式与凭据字段。
- `HeaderRowsEditor.tsx`（新增）：请求头增删行。
- `McpConfigImport.tsx`（新增，P1）：粘贴 `mcpServers` JSON。
- `McpDetailSideSheet.tsx`：基础信息增加"认证方式"摘要（Bearer / 自定义头 N 个 / 无），不展示头值。
- `services/mcpServers.ts`：`McpServerDetail.auth_config` 改为 `{ headers: Record<string,string>; secret_header: string | null }`。

### 3.3 组件设计

#### 表单布局

```text
注册 MCP                                        [Streamable HTTP]

名称* [__________]    标识* [__________]
服务地址* [____________________________________]
                     [从配置导入]
认证方式 [无认证 ▼ / Bearer Token ▼ / 自定义请求头 ▼]
  ├ Bearer：Token [••••••]（编辑时提示"已配置，留空不修改"）
  └ 自定义头：
      请求头
        [头名] [值] [删除]
        [+ 添加请求头]
      密钥请求头 [X-Api-Key]   密钥值 [••••••]
用户范围 [指定用户 ▼]   启用状态 [启用 ▼]
▸ 高级设置（连接超时 [5000] ms · 缓存 TTL [300] s）
                                   [取消] [保存并发现工具]
```

- `Streamable HTTP` 是静态 Tag/文案，不是输入框。
- 高级设置使用可折叠面板，默认收起；编辑已有服务时若值非默认，折叠标题显示"已自定义"提示。
- 底部长 Banner 改为一行提示："保存后自动发现工具；工具目录跟随用户范围，不做 Tool 级授权。"

#### 认证模式

| 模式 | 提交 `auth_config` | 提交 `auth_secret` |
|---|---|---|
| 无认证 | `{}` | `''`（清除） |
| Bearer Token | `{}` | 用户输入；编辑留空则不传 |
| 自定义请求头 | `{headers, secret_header}` | 密钥值；编辑留空则不传 |

- 编辑时推导模式：`secret_header` 非空或有 `headers` → 自定义头；否则 `auth_secret_configured` → Bearer；否则无认证。
- 切换模式时清空另一模式的本地输入，但已配置密钥保持"留空不修改"语义。
- "无认证"提交空字符串以显式清除旧密钥；后端必须接受空串清除。

#### 请求头编辑器

- 每行两个输入 + 删除按钮；"添加请求头"追加空行。
- 头名必填、唯一（大小写不敏感）、符合 token 规则；值必填（允许空字符串？统一要求非空，避免误配）。
- 密钥头名称可与静态头不同；若与静态头重名，前端直接提示。
- 头名/值含换行、回车、NUL 时本地拦截。
- 静态头敏感名（`token`/`secret`/`password`/`credential`/`api-key`/`apikey`/`private-key`）本地提示"密钥请填在密钥值"。

### 3.4 组件接口契约

```ts
export type AuthMode = 'none' | 'bearer' | 'headers';

export interface HeaderRow {
  name: string;
  value: string;
}

export interface McpAuthValues {
  auth_mode: AuthMode;
  auth_secret?: string;
  secret_header?: string;
  headers: HeaderRow[];
}

export interface McpFormValues extends McpAuthValues {
  name: string;
  key?: string;
  endpoint: string;
  user_scope: 'ALL' | 'SELECTED';
  enabled: 'true' | 'false';
  connect_timeout_ms?: number;
  tool_cache_ttl_sec?: number;
}

export function buildAuthPayload(values: McpAuthValues): {
  auth_config: McpAuthConfig;
  auth_secret?: string;
};
```

`buildAuthPayload` 是唯一组装入口，单测覆盖三态与编辑留空。

### 3.5 状态与数据流

- `FormModal` 持有 `FormApi`；`headers` 用 `Form` 数组字段或本地 `useState<HeaderRow[]>`，提交时合并。
- 编辑初始化：从 `server.auth_config.headers` 复制行；`secret_header` 填入；`auth_secret` 留空。
- 提交：本地校验 → `buildAuthPayload` → `create/update` → 创建成功后 `discoverTools`（失败不阻塞，沿用现有 Toast）。
- 配置导入（P1）：解析 JSON 后只填充字段，不直接提交；stdio 条目提示不支持。

### 3.6 UI 状态

| 状态 | 表现 |
|---|---|
| 提交中 | `confirmLoading`，按钮禁用 |
| 字段错误 | `Form.setError`，弹窗不关闭，保留输入 |
| `MCP_CONFIG_INVALID` | 定位到 endpoint 或认证区，展示后端消息 |
| 密钥已配置 | 密码框 placeholder/extraText "已配置，留空不修改" |
| 无认证 | 隐藏凭据字段；提示将清除已保存密钥 |
| 高级参数自定义 | 折叠标题显示"已自定义" |

### 3.7 文案与 i18n

新增/调整 `zh-CN.json`、`en-US.json`：

- `mcp.form.authMode` / `authModeNone` / `authModeBearer` / `authModeHeaders`
- `mcp.form.secretHeader` / `secretHeaderHint`
- `mcp.form.headers` / `headerName` / `headerValue` / `addHeader` / `removeHeader`
- `mcp.form.headersInvalid` / `headerReserved` / `headerSecretName` / `headerDuplicate`
- `mcp.form.transportStatic`（"仅支持 Streamable HTTP"）
- `mcp.form.advancedToggle` / `advancedCustomized`
- `mcp.form.importConfig` / `importHint` / `importStdioUnsupported`
- `mcp.detail.authMode` / `authModeBearer` / `authModeHeaders` / `authModeNone`

移除或不再使用：`mcp.form.authConfig`、`authConfigHint`、`authConfigInvalid`、`mcp.form.transportHint`（保留 `transport` 字段用于详情展示）。

### 3.8 可访问性与兼容性

- 请求头行的删除按钮带 `aria-label`；新增/删除后焦点落在新行第一个输入。
- `data-testid`：`mcp-auth-mode`、`mcp-add-header`、`mcp-header-name-{index}`、`mcp-header-value-{index}`、`mcp-secret-header`、`mcp-secret-value`、`mcp-advanced-toggle`、`mcp-import-config`。
- 兼容现有 Semi 主题与暗色模式；不新增全局样式。

## 4. 测试建议

| 层 | 用例 |
|---|---|
| unit | `buildAuthPayload` 三态；编辑留空；无认证清空；敏感头/重复头/CRLF 校验 |
| contract | `tests/frontend/test_mcp_form_contract.py` 断言无原始 JSON、认证方式、高级折叠、导入入口 |
| component | 表单初始化/提交失败保留输入/密钥不回显 |
| E2E | 创建 Bearer 与自定义头两种 MCP，发现工具成功；假 MCP 作为后端 |

## 5. 风险与依赖

| 风险 | 缓解 |
|---|---|
| 表单状态与 Semi Form 数组字段耦合 | 用本地 `headers` state + 提交合并，避免动态字段 API 复杂度 |
| 编辑时模式推导歧义 | 明确优先级：`secret_header`/headers > secret_configured > none |
| 配置导入误判密钥头 | 导入后让用户确认密钥头，不自动保存 |

## 附录：术语表

| 术语 | 定义 |
|---|---|
| 认证方式 | 无认证 / Bearer Token / 自定义请求头 |
| 密钥头 | 承载 `auth_secret` 的请求头名称 |
