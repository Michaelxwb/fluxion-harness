# 安全政策（Security Policy）

Fluxion Harness 处理 Agent 运行态配置、用户/租户绑定、Credential 与审计数据，安全是我们的首要关切。

## 支持版本

| 版本 | 支持状态 |
|------|----------|
| 0.1.x（当前） | 支持 |

## 报告漏洞

如发现安全漏洞，**请勿在公开 Issue 中披露**。请将细节发送至项目维护者（见仓库 OWNERS/团队联系方式），
我们会尽快确认并响应。请在报告中包含：

- 受影响组件与版本；
- 复现步骤与影响面；
- 建议的修复思路（可选）。

我们会在修复并发布后，与报告者协商公开披露时间线（负责任披露）。

## 已知安全边界

- **本地 dev 模式**（`fluxion serve --dev`）会为缺失的 `FLUXION_SECRET_MASTER_KEY` 随机生成内存 key，
  仅用于本地开发，**不得用于生产**。
- 生产必须外置配置 `FLUXION_SECRET_MASTER_KEY`（32 字节）与 `FLUXION_DATABASE_URL`（PostgreSQL），
  并通过 Secret 管理（见 `deploy/helm`）。
- Secret 永不进入 Resource Spec、日志或 Trace（使用 SecretRef / SecretStore）。

## 安全设计要点

- SecretRef + SecretStore：AES-256-GCM，Master Key 外置。
- Tenant 全链路隔离：Resource / Cache / Binding / Session / Trace / Authorization 均带 tenant。
- 插件信任边界：不可信扩展走 MCP / RPC / Sandbox / isolated worker。
- 高风险操作（Publish / Rollback / Binding / Policy / Bind）进入独立 AuditLog。
