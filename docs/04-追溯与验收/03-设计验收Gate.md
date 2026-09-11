# 设计验收 Gate V1.9.1

## Gate 0：文档详细度

核心 design-full 模块必须具备：

- 文档控制；
- 背景/痛点；
- 功能清单；
- In/Out Scope；
- RULE；
- 正常/异常场景；
- 方案选型；
- 架构/流程；
- DB；
- API；
- lifecycle；
- DFX；
- deployment/rollback；
- risk/dependency；
- traceability；
- Compliance Matrix。

禁止用“见总体设计”“待实现”替代核心设计内容。Knowledge 规划项例外，但必须写清未决问题和禁止实现边界。

## Gate 1：交互闭环

- 所有菜单来自用户旅程；
- 新增按钮存在且表单完整；
- 编辑字段明确；
- 详情字段明确；
- Secret 不回显；
- Builder/Admin 角色差异明确；
- 列表 page size；
- 时间完整；
- 详情只读规则一致。

## Gate 2：领域闭环

- 一个概念只能有一个事实源；
- Service 是唯一发布对象；
- Skill dependency 来自 manifest；
- identity/route/grant 三关系拆分；
- ProjectPlatform/ProjectIntegration 分离；
- Agent direct capabilities 不等于 Skill deps。

## Gate 3：API/DB 闭环

- 每个 UI 写动作有 API；
- 每个 API 有 DTO/Domain；
- 每个持久字段有用途和约束；
- unique/FK/index/secret ref 明确；
- 不存在万能 JSON/万能 Resource 替代关系。

## Gate 4：Runtime 边界

- Runtime 无状态；
- Capability paging 不在 Skill；
- Skill 只依赖 SDK；
- 长任务进入 Execution；
- Async Task 属 Step detail；
- Gateway 负责 WebSocket/identity/route/grant。

## Gate 5：DFX

### Reliability

- crash/restart；
- retry/idempotency；
- cancellation；
- publish transaction；
- pagination limits。

### Security

- Secret；
- IDOR；
- trusted context；
- sandbox；
- upload path traversal；
- credential isolation。

### Observability

- request/trace/execution/step；
- audit；
- error taxonomy；
- no sensitive logs。

## Gate 6：测试

最低 E2E：

1. Service create must primary Agent；
2. Service publish；
3. Agent save immediate；
4. Skill import/new artifact；
5. Skill Mock→Dev Gateway→Production parity；
6. Capability 10000/100 paging；
7. Two users different MSS credentials；
8. Two Bot route two Agents；
9. `/bind` identity + no-grant deny；
10. `/new`；
11. `/stop` async；
12. Multi-Pod conversation；
13. Worker kill recovery；
14. large result artifact；
15. Secret no readback。

## Gate 7：编码开工条件

只有以下全部成立才能拆 coding tasks：

- interaction frozen；
- design-full updated；
- API DTO frozen enough；
- logical DB frozen enough；
- migration impact known；
- test scenarios executable；
- no unresolved P0 architecture question。

## Gate 8：变更规则

编码中发现设计问题时：

```text
issue
→ update interaction/design
→ review
→ update API/DB trace
→ update task
→ code
```

禁止：

```text
code workaround
→ later document what code happened to do
```
