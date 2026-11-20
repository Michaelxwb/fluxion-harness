---
design_template: full
---

# 平台登录态逐平台返回 设计

> **文档编号**: MOD-PLATFORM-SESSION-REPORT-1.4
> **文档版本**: v1.4
> **创建日期**: 2026-11-19
> **修订日期**: 2026-11-20
> **文档状态**: 设计评审中

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 需求来源 | 用户问“可以列一下平台 session”，只收到一条认证失败和运行终止信息；追问“都失败了吗”仍不知道各平台结果 |
| 本版目标 | 第一次回复就列清本次查询范围内各平台的结果及失败原因；Skill 保持现有调用和失败终止行为 |
| 涉及模块 | platform-sdk（`list-platforms` / `get-state` / broker 报告）；agent-core（执行器与错误包装）；agent-runtime / agent-worker（工具说明、失败出口）；技能模板与作者指南 |
| 关联文档 | [平台 Spec](../../../specs/platform/harness-project-platform.md)、[密钥 Spec](../../../specs/secret/harness-secret.md)、[测试 Spec](../../../specs/test/harness-test.md)、[既有会话设计](../../../../docs/designs/2026-11-17-unified-platform-session.md)、[人工验收场景](platform-session-reporting.scenarios.md) |

### 1.1 修订历史

| 版本 | 日期 | 修订内容 |
|---|---|---|
| v0.1 | 2026-11-19 | CLI 逐平台报告草稿 |
| v1.1 | 2026-11-19 | 扩展为执行级报告、并发获取和多入口结果改造 |
| v1.2 | 2026-11-20 | 收窄为状态查询与失败告知；保留 Skill 原有执行行为 |
| v1.3 | 2026-11-20 | 直接增强 `list-platforms`，不新增查询命令；覆盖原显式调用的预登录失败 |
| v1.4 | 2026-11-20 | 按最优最小设计收敛：一个执行级“最后报告”对象 + 有界摘要进现有证据/错误载荷；不引入账本、审计字段、迁移、并发或新命令 |

> **本版核心结论**：需求有两条路——直查走增强后的 `list-platforms`；Skill 失败走现有错误出口。两者共用一次顺序采集；报告只保留“本批次最后一份”，由 broker 环境暴露、executor 附加到现有错误/证据，不建账本。

## 2. 需求分析

### 2.1 需求概述

直接查询 session 时，继续使用 `session-manager list-platforms`。增强后它按本次范围逐平台复用或获取登录态，第一次回复就说明哪些平台可用、哪些获取失败及原因；不再把“有凭据配置”当作登录态可用。

Skill 继续按现有方式调用 `session-manager get-state` 获取登录态、执行自身业务逻辑。获取失败仍走现有异常或非零退出路径；本次只补诊断，不新增 Skill 执行门禁，也不让缺少依赖的 Skill 成功继续。

“可用”指按现有会话机制复用或获取后可供使用的登录态，不代表业务接口查询成功。查询不强制刷新，也不新增业务健康检查。

### 2.2 当前代码与问题

| 位置 | 当前事实 | 本次处理 |
|---|---|---|
| `skill_access.start()` | 现有预登录先于脚本启动；首个认证失败即抛错，成功结果没有随异常返回 | 按请求集顺序采集，报告随异常返回；失败出口不变 |
| `skill_bridge.skill_platform_environment()` | 先调用 `start()`，成功后才启动 broker 并交给 workspace 执行命令；显式平台查询可能在 `list-platforms` 运行前失败 | 覆盖该原路径；预登录失败时报告进入现有错误载荷/回复，不能只改 CLI 或提示词 |
| `skill_bridge._write_state()` | 状态获取首错即停，全部获取成功后才写文件 | 按请求集采集；失败仍不交付成功状态文件，报告随错误返回 |
| `skill_tools._workspace_shell_with_platforms()` | 平台异常被压成 `platform session unavailable`，平台身份和原因丢失 | 透传脱敏报告；不改变 workspace 执行协议 |
| `agent/runner._track_failure()` | 脚本非零退出且非可重试时终止，不再调用模型 | 保持终止规则；失败收尾读取本次工具错误里的有界摘要 |
| `skill_access._discover_keys()` | 只保留 `available` 的目录项，缺凭据平台被跳过 | `list-platforms` 从可见目录取范围；`start()/_write_state()` 用请求集，不用该过滤结果 |
| Console `list_platforms()` | `available` 只表示无需凭据或能选到凭据；没有执行平台认证 | Console 目录继续提供候选范围；CLI `list-platforms` 在此基础上逐平台获取，返回实际结果 |

用户贴出的失败回复只能证明发生过认证失败，不能证明其他平台全部失败，也不能推断具体哪个平台已经成功。

### 2.3 功能方案

| 功能 ID | 内容 |
|---|---|
| FEAT-01 | 本次请求的每个平台均有结果：可用、获取失败及原因，或明确未检查 |
| FEAT-02 | 查询返回完整报告，单个平台认证失败不使整个查询提前结束 |
| FEAT-03 | 平台 key、错误码和已脱敏的响应原因保留到用户回复 |
| FEAT-04 | Skill 及既有 `get-state`、`refresh` 的执行和失败终止语义保持 |
| FEAT-05 | Skill 因登录态失败终止时，现有失败回复展示本次依赖范围内的报告 |
| FEAT-06 | 模型可见说明与 CLI 契约同步：列 session 用 `list-platforms`；Skill 直接用 `get-state`，不要先 list |
| FEAT-07 | 一个执行级“最后报告”对象：`start()` / `list-platforms` / `_write_state()` 共用；不建账本、不持久化、不跨调用 |

### 2.4 范围与边界

| 类别 | 内容 |
|---|---|
| 范围 | 一次查询/一次获取批次的报告；`list-platforms` 的实际检查；一个执行级最后报告；有界摘要进入现有错误/证据；`execute` 说明、`--help` 与作者指南；相关测试和人工场景 |
| 保持 | Skill 脚本调用、失败退出、同步/异步执行方式；身份和授权；缓存键、有效期、复用、续期、刷新、失效；成功状态文件形状、权限及清理；`get-state` / `refresh` 的退出与错误语义 |
| 不在本次范围 | 新增 CLI 命令或参数；执行级账本/失败缓存；新增审计字段或数据库迁移；强制 artifact 外置；并发登录；新增 Skill 前置门禁；无会话适配器完整支持；任务状态机和调度改造 |
| 取舍 | `list-platforms` 从纯目录变为实际登录态查询，会写成功会话、续期并消耗出网预算；顺序检查，不承诺性能优化；`version: 2` 保持，文档说明 `available` 新语义 |

### 2.5 验收条件

| ID | 约束 | 场景 |
|---|---|---|
| RULE-01 | 查询取用户指定范围或当前身份可见目录；Skill 诊断只取本次声明/请求的依赖平台，其他平台不影响它 | S-03 / B-01 |
| RULE-02 | 每个请求平台都有结果；认证失败后继续采集其他请求平台，按请求顺序返回 | S-01 / S-02 / E-01 |
| RULE-03 | `list-platforms` 检查完成后把各平台失败作为数据，即使全部失败也成功退出并返回报告；若显式执行在原有预登录阶段失败，仍沿原规则终止并在本次回复列清全部结果，不声称 CLI 已执行 | S-02 / E-01 / B-05 |
| RULE-04 | 原本失败的 `get-state` 仍非零退出；Skill 沿用现有失败处理，不将部分结果交付为完整登录态 | S-04 / E-03 |
| RULE-05 | 查询第一次回复和 Skill 本次失败回复都能读到对应平台结果，不依赖用户追问或再次执行 | S-02 / S-04 / B-05 |
| RULE-06 | 失败保留平台 key、错误码和可取得的脱敏原因；缺凭据、认证拒绝与基础设施故障不得互相冒充 | E-02 / B-04 |
| RULE-07 | 缓存、刷新、续期、授权和状态文件生命周期沿用原有机制 | B-01 / B-02 / E-03 |
| RULE-08 | 报告、对话、错误载荷与日志不得出现 Cookie、CSRF、AK/SK 原值 | S-02 / S-04 |
| RULE-09 | 全局依赖故障、预算或取消使检查未完成时，保留已知结果，剩余项明确未检查，不推断其登录成败 | B-04 |
| RULE-10 | 一个执行只保留“本批次最后报告”一个对象；不建历史、不落库、不跨执行共享；报告只投影安全字段 | S-04 / B-05 |
| RULE-11 | 模型可见说明必须与 CLI 契约同步：`execute` 描述与 `list-platforms --help` 写明“列 session 用 list；Skill 直接用 get-state；不要先 list” | S-03 / B-03 |
| RULE-12 | `start()` / `_write_state()` 使用请求集，不使用 `allowed_keys()` 的过滤结果；`allowed_keys()` 只服务授权闸门；`list-platforms` 使用可见目录 | E-02 / S-04 |
| RULE-13 | 失败报告进入现有通道：结构化 `platform_sessions` 随工具错误载荷；同一份报告压成有界摘要放在错误信息/`stderr` 前部，复用现有 `evidence_preview` 与失败收尾，不新增审计字段 | S-04 / B-05 / E-03 |
| RULE-14 | `SessionMode.NONE` / `REQUEST_SIGNING` 平台在 `list-platforms` 中标 `available=null` + `reason`，不报成认证失败；`get-state` 不因此扩展适配器能力 | B-04 |

## 3. 技术设计

### 3.1 方案选型

一次采集生成一份报告。直接增强现有 `list-platforms`，逐平台复用或获取登录态，返回可用性与失败原因；`get-state`、`refresh` 继续服务 Skill，不增加第二个查询命令。

不把 `get-state` 部分失败改为成功退出：旧 Skill 依赖返回码、错误和状态文件。也不调整 Runner 失败阈值或增加模型补答回合；Skill 的真实失败由现有收尾告知用户。

| 决策点 | 选择 | 被否决项 | 理由 | 可逆性 |
|---|---|---|---|---|
| 查询入口 | 增强 `list-platforms` | 新增 `status` 命令 | 模型已经知道 `list-platforms`；少一套入口 | 易 |
| 报告传递 | 最后报告对象 + 结构化错误载荷 + 有界摘要进现有证据 | 执行级账本、审计字段、强制外置 | 两条路都覆盖，零迁移、零新存储 | 易 |
| 失败语义 | `get-state` 仍非零退出；Skill 停止 | 部分成功退出 | 旧 Skill 依赖返回码；不交付不完整状态 | 易 |
| 并发 | 顺序 | 并发 | 当前平台数个位数；先保证行为正确，性能另议 | 易 |
| 模型说明 | `execute` 描述 + `--help` + 作者指南 | 新增系统提示段 | 工具说明是常驻能力入口；不重复占上下文 | 易 |
| `available` 语义 | CLI 返回实际可用性；Console 内部目录不变 | 改 Console 目录语义 | 查询是执行级事实；目录仍是候选范围 | 易 |
| `version` | 保持 2，新增字段与语义由文档/测试锁定 | 升到 3 | CLI 是内部入口；升版会扩散到模板和存量脚本 | 易 |

### 3.2 调用路径

**直接查询**：使用现有 `execute target=workspace` 运行 `session-manager list-platforms`。AUTO 取当前身份可见目录；指定范围使用现有 `execute platforms`（受信范围）；不新增 CLI 参数。

`list-platforms` 通过 broker 逐平台检查；登录请求仍绑定现有出网预算与审计 transport，复用 `_workspace_platform_scope` 的绑定能力，不因查询跳过预算。能产出报告即成功退出；单个/全部平台失败、预算/取消后的 `available=null` 都是报告条目。

**原显式查询的前置失败**：`execute platforms` 会使现有 `start()` 在 shell 命令之前预登录。此处按请求范围采集完诊断后，沿现有失败出口返回报告；失败收尾/模型回复如实说明命令未执行。不能只引导模型改走 AUTO，或只改 `list-platforms` 的 CLI 分支来宣称修复了原问题。

**Skill 执行**：继续走既有执行器、预登录和脚本调用链。现有预登录或脚本内 `get-state` 失败时，采集本次请求的结果后沿现有出口失败。正常执行成功结果不增加报告字段；不新增独立的执行检查阶段。

**失败路径（必须区分）**：

| 路径 | 谁产生第一次回复 | 报告通道 |
|---|---|---|
| 直查 `list-platforms` 完成 | 模型读 stdout | 命令 stdout；无失败收尾 |
| 显式 `platforms` 预登录失败 | 模型读工具错误；若运行终止则失败收尾 | 工具错误载荷 `platform_sessions` + 有界摘要 |
| Skill 预登录失败 | 模型读工具错误（`execute_skill` 错误载荷） | 同上；不依赖 finalization |
| Skill 脚本内 `get-state` 失败 | 现有失败收尾（非可重试） | executor 附加最后报告 + 有界摘要进 `stderr`/证据 |

共用一个顺序采集函数：按请求顺序调用现有解析和 `acquire`，保存每项结果。成功状态仅供本次文件生成；报告只投影安全字段。用后释放，不建立历史、失败缓存或跨进程存储。

### 3.3 数据设计

**`list-platforms` 输出**（保留 `version`、`key`、`name`、`authentication`；`available` 现在表示实际可用性）：

```json
{
  "version": 2,
  "platforms": [
    {"key": "mssp", "name": "mssp", "authentication": "session",
     "available": true, "expiresAt": "2026-11-20T10:00:00Z"},
    {"key": "mssw", "name": "mssw", "authentication": "session", "available": false,
     "error": {"code": "PLATFORM_AUTH_REJECTED",
               "detail": {"http_status": 401, "message": "平台返回的已脱敏原因"}}}
  ]
}
```

- `available=true` 只用于按现有机制确认可用的平台；
- `available=false` 表示本次获取失败，`error.code` + 可回传的 `detail`；
- `available=null` 表示尚未检查，`reason` 说明原因（预算、取消、全局故障、无会话适配器）；
- 空目录返回空列表，回复说明没有可查询的平台；
- 不写状态文件；不新增 Redis 键；不新增审计字段。

**`get-state` 失败载荷**（stderr；原错误码、detail、retryable 保持）：

```json
{
  "error": {"code": "PLATFORM_AUTH_REJECTED",
            "detail": {"http_status": 401, "message": "平台返回的已脱敏原因"}},
  "platform_sessions": {
    "platforms": [
      {"key": "mssp", "available": true, "expiresAt": "2026-11-20T10:00:00Z"},
      {"key": "mssw", "available": false,
       "error": {"code": "PLATFORM_AUTH_REJECTED",
                 "detail": {"http_status": 401, "message": "平台返回的已脱敏原因"}}}
    ]
  }
}
```

**有界摘要**（executor 在前置失败/脚本失败时放入错误信息或 `stderr` 前部）：

```text
platform_sessions: mssp=ok; mssw=PLATFORM_AUTH_REJECTED(http=401) 平台返回的已脱敏原因
```

- 摘要按请求顺序；单行、总量有界；超出预算时优先保留失败项，但保留项内部仍按请求顺序；
- 复用现有 `evidence_preview`（1 KiB）和失败收尾的预览预算（当前 300 字）；2 个平台的摘要远小于预算；
- 结构化 `platform_sessions` 进工具错误载荷，模型第一次可读；摘要是失败收尾/证据的可读投影；
- 两者都不含 Cookie、storageState、requestProfile 或凭据。

**最后报告对象**：`PlatformSessionReport` 存在 broker 内存中，按请求顺序保存每个平台最近一次 `ok/error/not_checked`；`start()`、`list-platforms`、`_write_state()` 都更新同一个对象。它没有历史、没有持久化、没有跨执行共享；broker 结束即释放。

### 3.4 接口设计

| 命令 | 结果与退出语义 |
|---|---|
| `session-manager list-platforms` | 按本次查询范围逐平台检查登录态，stdout 返回实际 `available`、有效期或失败原因；能产出报告即成功退出（含单/全部平台失败、预算/取消后的 `available=null`）；目录/身份无法取得等不能产出报告的故障仍非零 |
| `session-manager get-state --platform <key>` | 保持成功状态文件与 stdout 字段；失败仍 stderr + 非零退出，补 `platform_sessions` |
| `session-manager get-state --skill-name <name>` | 保持名称校验和原有交付语义；获取失败补 `platform_sessions` |
| `session-manager refresh --platform <key>` | 保持原有刷新及失败语义，不做批量查询 |

- 不新增无参数 `get-state`、不新增 `status` 命令、不新增 CLI 参数、不改变既有命令版本字段。
- 参数/身份非法、无法取得目录等不能完成查询的故障仍明确报错；已有结果随诊断保留，未完成项 `available=null` + `reason`。
- 例如 MSSW 的 AK/SK 填错：增强后的 `list-platforms` 返回 `available=false` 和平台认证失败原因，不再把“有凭据配置”当作登录态可用。

**模型可见说明**（`execute` 工具描述、`list-platforms --help`、作者指南同文）：

```text
The execution always provides session-manager. Use `session-manager list-platforms`
when the user asks to list platform sessions; it checks actual login state and
returns per-platform availability. For a Skill, call `session-manager get-state`
directly; do not run list first. For one platform use `get-state --platform <key>`;
use `refresh --platform <key>` only for an explicit refresh. Read the per-platform
result before answering. `platforms` narrows the allowed set and is fail-closed:
pass only keys from list-platforms or the skill declaration. Do not output or
persist credentials.
```

**函数 / 执行器契约**：

| 位置 | 行为 |
|---|---|
| `SkillPlatformAccess.collect_batch(keys)` | 顺序解析 + `acquire`；返回有序条目；平台错误进报告，基础设施错误按原规则失败 |
| `SkillPlatformAccess.start()` | 使用请求集采集；预登录失败时把最后报告附在异常上；成功时更新环境报告 |
| `skill_bridge._write_state()` | 使用请求集采集；成功平台写状态文件；失败附 `platform_sessions` |
| `PlatformSessionEnvironment.report` | 暴露最后报告；executor/沙箱在退出前读取 |
| `SkillExecutionError` / `SkillToolError` / draft 错误 | 增加可选的 `platform_sessions`；现有错误码/retryable/detail 不变 |
| `SkillExecutionResult` / draft 失败结果 | 失败时附 `platform_sessions` 和一行摘要；成功结果不新增字段 |
| `skill_tools._workspace_shell_with_platforms()` | 预登录失败时返回带 `platform_sessions` 的现有错误载荷；不新增执行协议 |
| `run_service` / finalization | 读取失败结果 `stderr` 前部的有界摘要（现有 `evidence_preview`）；逐平台告知；结构化 `platform_sessions` 供模型读取；不新增审计列 |

**失败分类**：

| 类别 | 行为 |
|---|---|
| 平台认证/配置、声明集缺凭据、可重试平台失败 | 进报告条目；`list-platforms` 完成后成功退出；`get-state` 仍失败退出 |
| 基础设施（目录/Redis/预算/限流/Console 解析） | 按原规则失败；已采集条目保留，剩余 `available=null` + `reason` |
| 无会话 / REQUEST_SIGNING | `list-platforms` 中 `available=null` + `reason`；不伪装成认证失败 |
| 显式未授权平台 | 按原授权规则拒绝；不返回报告替代授权 |

### 3.5 实现落点与质量

| 位置 | 有限改动 |
|---|---|
| `packages/platform-sdk/.../skill_access.py` | 顺序采集；`start()` 使用请求集；预登录失败附最后报告；不静默过滤缺凭据平台 |
| `packages/platform-sdk/.../skill_bridge.py`、`session_cli.py` | 增强 `list-platforms`；`_write_state` 失败附报告；`--help` 同步；成功输出/状态文件保持 |
| `packages/agent-core/.../skill 的错误包装与执行器` | 从 `PlatformSessionEnvironment.report` 读取最后报告；失败时附 `platform_sessions` 和一行摘要；不改执行协议/成功结果 |
| `apps/agent-runtime/.../skill_tools.py` | 预登录失败/脚本失败透传报告；更新 `execute` 描述 |
| `apps/agent-runtime/.../run_service.py`、`finalization` | 现有失败收尾读取 `platform_sessions` 或 `stderr` 前部摘要；逐平台告知；不新增审计列 |
| `apps/agent-worker/.../worker 的失败出口` | 同类失败转任务错误时保留诊断；复用现有失败展示/投递，不改状态、重试决策或成功结果 |
| 文档与作者指南 | `self-skill-creator`、`MSSP-INTEGRATION.md`、`legacy-skill-migration.md` 同步 `list-platforms` 新语义（会登录/续期/消耗预算）与“Skill 直接用 get-state”；删除 `session_get_state` 陈旧段落 |

**摘要的落点与边界**：

- executor 在失败结果的 `stderr` **前部**插入一行摘要（脚本自己的 stderr 保留在后）；`skill_execution_outcome` 的 `evidence_for` 取 stderr，现有 1 KiB `evidence_preview` 包含摘要；失败收尾现有预览预算足够覆盖 2 个平台。
- 结构化 `platform_sessions` 放在工具错误载荷顶层；模型第一次能读；不要求成功结果全面增加字段。
- 摘要与结构化报告使用同一份数据；不同时维护两套采集。
- 摘要单行、有界；平台多时优先列出失败项，其余用 `+N` 或省略；不做无界增长。

**安全**：

- 失败明细复用适配器 `_scrub`，补齐本次 CSRF token（以及登录响应中可取得的 Cookie 值）的擦除；报告只携带 `http_status` / `business_code` / `message`，不得出现 Cookie、CSRF、AK/SK 原值。
- 日志只记平台 key 与错误码；报告/摘要/对话不打印状态文件正文或请求 profile。

**超时与预算**：

- 顺序检查沿用现有出网预算与 `acquire` deadline；不新增并发或自动重试。
- 查询在现有客户端超时前保留已知结果，剩余项 `available=null` + `reason`；具体超时值由既有所有者定义，不写死。
- 预算/限流/取消按原规则失败或标注未检查，不编造登录成败。

**测试建议**（后续任务文档声明，本轮不实施）：

- `tests/platform_sdk/test_skill_bridge.py`、`test_unified_session_contract.py`：真实 Unix socket + CLI；`list-platforms` 一成一败、全失败、缺凭据、无会话平台；`get-state` 失败带 `platform_sessions`；成功形状/状态文件/身份校验保持。
- SDK 采集与错误映射：首平台失败后继续后续平台；请求范围/顺序/原因准确；`start()`/`_write_state()` 使用请求集；缺凭据不被过滤。
- `tests/agent_runtime/test_workspace_shell_tool.py`：AUTO `list-platforms` 完整报告；显式 `platforms` 预登录失败（B-05）在工具错误里带报告；查询登录经现有预算与审计。
- `tests/agent_runtime/test_finalization_facts.py`、`test_finalization_render.py`、`test_finalization_funnel.py`：Skill 仍失败；脚本内 `get-state` 失败的 1 KiB 证据与预览预算下，2 个平台摘要仍可见；不新增字段。
- `tests/agent_runtime/test_skill_tools.py`：`execute` 描述包含“list 用于列 session / Skill 直接用 get-state / 不要先 list”；`platform_sessions` 在错误载荷。
- `tests/sdk/test_platform_sliding_session.py` 及既有集成回归：复用、续期、刷新、文件清理；Worker 失败诊断透传。
- 负向验证：恢复首错即停、丢弃报告、通用文案、`list-platforms` 只回目录时，对应回归变红；敏感值探针检查报告、回复和日志。

## 4. 部署与运维

随使用 platform-sdk 的 Runtime / Worker 发布。无数据库迁移，无新增 Redis 键或指标；最后报告只在 broker 内存中，摘要复用现有证据/错误载荷。回退对应镜像即可。旧 Skill 保留原命令与执行流程；`list-platforms` 的 `available` 语义变化在 `--help` 和作者指南中说明。

本阶段只修改设计和人工场景；实现任务通过 task finish 执行声明测试。全量 pytest/acceptance/component 按项目 CI 策略执行；浏览器场景由人工在真实服务与真实浏览器执行，不进 CI。

## 5. 风险与依赖

| 风险 ID | 风险与应对 | 场景 |
|---|---|---|
| RISK-01 | 模型仍按旧说明“先 list 再 get-state”，使 Skill 前触发全平台登录；`execute` 描述改为“列 session 用 list；Skill 直接用 get-state”；补契约测试 | S-03 / B-03 |
| RISK-02 | 有界摘要被 1 KiB/300 字预算截断；摘要单行有界、失败项优先；用 2 平台集成测试锁定 | S-04 / E-03 |
| RISK-03 | 脚本内 `get-state` 失败时报告只存在于脚本 stderr；executor 从环境最后报告附加摘要与结构化字段；测到实际失败回复 | S-04 / E-03 |
| RISK-04 | 顺序检查耗尽现有预算或客户端超时；保留已知结果，剩余 `available=null` + `reason`；本轮不做并发 | B-04 |
| RISK-05 | 无会话/REQUEST_SIGNING 平台被误报为认证失败；`list-platforms` 标 `available=null` + `reason`；不做完整适配器支持 | B-04 |
| RISK-06 | `available` 语义变化影响旧消费者；保持 `version: 2` 但同步 `--help`、作者指南与测试；如外部脚本依赖旧语义，记录风险 | B-01 / B-02 |
| RISK-07 | 平台原文含 Cookie/CSRF/AK/SK；扩展 `_scrub` 并加 canary 测试；日志只记 key/code | S-02 / S-04 |

## 6. 需求追溯矩阵

| 需求 | 功能 | 场景 | 验证方式 | 状态 |
|---|---|---|---|---|
| 首次列清每个平台状态 | FEAT-01/02 | S-01 / S-02 / E-01 / B-05 | CLI/工具集成 + 人工对话 | 待实现 |
| 原因准确、缺凭据不被过滤 | FEAT-03 | E-02 / B-04 | SDK 契约/集成 + 人工对话 | 待实现 |
| Skill 范围和停止行为保持 | FEAT-04 | S-03 / S-04 / E-03 | 既有执行回归 + 人工业务观察 | 待实现 |
| Skill 失败一次告知依赖平台结果 | FEAT-05 | S-04 / B-05 | 失败收尾/错误载荷 + 人工对话 | 待实现 |
| 模型说明与新语义同步 | FEAT-06 | S-03 / B-03 | 契约测试 + 人工对话 | 待实现 |
| 最后报告与有界摘要 | FEAT-07 | S-04 / B-05 / E-03 | 集成 + 失败收尾回归 | 待实现 |
| 授权、缓存和状态文件机制保持 | FEAT-04 | B-01 / B-02 / E-03 | 真实依赖集成 + 人工验收 | 待实现 |

## Spec Compliance Matrix

| Spec / Rule | 约束与设计落点 | 场景 |
|---|---|---|
| `platform/harness-project-platform#RULE-platform-001` | 认证和会话继续归 Adapter / 会话管理器；凭据归属与缓存协议保持，§2.4 / §3.2 | B-01 / B-02 |
| `platform/harness-project-platform` 的失败原因约定 | 保留安全原因；`get-state` 失败仍 stderr + 非零退出；`list-platforms` 完成检查后以报告返回，§3.3 / §3.4 | S-02 / E-03 |
| `secret/harness-secret#RULE-002` 及第三方原文擦除约定 | 报告进入模型和失败回复前擦除 ak/sk/headers/CSRF/Cookie 值，§3.3 / §3.5 | S-02 / S-04 |
| `test/harness-test` | 有意义的契约/集成回归；人工浏览器验收与 CI 自动化分开，§3.5 / §4 | 全部场景 |

## Spec Delta（后续任务文档声明）

本轮不改已接受 Specs。后续在 `platform/harness-project-platform` 的 Conventions 补充：

- `list-platforms` 按本次范围逐平台检查实际登录态，返回实际 `available`、有效期或失败原因；CLI `available` 不再只表示“有凭据配置”；Console 内部目录继续提供候选范围；
- 既有会话设计中“`list-platforms` 仅目录、不触发登录”的说明随该项修正；保留平台发现用途，不新增查询命令；
- `get-state` / `refresh` 沿用原有失败出口；Skill 失败报告随现有错误载荷/证据透传；
- 执行级只保留“本批次最后报告”一个内存对象，不新增账本、审计字段或数据库迁移。

`secret/harness-secret` 的“第三方原文回传前必须擦除本次凭据值”补充：擦除范围包含本次 CSRF token 与登录响应中可取得的 Cookie 值；报告/摘要/对话不得出现 Cookie、CSRF、AK/SK 原值。

- ✅ 查询一个成功、一个失败的平台，第一次回复分别说明结果和原因；Skill 因 `get-state` 失败停止，失败回复保留依赖平台诊断。
- ❌ 单个平台失败导致整批结果丢失；缺少依赖的 Skill 被当作成功继续；用截断预览代替完整报告；为 `list-platforms` 新增第二套查询命令。

## 附录：术语表

| 术语 | 定义 |
|---|---|
| `available` | CLI `list-platforms` 中按现有会话机制实际确认的可登录态；`false` 是失败，`null` 是未检查 |
| 最后报告 | 一次执行内按请求顺序保存的批次结果；不持久化、不跨调用 |
| 有界摘要 | 最后报告的紧凑文本投影，放入错误信息/`stderr` 前部，复用现有证据预算 |
| 显式预登录失败 | `execute platforms` 在 shell 命令前由 `start()` 触发的失败；命令未执行 |
| 硬失败 | 不写状态文件、不产出查询报告，直接 stderr + 非 0 的授权/参数/基础设施失败 |

*文档结束*
