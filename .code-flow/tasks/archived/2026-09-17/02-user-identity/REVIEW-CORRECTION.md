# 02-user-identity 核验与修正记录

**核验日期**: 2026-09-20
**核验人**: Claude Code
**范围**: 设计（backend + frontend）→ 5 个 TASK 的拆分，以及 `apps/console-platform/backend`、`frontend/src/modules/user-identity`、`tests/`、`e2e/` 的实现

---

## 一、结论摘要

| 类别 | 数量 | 处理 |
|---|---|---|
| 设计→任务拆分缺陷 | 6 | 设计文档已回改；公共组件已补齐 |
| 实现功能缺陷 | 13 | 已修复并补测试 |
| E2E 设施缺陷 | 3 | 已修复（自带端口 + 构建产物 + 数据清理） |
| 验收证据不实 | 4 站不住 / 7 位置错 | 测试重写 + 证据表按实际断言位置重写 |
| 无主测试数据 | 192 用户 / 43 级联行 + 8 用户 / 2 级联行 | 已全部清理（§6.1、§6.2）；default 租户仅剩真实用户 |
| 需决策项 | 5 | 已全部处理：memory 归属→记录为决定；统一封套→撤回误判；E-07 缺口→降级为 contract 并记录（见第五节） |

修正后验证：

- `cf_acceptance_runner --include-e2e`：**16/16 场景 pass**（run_id `c7754bc15ddf43a3aec27a917521d4dd`），manifest 与任务书无 drift
- 后端 `uv run pytest -q --ignore=tests/acceptance/runtime`：**852 passed**
- 前端 `tests/frontend`：**99 passed**；typecheck / build / i18n checker / api-usage checker 全过
- spec verifier：`harness-api` / `harness-data` / `harness-i18n` / `harness-im` / `harness-auth` / `harness-rel` / `harness-time` / `harness-ui` / `harness-ui-detail` / `harness-frontend` **全部通过**；仅 `harness-test#RULE-test-001` 因 08 在途重构（见 §6.3）无法通过

---

## 二、设计 → 任务拆分缺陷（已回改设计文档）

| ID | 问题 | 修正 |
|---|---|---|
| A1 | 两份 design 的 Spec Compliance Matrix 全部引用不存在的 spec owner `harness-platform`（backend 5 条、frontend 7 条） | 改为实际生效的 spec id（harness-api / harness-data / harness-i18n / harness-im / harness-test / harness-auth / harness-rel / harness-frontend / harness-ui / harness-ui-detail / harness-time），并注明原写法 |
| A2 | 前端 design §3.3「必须复用」的 14 个公共组件中，`ErrorState` / `StatusTag` / `ConfirmAction` / `EntityLink` / `LocaleSwitch` 在 01 中**不存在**（`ConsoleShell`→`layout/AppLayout.tsx`，`DetailTabs` 不存在） | 在 `components/common/` 补齐 5 个组件 + 契约测试；`LanguageSwitcher` 更名为 `LocaleSwitch` 并对齐 design；design 清单更正为真实名称 |
| A3 | 任务拆分反转了 design 的所有权决策（§3.1 原写「复用 07/08 application service」），把 `GrantService`/`MemoryService` 落在 02 | 授权侧成功（07 `api/agents.py` 复用同一 `GrantService`）；记忆侧失败（08 有独立 `MemoryService`）。design §3.1 改为如实描述并标注「统一口径待 ADR」 |
| A4 | TASK-002 写「07/08 服务落地后切换为同一 application service 调用」，该切换从未发生 | 删除该承诺，改为由 TASK-001 直接保证口径一致 |
| A5 | API-11 声称「复用 08 `/internal/admin/users/{user_id}/memory` 同一 service」，该端点全仓库不存在 | 改为如实描述（02 直读 `runtime.user_memory`） |
| A6 | ~~「表已存在于迁移 0002」与本仓库实际不符~~ **本条为误判，已撤销** | 复核后确认 `migrations/versions/0002_initial_schema.py` 确实创建 `control.platform_user` 等表，alembic 单链（0001~0007）仍在用；原写法正确。已把误改的 TASK-001/TASK-002 Description 恢复为「已在迁移 `0002_initial_schema`」 |

---

## 三、实现功能缺陷（已修复 + 补测试）

| ID | 问题 | 修正 | 验证 |
|---|---|---|---|
| B1 | `ChannelService.bind`：外部身份已绑定到**其他**用户时，静默不更新却消费掉码并返回成功 | 返回 `IDENTITY_ALREADY_BOUND`(409) 且不消费码（保持 ACTIVE，解绑后可复用）；同一用户仍幂等 | `test_channel_bind_api.py::test_identity_bound_to_another_user_is_rejected` |
| B2 | 四个 Tab 清单不传分页 → 后端默认 20 条且 `pagination={false}`，与 Tab 标题计数矛盾 | 四个 Tab 分页消费 + `PaginationFooter` | typecheck/build + E2E |
| B3 | E-06 验收是伪造的（零故障注入、断言位置指向无关行） | 用真实 500 重写（播种 `content_json` 非对象的记忆 → 后端 `MemoryItem.content` 校验失败），断言 Tab 级 `ErrorState` 且其他 Tab 正常 | E-06 E2E pass（后端日志可见真实 ValidationError） |
| B4 | `user_memory` 双实现，且 Console 列表/计数含 `enabled=false` 而运行时不读 | 属「管理面全量 / 运行面生效子集」的有意差异 → design API-11 补口径说明；双实现待 ADR | 文档 |
| B5 | `has_active_grant` 未含 `AgentDefinition.enabled`，违反 `harness-auth#RULE-auth-001`；IM resolve 的 `authorized` 因此对禁用 Agent 返回 true | 加 `enabled` 判定 | `test_channel_resolve_api.py::test_disabled_agent_is_unauthorized` |
| B6 | `agent_grant_count` 不 JOIN `agent_definition`，删除 Agent 后「授权数」不归零（与 Tab 列表 0 行矛盾） | 计数 JOIN `agent_definition`（is_deleted=false + tenant），与 API-05 同口径 | `test_users_api.py::test_agent_grant_count_stays_consistent_with_agent_tab_when_agent_deleted` |
| B7 | API-04 设计要求的 `SELECT ... FOR UPDATE` 未实现 | 新增 `PlatformUserRepository.get_for_update` 并用于 `update_user` | `test_users_api.py::test_update_user_path_takes_row_lock` |
| B8 | API-07「无 ACTIVE grant → `COMMON_NOT_FOUND`」未实现（静默 200），且测试走错分支 | 改为 404 `COMMON_NOT_FOUND`；补真实分支用例 | `test_user_side_relations.py::test_s04_unknown_agent_or_user_returns_not_found` |
| B9 | API-06 缺 Agent 时抛 `AGENT_NOT_FOUND` 而设计写 `COMMON_NOT_FOUND`；测试用 `# v1.1 契约` 为未回改的设计背书 | 设计改为 `AGENT_NOT_FOUND`；删除误导注释 | 文档 + 测试 |
| B10a | API-02/04 响应带了设计明确「不含」的计数字段 | 新增 `UserBasic` DTO（不含计数），create/update 使用 | `test_users_api.py::test_create_and_update_responses_exclude_aggregate_counts` |
| B10b | 「4 个 COUNT 合并查询」实为 4 次串行往返 | 合并为单条 UNION ALL | 计数用例全绿 |
| B10c | `_record_audit` 的 `user` 形参完全未使用 | 删除死参数 | — |
| B11 | 前端：列表无错误态/重试；§3.3.1 的搜索/重置按钮缺失且逐字符发请求无竞态保护；单条删记忆复用清空文案；`openDetail` 吞异常；`UserFormModal` 仅处理冲突错误；Agent 下拉静默截断 | 逐项修复（ErrorState+重试、搜索/重置+草稿态+请求序号、`user.memory.confirmDelete`、catch+Toast、非冲突错误 Toast、截断提示） | typecheck/build + frontend 契约测试 |
| B12 | E2E 三条用例建 `e2e-*` 用户后不清理（重演 RULE-test-001 Convention 记录的 05 事故） | 全部用例 `finally` 清理；`seed_user_identity.py --cleanup` 扩展为删除 `bind_code` + `platform_user` | 运行后无新增 `e2e-*` 残留 |
| B13 | E-05/E-07 标为 integration，实为源码子串 grep | E-05 改为真实 404 的 E2E；E-07 降为 contract 并删除「Toast 引导重新生成」的虚假声明 | E-05 E2E pass |

---

## 四、E2E 设施修正

| ID | 问题 | 修正 |
|---|---|---|
| C1 | 验收跑 Vite **dev server**（场景命令先 `npm run build` 的产物被丢弃），且 `reuseExistingServer: true` 会静默复用手工启动、配置不同的服务 | 改为 `vite preview` 跑构建产物；本模块**自带端口**（后端 8001 / 预览 4174）并 `reuseExistingServer: false`，与其它域共用的 8000 dev server 解耦；`vite.config.ts` 增加 `preview.proxy` 与 `MUAD_API_TARGET` |
| C2 | 见 B12 | 同 B12 |
| C3 | — | 见 B13 |

> 遗留：其余域的 playwright config（agent/model-management/platform/skill/mcp）仍是 dev server + `reuseExistingServer: true`，同样与 RULE-test-001 Convention 冲突。本次只修了 02 自己那份，跨域统一需单独决策。

---

## 五、本轮未修复（需决策或跨模块）

1. **`runtime.user_memory` 双实现（B4 / A3）—— 已作为决定记录，不再挂起**：仓库里**没有 ADR 目录、也没有任何 ADR 引用**（早期形态已被清除），故决定记录在 02 后端设计 §3.1 决策表：**02 拥有管理面 CRUD**（list/delete/clear，全量含 `enabled=false`）、**08 拥有运行面读写**（只读 `enabled=true`）；不引入 internal API（管理面读路径不应耦合 runtime 可用性）。若将来要求进程级隔离再单独立项。
2. ~~**异常未走统一封套**：`MemoryItem(**item)` 校验失败时抛出的是无封套的裸 500~~ **本条为误判，已撤回（2026-09-20 复测）**：实测 `muad_api.handlers.install_exception_handlers` **已经**注册了 `@app.exception_handler(Exception)`，意外异常返回的是标准封套 `{code: COMMON_INTERNAL_ERROR, msg: 系统内部错误, ...}` + HTTP 500。当时把服务端日志里的 traceback（Starlette 在回完响应后重新抛出以便记录）误读成了响应体。无实现缺陷。
3. **E-07 的「过期引导」能力缺失**：Console 无法感知 IM 内消费，且只有 POST 生成（DB 只存 hash 无法回显），需新增「查询当前 ACTIVE 绑定码状态」端点。本轮把场景降到 contract 层并记录缺口。

---

## 六、附：清理的无头数据与遗留

### 6.1 已清理（2026-09-20，经确认）

default 租户下 `user_code LIKE 'e2e-%'` 的历史垃圾用户：

| 表 | 删除行数 |
|---|---|
| `control.platform_user` | 192 |
| `control.bind_code` | 25 |
| `control.mcp_user_grant` | 14 |
| `control.skill_user_grant` | 2 |
| `control.user_credential_ref` | 2 |
| 合计 | 235 |

删除前已枚举全部 6 个引用 `platform_user` 的外键（`agent_access_grant` / `bind_code` / `channel_identity` / `mcp_user_grant` / `skill_user_grant` / `user_credential_ref`），按依赖顺序在同一事务内删除。default 租户用户数 201 → 9。

### 6.2 已清理（2026-09-20，第二批，经确认）

default 租户下 8 行同类 E2E 残留（`user_code` 不匹配 `e2e-%`，故未被 §6.1 覆盖）：

| user_code | display_name | 创建时间 | 来源 |
|---|---|---|---|
| `E2E User` | `e2e-1789733150539` | 2026-09-18 12:05 | 02 E2E（历史版本把 `user_code`/`display_name` 写反） |
| `Bind Code User` | `e2e-1789733157868` | 2026-09-18 12:05 | 同上 |
| `Detail Counts User` | `e2e-1789733164700` | 2026-09-18 12:06 | 同上 |
| `Conflict User` | `e2e-1789733171516` | 2026-09-18 12:06 | 同上 |
| `bind-e2e-b72eb836` | `Gateway Bind User` | 2026-09-18 12:19 | 02 S-02 E2E（早于清理逻辑落地的那次运行） |
| `debug-user` / `debug-user2` / `shot-user` | `Platform E2E User` | 2026-09-18 ~ 09-19 | 04 project-platform E2E |

删除 8 个 `platform_user` + 2 条级联行（`bind_code` 1、`channel_identity` 1）。清理后 default 租户仅剩真实用户 `lixue / 李雪`。

- 字段写反的 4 行是 **2026-09-18 的历史产物**，与当前代码无关：当前 `UserFormModal` 的 `field`/`label` 映射正确，本次 E2E 的 `user-link-${userCode}` 断言能通过即证明 `user_code` 填入正确。
- S-02 的 E2E（`tests/e2e/test_gateway_bind_e2e.py:212`）已有清理逻辑，本次运行无新增遗留。

### 6.3 跨模块遗留（不在 02 范围）

- `tests/acceptance/runtime/`：`@pytest.mark.asyncio` 错位导致 3 个文件语法错误（已修，共 6 处）；但该目录的 async 用例仍全部失败/报错（`coroutine ... was never awaited`），属 **08-runtime-execution 的未提交在途重构**（`conftest.py` 被删、fixture 内联），未进一步改动。连带使 `pytest -q tests/acceptance` 失败，并因残留数据污染 `tests/acceptance/test_skill_schema_constraints.py`（单独跑 4 passed）。
- 其余域的 playwright config（agent / model-management / platform / skill / mcp）仍是 dev server + `reuseExistingServer: true`，同样与 RULE-test-001 Convention 冲突，需跨域统一决策。
