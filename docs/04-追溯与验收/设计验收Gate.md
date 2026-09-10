# 设计验收 Gate

## 1. 架构 Gate

| Gate | 验证目标 | 最低自动化要求 |
|---|---|---|
| G01 Core Purity | Framework Core 不依赖具体 Integration | import/dependency scan |
| G02 Stateless Runtime | Agent Runtime 不依赖 Pod-local user state | Multi-Pod E2E |
| G03 Execution Boundary | 高风险/长任务必须进入 ExecutionService | integration |
| G04 Worker Recovery | crash 后过期 RUNNING 可被其他 Worker reclaim | SIGKILL E2E |
| G05 Long Wait | Wait 写 next_run_at 并释放 Worker | integration/E2E |
| G06 Redis Down | Redis 故障不丢 Execution | E2E |
| G07 Control Plane Down | 管理面故障不终止已运行 Execution | E2E |
| G08 Idempotency | 重复 Start/Step/Delivery 不产生重复副作用 | integration/E2E |
| G09 Capability Reuse | Agent Runtime/Worker 共用 Registry/Auth/Provider | contract test |
| G10 Auth Replaceability | 至少两种 AuthProvider 不修改 Core | integration |
| G11 Knowledge Replaceability | 更换 KnowledgeProvider 不修改 Agent/Worker | integration |
| G12 Channel Replaceability | 不同 Channel 统一 ChannelEnvelope | contract/E2E |
| G13 Sandbox Boundary | 绝对路径/穿越/跨 Workspace/未授权 shell 被拒 | unit/integration |
| G14 Unified Response | 普通 JSON API 使用统一 Envelope | architecture/integration |
| G15 Unified Logging | request_id/service/event + redaction | unit/integration |
| G16 Semi Design | Console 不引入第二套通用 UI Library | package/AST scan |
| G17 Standard List | CRUD 列表复用 StandardListPage | component/architecture |
| G18 Simplified Publish | 只有 Service/关键 Agent 使用 Draft/Published | schema/API review |

## 2. 不得降级为 Mock 的边界

以下场景的最终 Gate 不能只靠 mock：

```text
Worker SIGKILL 恢复
PostgreSQL claim/lease
Redis Down
Control Plane Down
Multi-Pod Stateless
Channel Delivery
Production Sandbox（真正启用 shell 前）
数据库 migration
Console E2E 路由/API/UI
```

## 3. Gate 与编码完成定义

一个模块不能仅以“代码编译成功”作为完成标准。

至少满足：

```text
对应模块设计完成
+
P0 FEAT 正常/异常场景有测试
+
关联 Architecture Gate 通过
+
日志/Response/Trace 契约一致
+
无未说明的架构偏差
```
