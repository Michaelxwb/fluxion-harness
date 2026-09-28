---
id: harness-im
description: Agent Harness 通用平台规则：im
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-im-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/console_channel
    - tests/gateway
    cwd: .
    timeout: 600
---

# harness-im

## Rules

- [RULE-im-001] 一个逻辑 Agent 可绑定 0..N 个 IM 通道账号；每个 `bot_id` 只路由到一个 Agent；Bot 与 Runtime/Worker Pod 无任何绑定；Gateway 不保存 `agent_id→Pod` 映射。

## Conventions

入站、绑定的错误语义与幂等口径：

- **入站消息按 `message_id` 去重**：键 `im:dedupe:{channel}:{message_id}`、TTL 600s（`apps/im-gateway/src/muad_im_gateway/application/inbound.py:44-45,256-269`）；去重存储不可用时**降级为 at-least-once（fail-open）且不得阻断投递**——`NullDedupeStore` 一律返回「非重复」并只记告警（`apps/im-gateway/src/muad_im_gateway/infrastructure/dedupe.py:129-159`）。丢消息比重复消息更严重：宁可重复处理，不可因 Redis 抖动静默吞掉用户消息。
- **Bot 快照按固定节拍轮询并整批发布**：默认 30s（`application/bot_snapshot.py:15`）、分页上限 100（`:16-18`）；跨页 `revision`/`total` 不一致则**整批丢弃**、保留旧快照等下个节拍（`:63-67,90-91`）；拉取失败保留最近一次完整快照且不清空（`:56-62`）。
  - ✅ 失败保留上一份（`revision` 不变）；❌ 失败清空 → 全量 bot 短时不可路由
- **投递确认语义 `reserve` → 发送 → `mark`**：`reserve` 是 in-flight 占位（TTL 30s，短于送达标记），只有 `mark` 的送达标记才代表成功（TTL 604800s）；占位冲突最多等 2s，超时回可重试错误（`500`）而非假装成功；发送失败必须释放占位让 Worker 重试（`apps/im-gateway/src/muad_im_gateway/api/delivery.py:23-31,42-67,77-90,93-131`）。
  - ✅ 未送达 → 释放占位 + 报错；❌ 把「占位存在」当成「已送达」回报 `delivered=true`
- **`channel` 白名单仅 `WECOM`**：契约用 `Literal["WECOM"]` 钉死（`packages/contracts/src/muad_contracts/channel.py:23-29`），Gateway 侧适配器按 `name` 注册、重名直接抛 `DuplicateChannelAdapterError`（`apps/im-gateway/src/muad_im_gateway/channels/probe.py:22-23`、`channels/base.py:65-79`）。
  - ❌ 只加新通道实现而不改契约 Literal（请求在入口就被拒，或绕过校验后无处路由）
- **绑定码一次性 + 绑定请求幂等**：绑定码状态 `USED`/`REVOKED` → `BIND_CODE_INVALID`，`EXPIRED`/已过期 → `BIND_CODE_EXPIRED`（`application/channel_service.py:219-227`，Console 侧）；带 `Idempotency-Key` 时用共享幂等表 + `pg_advisory_xact_lock` 串行化，同 key 异指纹 → `IDEMPOTENCY_MISMATCH`（`:135-159,171-172`）；`identity_key = (channel, bot_id, external_user_id)` 每租户唯一（`infrastructure/models/channel.py:60-66`），该身份已属于其他平台用户时抛 `IDENTITY_ALREADY_BOUND` 且**不消费绑定码**（`:250-252`）。
- **`bot_id` 是跨租户全局唯一**：`uq_bot_account_bot_id` 只含 `bot_id`、**不含 `tenant_id`**（`migrations/versions/0002_initial_schema.py:379-386`、`infrastructure/models/channel.py:19-25`），冲突统一报 `BOT_ID_EXISTS`（`application/channel_admin_service.py:117,164`）。因此 A 租户已占用的 `bot_id` 会让 B 租户创建失败——这是**有意的全局唯一**（一个物理 bot 只能路由到一个 Agent，避免跨租户抢占同一 bot）。
- **解析顺序固定，错误码不得泄露跨租户存在性**：先向 Console `resolve`（未知/禁用/已删 bot → `BOT_NOT_FOUND`；跨租户 bot 同样按 `BOT_NOT_FOUND`，不泄露其存在性），再依次判未绑定 → `UNBOUND`、未授权 → `NO_PERMISSION`（`apps/console-platform/backend/src/muad_console_platform/application/channel_service.py:94-126`；`apps/im-gateway/src/muad_im_gateway/application/inbound.py:414-428`）。
  - 机检：`tests/console_channel/test_channel_resolve_api.py:210-220`
- **幂等键沿链路透传**：Gateway 以原通道 `message_id` 作为 `Idempotency-Key` 传给 Console bind 与 Runtime run/conversation，使可重试提交不重复建 Run（`apps/im-gateway/src/muad_im_gateway/application/runtime_client.py:98-101`、`application/console_client.py:198-200`）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
