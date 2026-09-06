# Review 缺陷修复设计

2026-09-06 用户授权：确认上一轮六项问题是否存在，存在则修复闭环。
沿用 remediation-plan.md §4.1、§4.7、§7.3、§8.6、§8.7 的既有语义，不修改核心 Contract。
适用规范继承原 Context 的 backend/code-quality-performance、architecture/resource-registry、backend/console-api-contract、architecture/runtime-core、frontend/quality-standards、frontend/semi-design；原 Rule owner 保留，本任务负责新增回归场景。

## 凭据

S-01：创建不得覆盖既有 SecretRef；冲突/同名创建和并发创建不能改变原密钥。服务端生成唯一逻辑 ID，显示名独立。
S-02：逻辑凭据禁用必须覆盖轮换保留的所有版本；旧 Provider 引用后续解析也必须失败。保持租户隔离，写独立 Audit，明文不得进入 Registry 或响应。使用既有 SecretStore 接口，不更改协议。
两场景使用真实 Console Service、Registry、SecretStore/CredentialResolver integration 测试，并回归凭据浏览器 Journey。

## 评测

S-03：Eval 的 Agent target 必须匹配 Trace Snapshot 中 Agent ID 与精确版本；缺失/错 Agent/错版本拒绝且不写 EvalRun；正确目标成功。Workflow target 使用现有执行证据校验，不凭共享 RuntimeProfile 冒认目标。真实 Eval Service、Registry、TraceStore integration。

## 默认配置

S-04：唯一默认检查只看各逻辑 Profile 最新 published 版本，与 Resolver 选择一致；历史 default=true 不阻挡新默认。保留活跃默认冲突保护，SQLite/PostgreSQL 使用同一 Contract Test。

## 模型刷新

S-05：刷新读取既有 Provider 真实发布版本，不重复 publish；新增 Model pin 正确 Provider 版本。Browser → HTTP → Registry → 本地模型 stub E2E。

## 编辑器

S-06：Tool、Skill、MCP、Policy、Workflow 发布后继续编辑必须切换到新 Draft，旧版本保持不变；发布成功提示不丢失。Browser → HTTP → Registry E2E 覆盖各编辑器，补组件交互回归。
