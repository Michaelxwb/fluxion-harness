---
id: fluxion-dfx
description: Fluxion 编码阶段必须满足的 DFX 非功能与工程质量约束
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-fluxion-dfx-001
    type: manual
    config:
      checklist: 检查可用性、可靠性、扩展性、性能、安全、可维护性、可测试性、可观测性、可部署性、兼容性、可恢复性和可运维性。
      owner: project-owner
---

# Fluxion DFX 规范

## Rules

- [RULE-fluxion-dfx-001] DFX 必须在编码阶段实现并通过自动化证据验证，不能在功能完成后再补。

## Guidance

- 外部依赖必须定义 timeout、retry 和 fail/circuit-breaker policy。
- Cache 必须定义 key scope、TTL、invalidation 和 stale 行为。
- Plugin/Hook 必须定义 trust、timeout、fail policy 和观测指标。
- 关键路径必须可 Trace，并关联 execution_id 和资源版本。
- SQLite/PostgreSQL Contract Test 通过率 100%。
- P0/P1 验收自动化率至少 95%。
- Runtime 框架开销满足设计文档性能预算。
- 通过依赖边界测试保证 Kernel 不引用具体 Provider/Plugin。
