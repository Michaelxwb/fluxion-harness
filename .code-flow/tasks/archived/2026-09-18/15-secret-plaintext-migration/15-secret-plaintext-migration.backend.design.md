# 密钥明文入库专项 模块需求与设计一体化文档

> **文档编号**: MOD-SECRET-V1.0
> **文档版本**: v1.0
> **创建日期**: 2026-09-18
> **文档状态**: 已批准（project-owner 决策）
> **模板**: design-full.md

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | 密钥明文入库专项（移除 SecretRef 机制） |
| Owner | muad-platform-sdk + muad-api + muad-console-platform + muad-agent-runtime + muad-im-gateway |
| 数据 Owner | control |
| 前置模块 | 01-platform-foundation、03-model-management |
| 决策依据 | 2026-09-18 project-owner：所有 Secret 明文入库，跨表以主键引用；不再引入 Secret Provider 读写逻辑 |

### 1.1 修订历史

| 版本 | 日期 | 作者 | 变更说明 |
|---|---|---|---|
| v1.0 | 2026-09-18 | fluxion-harness | 初始设计：移除 `secret_ref`/SecretProvider，6 张表密钥列改明文，契约/消费侧/迁移/规范/文档同步 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 业务背景 | 现有设计以 `secret://` 引用 + SecretProvider 解析，跨机迁移与运维成本高；owner 决定密钥明文入库、其他表以主键引用。 |
| 核心目标 | 删除 SecretRef 解析链路；6 张表密钥列直接存明文；契约改为携带密钥字段；保留“不进日志/审计/Snapshot/LLM/API 响应”的脱敏边界。 |

### 2.2 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | `model_definition.api_key`、`bot_account.secret`、`mcp_server.auth_secret`、`project_platform.auth_secret`、`user_credential_ref.credential_json`、`shared_credential_ref.credential_json`；contracts/api-kit/platform-sdk 同步；0004/0005/0006 迁移与 backfill；受影响测试与文档/规范同步。 |
| Out of Scope | 不做应用层加密、不保留 SecretProvider 兼容层、不做密钥轮换接口；不改变“日志/审计/Snapshot/LLM 不含密钥”的脱敏要求。 |
| 技术债 | 无 |

### 2.3 验收条件

#### 2.3.1 业务规则与约束

| ID | 类型 | 描述 | 验证场景 |
|---|---|---|---|
| RULE-01 | 系统约束 | 密钥明文入库；跨表以主键引用，不再存 `secret_ref`；`secret://` 与 `SecretProvider` 代码路径全部删除。 | S-01 / E-01 |
| RULE-02 | 系统约束 | 密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 与 API 响应（用 `api_key_configured` / `credential_configured` 表达）。 | S-01 / E-02 |
| RULE-03 | 系统约束 | 迁移采用 expand→backfill→contract；backfill 在旧机提供环境变量前不得执行；不可解析行必须显式列出。 | S-04 / E-03 |
| RULE-04 | 系统约束 | 模型调用（Runtime）与 Bot 建连（Gateway）直接消费 DB 字段；缺密钥时的错误语义与旧 `CREDENTIAL_MISSING` 保持一致。 | S-02 / S-03 / E-04 |

#### 2.3.2 功能验收场景

| 场景ID | 测试层级 | 关键真实边界 | 操作/前置 | 预期结果 |
|---|---|---|---|---|
| S-01 | E2E | Browser→models API→DB（依赖 03-model-management 前端；当前 e2e_deferred） | 新增模型含 API Key | 明文落库；响应与列表仅显示“已配置”；日志/审计无明文 |
| S-02 | integration | Runtime→DB→模型端点 | 模型配置含 `api_key`，执行一次 Agent Run | 请求携带该 Key；无 Provider 依赖 |
| S-03 | integration | Gateway→DB→企业微信 SDK Port | Bot 配置含 `secret`，触发建连 | SDK 使用该 Secret；无 Provider 依赖 |
| S-04 | integration | Alembic expand→backfill→contract | 旧数据含 `secret_ref` + 环境变量 | backfill 写入明文列；contract 后旧列消失 |
| E-01 | unit | 代码库静态检查 | 全仓扫描 | 无 `secret://`、`SecretProvider`、`secret_ref` 残留 |
| S-05 | unit | 文档/规范静态一致性 | 扫描 docs 与 specs | 文档无 SecretRef 残留，规则为明文政策 |
| E-02 | integration | 日志/审计捕获 | 含密钥的写操作 | 日志与 `config_audit_log` 无明文 |
| E-03 | integration | backfill CLI | 环境变量缺失 | 列出不可解析行并非零退出 |
| E-04 | integration | Runtime/Gateway | 密钥列为空 | 返回 `CREDENTIAL_MISSING`，不发起外部请求 |

无可靠实测数据的性能阈值统一标记“待定”。

## 3. 技术设计

### 3.1 技术选型与关键决策

| 决策 | 选择 | 放弃项 | 理由 |
|---|---|---|---|
| 密钥存储 | 明文入库 | SecretRef + Provider | owner 决策：迁移/运维简单 |
| 加密 | 不加密 | 应用层对称加密 | 后续如需再加，列为可选强化 |
| 引用方式 | 主键引用（model_id/bot_account_id/mcp_server_id/platform_id/user_id） | 字符串 ref | 关系清晰 |
| Provider | 删除（含 platform-sdk credential 模块） | 保留兼容层 | 避免双实现漂移 |

### 3.2 数据设计

| 表 | 旧列 | 新列 | 备注 |
|---|---|---|---|
| `control.model_definition` | `secret_ref` | `api_key text` | 03 已按此设计（0004） |
| `control.bot_account` | `secret_ref` | `secret text`（nullable，04/10 建 Bot 时强制必填） | 0004/0005 |
| `control.mcp_server` | `auth_secret_ref` | `auth_secret text` | 0004 |
| `control.project_platform` | `auth_secret_ref` | `auth_secret text` | 0004 |
| `control.user_credential_ref` | `secret_ref` | `credential_json jsonb NOT NULL DEFAULT '{}'` | 0004 |
| `control.shared_credential_ref` | `secret_ref` | `credential_json jsonb NOT NULL DEFAULT '{}'` | 0004 |

迁移编号约定：`0004` expand（加新列，保留旧列）→ backfill CLI → `0005` contract（删旧列）。契约字段（`ResolvedModel.api_key`、`BotSnapshotItem.secret`、`McpServerSnapshot.auth_secret` 等）在 expand 版本起同时提供新旧字段，contract 版本删除旧字段。

### 3.3 代码改造点

- `packages/contracts`：`resolve.ResolvedModel.secret_ref→api_key`；`channel.BotSnapshotItem.secret_ref→secret`；MCP/凭据契约随 06/04 模块发布时同步。
- `packages/api-kit/startup.py`：`validate_startup` 移除 `secret_provider` 参数与可达探针；保留配置/迁移/存储挂载校验。
- `packages/platform-sdk`：删除 `credential/`（Provider、EnvSecretProvider、CredentialResolver、SecretValue）及导出；Runtime/Gateway 改为直接读字段。
- `apps/agent-runtime`：`executor.resolve_model_api_key` 直接用 `ResolvedModel.api_key`；空值时 `CREDENTIAL_MISSING`。
- `apps/im-gateway`：`wecom/adapter.py` 用 `BotSnapshotItem.secret`；连接缓存比较改看字段。
- `apps/console-platform`：`resolve_service`/`channel_service` 输出明文字段（旧列过渡期双写/双读）。

### 3.4 迁移与回滚

- backfill CLI：`python -m muad_console_platform.cli backfill-secrets`，按 6 张表逐行解析旧 `secret_ref`（`MUAD_SECRET__*` 环境变量）写入新列；失败行输出清单并非零退出；幂等（已有新值则跳过）。
- 回滚：contract 之前可 `downgrade` 回 expand 状态；contract 之后回滚需从 DB 备份恢复。
- 换机：旧机 `pg_dump` + 导出 `MUAD_SECRET__*` 环境变量；新机按“expand → backfill → contract”顺序执行。

### 3.5 质量实现方案

- 安全：仅保留“脱敏边界”要求（日志/审计/Snapshot/LLM/API 响应无明文）；`sanitize_payload` 与 logging redaction 继续生效。
- 质量门：新增静态检查（无 `secret://`/`SecretProvider`/`secret_ref` 残留）；迁移 expand/contract 均需 upgrade/downgrade 冒烟 + schema parity。
- 可观测：backfill 输出统计（解析成功/失败/跳过）与 trace_id。

## 4. 部署与运维

随 `muad-platform-sdk + apps` 对应镜像发布；换机手册按 §3.4 执行；DB 备份与只读账号需按“密钥明文”重新评估权限。

## 5. 风险与依赖

- 风险：DB 备份/只读账号暴露全部凭据（owner 已接受）；忘记导出旧机环境变量导致 backfill 不可解析。
- 应对：backfill 非零退出 + 失败清单；文档标注换机必须携带 Secret 环境变量。

## 6. 需求追溯矩阵

| 用户故事 | 接口ID | 测试用例ID | 测试层级 | 状态 |
|---|---|---|---|---|
| owner 决策 | - | S-01~S-04, E-01~E-04 | E2E/integration/unit | 待实现 |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-secret#RULE-secret-001` | required | 规则重写：密钥明文入库、主键引用、脱敏边界保留 | §2.3.1 / §3.3 / §3.5 | S-01, E-02 | applied |
| `harness-data#RULE-data-001` | required | 6 张表列改造 + expand/contract 迁移与 parity | §3.2 / §3.4 | S-04 | applied |
| `harness-im#RULE-im-001` | required | Gateway 直接消费 Bot 明文 Secret | §3.3 | S-03 | applied |
| `harness-test#RULE-test-001` | required | 迁移/消费/脱敏的 E2E 与 integration 覆盖 | §2.3.2 | S-01~S-04, E-01~E-04 | applied |
