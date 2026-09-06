---
id: fluxion-resource-registry
description: Fluxion Resource、Definition/Binding、PostgreSQL Registry、版本与租户边界
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-fluxion-resource-001
    type: manual
    config:
      checklist: 检查 Resource 版本不可变、Definition/Binding、tenant scope、PG Contract Test 和 SecretRef。
      owner: project-owner
---

# Fluxion Resource 与 Registry 规范

## Rules

- [RULE-fluxion-resource-001] 所有可配置事实必须资源化、版本化并保存于 Registry；用户/租户差异通过 Binding 表达；Registry 唯一实现为 PostgreSQL（ADR-A007 单库 Contract）。

## Guidance

- Published Resource 不可原地修改。
- Agent、Skill、MCP、Plugin、Workflow、Policy 使用稳定 Resource ID + Version。
- 用户/租户 Credential、配置、Grant 放 Binding，不进入 Definition。
- Dev/Prod 同 PostgreSQL（ADR-A007）。
- YAML 只能 import/export，不能成为运行 Source of Truth。
- Repository/Store 查询必须强制 tenant scope。
- Secret 只保存 SecretRef 或经 SecretStore 加密后的值，不得进入普通 Resource Spec。
- PG 跑同一套 Migration 和 Contract Test（单库，ADR-A007）。
