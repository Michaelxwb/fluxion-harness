# ADR-009: Capability 接口保留在 Runtime，Center 归业务层

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P12

## Context

Tool、Workflow Step、Business API 不能分别实现同一套业务逻辑，否则同一个 `create_employee` 会有多套业务规则和长期维护成本。Tool/Step 是 Adapter，Capability 才是真实业务能力。

## Constraints

- 业务逻辑只实现一次，通过稳定 Capability Contract 暴露。
- 开源 V1 是业务无关 harness，不包含具体业务 Capability 实现。
- Agent Tool 与 Workflow Step 共用同一 Capability 抽象。

## Decision

**Runtime 保留 Capability 接口抽象**（`CapabilityProvider`、`CapabilityDescriptor`、统一 Schema/Error Model），使 Agent Tool 与 Workflow Step 都收敛到 Capability：

```text
                  Capability
                      ▲
          ┌───────────┴───────────┐
      Agent Tool             Workflow Step（业务接入层）
```

**Capability Center / Registry（管理与发现业务 Capability 的实现与注册表）归业务接入层**，不在开源 V1。开源层只定义接口与契约，业务层提供实现。

## Trade-offs

- 换取业务逻辑单点实现与开源层业务无关，代价是 Capability 实现与注册表需要业务方自行搭建。

## Failure Modes

- 业务逻辑泄漏进 Agent Tool Adapter → 通过 §12 分层规则 + 评审控制。
- Capability 接口过早定型导致业务扩展受限 → 接口保持最小，Schema 可版本化。

## Validation

- S-R08：Capability Contract 解析与调用（接口级）。
- Tool Adapter 与 Workflow Step 共用 Capability 的实现路径在业务接入阶段验证。

## Revisit Conditions

- 业务接入后发现 Capability 接口抽象不足以支撑通用业务能力，需要扩接口。
