# Tasks: channel-permessage-auth

- **Source**: backend/src/fluxion/api/channel.py S2 残留注释（L69-75）, backend/src/fluxion/services/channel_auth.py, backend/tests/channel/test_web_message_auth.py S-06
- **Created**: 2026-09-08
- **Updated**: 2026-09-08

## Proposal

`ChannelApplicationService.handle()` 今天信任调用方传入的 `channel_user_id`，防冒充全靠 API 层的 `/bind` content 门禁，没有结构性保证。S1 把保证做进签名：`handle()` 要求调用方传入 `VerifiedChannelIdentity | None`（匿名只能走未绑定流，已绑定执行必须持验证身份且与消息身份一致）。Web Bearer 逐消息校验已存在（S-06 已绿），本次不动；真实 IM Adapter 随 D1 另排期，本任务只把 IM 验证接口（verified 入口）准备好。

### Alignment

- **Scope**: 纳入：`handle()` 签名收口 + API 两处接线 + 存量直调测试迁移 + S2 残留注释消除。排除：真实 IM Adapter/路由（D1 另排期）、WeCom/Mattermost 入口接线（无路由可接）。
- **Decisions**:
  - D-S1-1：`verified=None`（匿名）永远只能走未绑定流——即使该 channel_user_id 已绑定，也不执行，只给 bind 提示/兑换。这是 impersonation 的结构性死刑。
  - D-S1-2：`verified` 与消息 `channel_user_id` 不一致 → `ChannelAuthError`；一致则按验证身份执行（bound 映射以 resolve 为准，verified 只做门禁不替代映射）。
  - D-S1-3：API 层匿名分支保留 `/bind` 门禁（S-06 锚定的 401 语义不变）， defense in depth。
- **Non-goals**: 真实 IM 通道接入；S-06 已有行为变更。
- **Acceptance**: S-06 双测试保持绿；新场景 AUTH-01/02/03 全绿。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| AUTH-01 | S2 注释 | integration | service.handle + PG 真库 | TASK-001 | verified |
| AUTH-02 | S2 注释 | integration | service.handle + PG 真库 | TASK-001 | verified |
| AUTH-03 | S2 注释 | integration | API + PG 真库（S-06 同链路） | TASK-001 | verified |

---

## TASK-001: handle() 要求已验证身份

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: backend/src/fluxion/api/channel.py#S2 残留注释(L69-75), backend/src/fluxion/services/channel_auth.py#WebBearerAuthenticator/VerifiedChannelIdentity(L35-82)
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: AUTH-01, AUTH-02, AUTH-03

### Description

`handle(adapter, external)` 改为 `handle(adapter, external, *, verified)`（必填，无默认值）。语义：None→只走未绑定流（bind 兑换/提示，永不执行）；verified 与消息身份不一致→ChannelAuthError；一致→按现有 resolve 映射执行。API 层：匿名分支传 None；Bearer 分支先 verify 后传 verified（已被 verify 的 token 链路保持行为）。存量直调 `handle()` 的测试全部显式传 verified（stub-im 场景手工构造 VerifiedChannelIdentity，文档化信任关系）。完成後删除 S2 残留注释。

### Checklist
- [x] `handle()` 签名收口（verified 必填关键字）
- [x] 匿名 + 已绑定 channel_user_id → 未绑定流（永不执行）
- [x] verified 与消息身份不一致 → ChannelAuthError
- [x] API 两处接线（messages + stream 匿名分支传 None；Bearer 分支保持 token 直解——已逐消息验证，无 channel_user_id 信任）
- [x] 存量直调测试迁移（显式 verified；`tests/channel_helpers.verified_identity`）
- [x] 删除 S2 残留注释（收口完成证据，api/channel + middleware + channel_auth 三处同步）
- [x] [AUTH-01][integration] verified=None + 已绑定 id → 未绑定提示且 Runtime 零调用，先写测试记 RED
- [x] [AUTH-02][integration] verified 身份与消息不一致 → ChannelAuthError，先写测试记 RED
- [x] [AUTH-03][integration] S-06 双测试保持绿（API 链路冒充仍 401）
- [x] verifier RULE-fluxion-console-001（manual）：未验证身份永不映射 PlatformUser（见 AUTH-01/02）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| AUTH-01 | integration | ChannelService、PG 真库、RecordingRuntime | 零调用 + 未绑定提示 | backend/tests/channel/test_verified_identity.py::test_AUTH01_* | .venv/bin/python -m pytest backend/tests/channel/test_verified_identity.py | verified |
| AUTH-02 | integration | ChannelService、PG 真库 | ChannelAuthError | 同上 test_AUTH02_*（2 用例：拒绝 + 匹配放行） | 同上 | verified |
| AUTH-03 | integration | API、PG 真库 | S-06 双绿 | backend/tests/channel/test_web_message_auth.py | .venv/bin/python -m pytest backend/tests/channel/test_web_message_auth.py | verified |

### Acceptance Evidence

- RED：实现前 3 failed（`handle() got an unexpected keyword argument 'verified'`）
- GREEN：3 passed；回归 channel 31 + api 36（合计 67 passed）；bind/adapter/identity 全绿
- 真实边界证据：PG 真库 + StubImChannelAdapter + RecordingRuntime 零调用断言 + ChannelAuthError
- mypy/ruff clean（仅剩 api/channel.py 2 处 pre-existing RUF059）
- 迁移说明：脚本批量迁移 30 处直调点时曾误删逗号/重复插入，已逐文件 diff 复核修复；benchmark 单行调用 verified 落括号外（元组表达式瑕疵）已修正
- 范围外：真实 IM Adapter 接线（D1 另排期）；WeCom/Mattermost authenticator 已存在，接线随 adapter 落地
- session 投影：`.code-flow/specs/_session/task-channel-permessage-auth-TASK-001.md`

### Log
- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)
