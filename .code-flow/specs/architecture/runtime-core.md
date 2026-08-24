---
id: fluxion-runtime-core
description: Fluxion 无状态 Runtime、Microkernel、ExecutionSnapshot、Plugin/Hook 核心约束
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-fluxion-runtime-001
    type: manual
    config:
      checklist: 检查 Runtime 无状态、Snapshot 固定版本、Kernel 依赖方向和 Hook/Plugin Contract。
      owner: project-owner
---

# Fluxion Runtime 核心规范

## Rules

- [RULE-fluxion-runtime-001] Runtime 必须保持无状态；一次 Execution 必须使用固定 ExecutionSnapshot；Kernel 只能依赖稳定 Contract，不允许依赖具体 Plugin/Provider。

## Guidance

- RuntimeProfile 是逻辑 Resource，不是 Pod。
- Runtime Pod 可以随时销毁和替换，不能丢失 Agent/User/Workflow 事实状态。
- Session、Memory、Credential、Workflow durable state 全部外置。
- Execution 开始时解析精确 Agent/Skill/MCP/Plugin/Policy 版本并生成 Snapshot。
- 当前 Execution 不得因配置热更新切换版本；新 Execution 使用最新 Published Version。
- Plugin/Hook 必须类型化并有 timeout、fail policy、scope、priority。
- 不可信 Plugin 不能默认 in-process。
- Runtime 不允许通过 Console API 读取事实配置，必须通过 Registry Contract。

## 统一语义

- Agent = 实际部署/运行的 Agent Runtime Service/Pod。
- RuntimeProfile = Console 创建/发布的运行态配置。
- 所有 Runtime Pod 对同一 RuntimeProfile/User/Tenant 读取相同事实源。
