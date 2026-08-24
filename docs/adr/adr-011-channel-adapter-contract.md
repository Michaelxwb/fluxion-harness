# ADR-011: Channel Adapter Contract（统一 IM Gateway）在开源 V1；具体 IM 通道为可插拔 Adapter

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P13

## Context

Fluxion 是业务无关的开源 Agent Harness（见 Architecture Baseline §12），身份统一收敛到 `PlatformUser`（FEAT-12 / §8）：Web、Mattermost、企业微信等只是 identity source / channel，不是第二套用户。Agent Runtime 的 AgentLoop 完全不感知具体 IM 平台。

开源 V1 只实现 Web Chat 一个通道。后续业务接入时大概率新增飞书、QQ、企微等 IM 通道。如果每个通道各自实现对 `ChannelAPI` 的私有接入，验签、身份映射、消息回推会各写一套，换通道还要改接入层——"只写适配器就能加通道"缺少稳定的实现面。因此需要把 **通道接入协议** 固化为统一契约。

## Constraints

- Agent Runtime 与具体 IM 平台无关；新增通道不得修改 Runtime 与通道无关核心。
- 所有通道共享 `PlatformUser` 身份与 `/bind` 绑定流程（RULE-14）。
- 开源 V1 范围 = Agent + Console（业务无关）；具体 IM Adapter 复用度高、与业务无关，但不阻塞 V1。

## Options

1. 不定义契约，每个通道各自接入 `ChannelAPI`。
2. 只做设计说明，不落任务。
3. **定义 Channel Adapter Contract（统一 IM Gateway），Web Chat 作为首个实现；后续 IM 通道仅新增 Adapter。**

## Decision

**Option 3。** 开源 V1 定义并实现统一 Channel Adapter Contract：

```text
开源 V1（实现）
├── Channel Adapter Contract（FEAT-26 / S-C119）
│   ├── 入站事件规范化：验签/解密回调 → 统一消息结构（channel_type / channel_user_id / tenant_id / platform_user_id）
│   ├── 出站推送：channel 原生消息接口（Web 为 SSE，IM 为平台推送）
│   └── 身份映射钩子：channel identity → PlatformUser（未绑定走 /bind）
└── Web Chat 作为首个实现（TASK-103）

独立通道包（V1 不开发，按需补充）
└── 飞书 / QQ / 企微等具体 IM Adapter，仅实现契约即接入
```

Agent Runtime 与通道无关核心不做任何 IM 专属改动；切换/新增 Adapter 不修改 Runtime。

## Trade-offs

- 换取"新增 IM 通道 = 只写 Adapter"的稳定扩展面，代价是 V1 需要多定义一个契约并让 Web Chat 同时承担 reference implementation 的角色。
- 具体 IM Adapter（飞书/QQ/企微）V1 不实现，业务接入时按需补充；复用度高，后续可进入开源层作为独立通道包。

## Failure Modes

- 契约过粗 → Adapter 各自实现漂移；以 Web Chat + Stub IM Adapter 双实现（S-C119）约束契约收敛。
- 通道专属逻辑外溢回 Core → 用 RULE-22 禁止，通道能力只走 Adapter 实现。

## Validation

- S-C119（TASK-103）：Web Channel Adapter + Stub IM Adapter 共用同一契约进入 Runtime，切换 Adapter 不修改 Runtime 核心。
- 后续：新增飞书/QQ Adapter 时，仅新增通道包、不触碰 Runtime 与核心代码。

## Revisit Conditions

- 若出现跨通道大量共有且契约难以承载的能力（如消息中间件），重新评估是否提升为通道内核能力。
