# Architecture Decision Records

Create an ADR before changing any non-negotiable architecture contract.

Required sections:
- Context / Problem Driver (Pxx)
- Constraints
- Options
- Decision
- Trade-offs
- Failure Modes
- Validation
- Revisit Conditions

## Index

| ADR | 主题 | Problem Driver |
|-----|------|----------------|
| [ADR-001](adr-001-stateless-agent-runtime.md) | Stateless Agent Runtime | P02 |
| [ADR-002](adr-002-runtimeprofile-agent-pod-decoupling.md) | RuntimeProfile 与 Agent Pod 解耦 | P05 |
| [ADR-003](adr-003-definition-binding-resource-model.md) | Definition + Binding 资源模型 | P03 |
| [ADR-004](adr-004-registry-store-abstraction.md) | Registry Store 抽象（SQLite dev / PostgreSQL prod） | P04 |
| [ADR-005](adr-005-execution-snapshot.md) | Execution Snapshot | P07 |
| [ADR-006](adr-006-microkernel-plugin-runtime.md) | Microkernel + Plugin Runtime | P08 |
| [ADR-007](adr-007-typed-lifecycle-hook.md) | Typed Lifecycle Hook | P09 |
| [ADR-008](adr-008-workflow-adapter-boundary.md) | Workflow Tool Adapter 接入协议在开源 V1；Engine/业务归业务层 | P11/P21 |
| [ADR-009](adr-009-capability-interface-and-center.md) | Capability 接口保留 Runtime，Center 归业务层 | P12 |
| [ADR-010](adr-010-trusted-untrusted-plugin-boundary.md) | Trusted / Untrusted Plugin 边界 | P10 |
| [ADR-011](adr-011-channel-adapter-contract.md) | Channel Adapter Contract（统一 IM Gateway）在开源 V1；具体 IM 通道为可插拔 Adapter | P13 |
| [ADR-012](adr-012-spec-model-single-source-of-truth.md) | Spec Model 单一真相源（typed model，不引入 `spec_json.get`） | P23 |
| [ADR-013](adr-013-durable-execution-vendor-pick.md) | Durable Workflow Engine Vendor Pick = DBOS | P11 |
