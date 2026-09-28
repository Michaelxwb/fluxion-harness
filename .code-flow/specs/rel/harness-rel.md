---
id: harness-rel
description: Agent Harness 通用平台规则：rel
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-rel-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/console_platform/test_user_side_relations.py
    cwd: .
    timeout: 300
---

# harness-rel

## Rules

- [RULE-rel-001] 关系类修改使用单关系 POST/DELETE 并由独立事务完成；禁止用全量 PUT 覆盖整个关系集合。

## Conventions

Console 侧关系变更的三条稳定口径：

- **每次关系变更必须与业务变更同一事务写 `control.config_audit_log`**：审计与业务改动共用同一 session（`AuditService` 复用调用方 session，`application/audit_service.py:28-34,57-78`），并在同一动作内落 `action` 与关系快照（`after`）。审计行缺失或落在另一个事务里，一旦业务回滚就会留下孤儿审计 / 一旦审计失败就读不到变更。
  - ✅ Agent→MCP 解绑：`action="REVOKE"` + before/after 快照（`application/agent_mcp_service.py:104-116`）；Skill 绑定/授权同款（`application/skill_service.py:723-731,751-766,768-783`）
  - ❌ 只改 `is_deleted` 不留审计行
  - 登记缺口（代码证实、快照缺位）：User→Agent 授权仍写 `before=None`——只能看到结果态、无法对账「改前是什么」（`application/grant_service.py:114-134`）
- **解除关系的幂等语义按端点分叉，同一关系的两入口必须一致声明**：Agent 侧 `DELETE /agents/{id}/users/{uid}` 幂等成功（关系不存在也返回成功），User 侧 `DELETE /users/{id}/agents/{aid}` 关系不存在时抛 `COMMON_NOT_FOUND`（`api/agents.py:301-307` 传 `idempotent=True` vs `api/users.py:196` 用默认值；分叉点在 `application/grant_service.py:76-82`）。
  - ✅ 调用 `GrantService.revoke` 时显式声明 `idempotent=`；❌ 一个入口成功、另一个 404 而无测试钉死
- **重新绑定已软删的关系必须「复活」原行，不得插新行**：`find_any` → `is_deleted=False` + 刷新 `update_time`（必要时更新 `sort_order`），复用同一 id；partial unique `WHERE is_deleted=false` 正是为这套复活模式留位，直接 `add(新行)` 会因旧行已软删而绕过唯一约束，产生同关系的重复行（`application/skill_service.py:603-617`、`application/grant_service.py:44-62`；索引见 `infrastructure/models/control.py:207-213`、`infrastructure/models/mcp.py:93-99`）。
  - ✅ `find_any` → 复用同一 id；❌ `self._session.add(AgentSkillBinding(...))`（软删旧行仍在盘）

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
