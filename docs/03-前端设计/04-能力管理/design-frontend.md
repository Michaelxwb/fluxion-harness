# 能力管理 前端模块需求与设计简报

> **文档编号**: FE-04-V1.11  
> **文档版本**: V1.11 模块分档拆分版  
> **创建日期**: 2026-09-11  
> **文档状态**: 交互基线已冻结，待仓库 Spec Context 绑定  
> **模板**: `design-frontend.md`  
> **交互事实源**: `../90-Console交互规格.md` + `../archive/fluxion-console-interaction-prototype-v0.8-final.html`（已归档：仅作交互形态参考，冲突以 90-规格 + 后端授权列为准）

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 前端负责人 | 待定 | 页面、路由、组件、services、E2E |
| 产品/交互 | 待定 | V0.8 交互与字段契约 |
| 后端 Owner | 见 API 映射 | DTO/权限/错误码 Contract |

### 1.2 修订历史

| 版本 | 日期 | 变更描述 |
|---|---|---|
| V1.11 | 2026-09-11 | 从大一统 Console 文档拆成独立产品模块设计 |
| V1.13 | 2026-09-12 | 第三轮 Review 修复：新增表单与 `CAP-API-02` 对齐（去 `enabled` 必填、幂等性三值、`output_schema` 必填、补 `side_effect`/`execution_characteristic`/`authorization_requirement`/`error_semantics`）；四类实现配置改引用模块 07 `capability-implementation-schema` 字段名并声明 timeout/retry 属 `execution_policy`、分页属 `data_retrieval_policy`；补 `FEAT-04-06` 与场景 `S-04-04`、修正两处 FEAT 错配；场景 ID 前缀拆分（integration → `I-04-01`） |
| V1.13.1 | 2026-09-12 | 第四轮 Review 修复：§3.4 补「能力测试（`CAP-API-05`，Z-04）」块（入口在详情页顶部、Builder+Admin 不隐藏、`input_schema` JSON 编辑器就地报错、`test_user_id` 仅 `USER_PLATFORM`/平台 `auth_mode=当前用户` 时显示、耗时取 `stats.latency` 渲染 `N ms`、`artifact_id` 走 `EXE-API-06` 不下发直链、`stats` 缺字段不补 0、`execution_source=TEST` 不计运营统计）；补场景 `S-04-05`；**D1（ADR-057）：判定元数据收敛**——删除 `execution_characteristic`/`authorization_requirement`/`error_semantics`（DTO/DB/校验全删），新增 `invocation_policy`（`DIRECT` 默认 / `EXECUTION_ONLY`），保留 `side_effect`/`risk_level` 作安全审计维度，凭据来源改由实现层 `auth_mode` 表达；§3.4 公共字段表与 §3.5 测试弹窗同步，`S-04-04`/`E-04-01` 断言同步（含 `side_effect=write`/`destructive` 或 `risk_level=HIGH` → 强制 `EXECUTION_ONLY`，非法组合 400 `CAPABILITY_CONTRACT_INVALID`） |
| V1.14.1 第五轮契约同步 | 2026-09-13 | 第五轮 D8~D15 契约同步：`auth_mode` 明确为 `implementation` **顶层必填字段**（默认 `NONE`，三值 `USER_PLATFORM`/`SHARED_SECRET`/`NONE`），从 PLATFORM_SERVICE 的 `config` 字段行删除，`config` 内出现该键一律 400 `CAPABILITY_IMPLEMENTATION_INVALID`；能力测试执行源改为 `CAPABILITY_TEST`（仅能力测试面板可见、不进默认执行列表），测试输出补 `execution_id` 并用其调 `EXE-API-06` 下载产物；`S-04-05` 断言同步 |

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | 能力管理 |
| 需求类型 | 页面/交互模块 |
| 业务背景 | Capability 是稳定原子能力边界，需要屏蔽 Platform Service/HTTP/MCP/Sandbox 的具体实现差异，并承担自动分页。 |
| 核心目标 | 完成 Capability Contract、四类实现配置、Data Retrieval Policy、测试和只读详情。 |
| 路由 | `/console/capabilities` |
| 角色 | Builder / Admin |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-04-01 | 能力列表 | 按类型/风险/状态筛选 | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-02 | Capability Contract | Input/Output Schema、风险、副作用、幂等 | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-03 | 四类实现 | Platform Service/HTTP/MCP/Sandbox typed form | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-04 | 自动分页 | Page/Offset/Cursor | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-05 | 测试 | 执行测试并显示结果 | P0 | V0.8 / Playbook / 总设 |
| FEAT-04-06 | 新增字段校验 | Create DTO 字段级必填/枚举校验与错误定位（含 `side_effect`/`invocation_policy` 等字段，及 `invocation_policy` 与 `side_effect`/`risk_level` 的强制组合） | P0 | V0.8 / Playbook / 总设 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | 完成 Capability Contract、四类实现配置、Data Retrieval Policy、测试和只读详情。 |
| 非范围（Out of Scope） | 不提供万能 JSON implementation 编辑器，不把 ProjectPlatform FK 强加给 HTTP/MCP/Sandbox。 |
| 有意妥协 / 技术债 | 仓库技术栈、组件 API 细节待真实 repo scan 后锁定；产品字段和交互语义已冻结。 |

### 2.4 验收条件

**通用规则**

- Console 仅面向 Builder/Admin，End User 不进入 Console。
- Semi Design 标准列表：左上一个主动作，右上筛选/搜索，中间 Table，右下 PageSize + Pagination。
- 详情默认只读；新增/编辑使用独立 Modal 或独立编辑页，不在详情 Drawer 内直接编辑。
- Secret 不回显；创建后不可变 key 在编辑态只读。
- 所有时间显示 `YYYY-MM-DD HH:mm:ss`。
- 页面组件不得直接裸调用 fetch/axios，统一经 `services/` 或等价数据访问层。

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|---|---|---|---|---|---|
| S-04-01 | FEAT-04-01 | E2E | 四类实现动态表单 | Builder 分别创建 HTTP/MCP/PLATFORM_SERVICE/SANDBOX 能力 | 类型切换渲染对应字段；PLATFORM_SERVICE 必选平台（安全选项） |
| S-04-02 | FEAT-04-02 | E2E | 分页策略表单 | Builder 配置 Page 分页+短页终止 | short_page_terminates 默认开启；可显式关闭并提示适用前提（R19 语义） |
| S-04-03 | FEAT-04-05 | E2E | 能力测试 | Admin 测试能力 | 测试结果展示 inline/summary；失败显示脱敏错误 |
| S-04-04 | FEAT-04-03 | E2E | 四类实现配置字段名与判定元数据 | 四类实现各保存一条（PLATFORM_SERVICE / HTTP / MCP / SANDBOX）；另一条把 `invocation_policy` 选为 `EXECUTION_ONLY` 后保存 | 请求体字段名与模块 07 `capability-implementation-schema` **逐字一致**；PLATFORM_SERVICE 缺 `project_platform_id` 时返回 422 且错误定位到该字段；`deadline_ms`/`max_retries` 出现在 `execution_policy` 而非 `config`；分页键出现在 `data_retrieval_policy`；`invocation_policy`/`side_effect`/`risk_level` 是公共字段，**不出现** 在实现 `config` 内，且请求体**不含** `execution_characteristic`/`authorization_requirement`/`error_semantics` |
| E-04-01 | FEAT-04-06 | E2E | 副作用字段必填与直调策略约束 | 创建能力不选"是否有副作用"；另选 `side_effect=write`（或 `risk_level=HIGH`）后再把 `invocation_policy` 选成 `DIRECT` 并提交 | 校验失败并定位到 `side_effect` 控件，不发起 `CAP-API-02`；选 `write`/`destructive` 时显示风险等级建议提升提示；`side_effect=write`/`destructive` 或 `risk_level=HIGH` 时 `invocation_policy` 只能为 `EXECUTION_ONLY`——前端就地提示并禁用非法项，强行提交由后端 400 `CAPABILITY_CONTRACT_INVALID` 拒绝 |
| S-04-05 | FEAT-04-05 | E2E | 能力测试弹窗 | Admin 与 Builder 分别在能力详情点「测试」，填入合法 JSON 提交；再次填入非法 JSON 提交 | 两角色详情顶部均渲染「测试」按钮（`90` §4.7：Builder + Admin，不隐藏）；弹窗显示 `ok=true`；耗时以 `N ms` 呈现且取自 `stats.latency`（非客户端计时）；`trace_id` 可复制；`artifact_id` 非空时同时展示响应返回的 `execution_id`，并出现「下载产物」入口（`GET /api/v1/executions/{execution_id}/artifacts/{artifact_id}/download`）；填入非法 JSON 时**就地报错**，且 Network 面板**无 `CAP-API-05` 请求** |
| I-04-01 | FEAT-04-01 | integration | services → API → UI | 后端返回字段校验/权限/冲突错误 | 保留当前上下文并显示可定位错误，不出现假成功 |

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|---|---|---|---|
| 组件库 | Semi Design | 项目约束 | 与 Console 既定 UI 规范一致 |
| 状态 | Server state + 页面 local state | 待 repo scan | 避免把表单/list query 变成全局事实源 |
| 数据请求 | `services/` 统一封装 | 待 repo scan | 组件中禁止裸 fetch/axios |
| Router | 复用仓库现有 Router | 待 repo scan | 不凭文档替换技术栈 |

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|---|---|---|---|
| 能力管理 | `/console/capabilities` | ConsoleLayout | 完成 Capability Contract、四类实现配置、Data Retrieval Policy、测试和只读详情。 |

### 3.3 组件设计

| 组件ID | 组件名 | 类型 | 职责 |
|---|---|---|---|
| CMP-04-01 | CapabilityListPage | 容器 | 标准列表 |
| CMP-04-02 | CapabilityForm | 容器 | 公共 Contract 字段 |
| CMP-04-03 | ImplementationDiscriminator | 容器 | 按类型切换字段 |
| CMP-04-04 | DataRetrievalPolicyForm | 容器 | 分页策略 |
| CMP-04-05 | CapabilityTestPanel | 容器 | 测试输入/结果 |

### 3.4 组件接口契约与字段

**公共字段（与 `CAP-API-02` Create DTO 逐字对齐）**

| 控件 | DTO 字段 | 类型 | 必填 | 默认 |
|---|---|---|---|---|
| 能力名称 | `name` | input | 是 | — |
| 能力标识 | `key` | input | 是 | 创建后不可改 |
| 说明 | `description` | textarea | 是 | — |
| 输入参数定义 | `input_schema` | JSON Schema | 是 | — |
| 输出结果定义 | `output_schema` | JSON Schema | **是** | — |
| 风险等级 | `risk_level` | select | 是 | — |
| 是否有副作用 | `side_effect` | select | 是 | `none`；四值 `none`/`read`/`write`/`destructive` |
| 幂等性 | `idempotency_semantics` | select | 是 | 三值 `NONE`/`KEYED`/`NATURAL` |
| 直调策略 | `invocation_policy` | select | 是 | `DIRECT`（默认，可被 Agent/Skill 短时直调）；另一值 `EXECUTION_ONLY`（必须经 Execution/Worker 可靠执行）。help：“只读但长跑、需人工检查点、有外部副作用 → 选 `EXECUTION_ONLY`”；`side_effect=write`/`destructive` 或 `risk_level=HIGH` 时服务端强制 `EXECUTION_ONLY`（非法组合 400 `CAPABILITY_CONTRACT_INVALID`，前端在提交前就地提示并禁用非法项） |
| 实现类型 | `implementation_type` | select | 是 | 创建后不可改 |
| 启用状态 | `enabled` | 只读 | — | 新增**不提交**；创建后默认启用，仅编辑页可改 |

**四类实现配置：字段名逐字取自模块 07 `capability-implementation-schema`**

| 类型 | `config` 字段 |
|---|---|
| PLATFORM_SERVICE | `project_platform_id`（必填）/ `service_key` / `request_mapping` / `response_mapping` |
| HTTP | `method` / `base_url` / `path` / `query_mapping` / `body_mapping` / `header_keys`（仅非 Secret key，值走 ref）/ `response_mapping` |
| MCP | `server_key` / `tool_name` / `input_mapping` / `response_mapping` |
| SANDBOX | `capability_key` / `entrypoint` / `argv` / `env_allowlist` / `network_policy` / `max_output_bytes` |

**凭据来源 `auth_mode`（实现层顶层字段，不属于 `config`）**：`auth_mode` 是 `implementation.auth_mode`（**顶层、必填、默认 `NONE`**，三值 `USER_PLATFORM` / `SHARED_SECRET` / `NONE`），与 `implementation_type`/`config`/`execution_policy` 并列，**不写进 `config`**；`config` 内出现 `auth_mode` 一律 400 `CAPABILITY_IMPLEMENTATION_INVALID`。表单上 `USER_PLATFORM` 时必填项目平台选择，`SHARED_SECRET` 时必填共享 Secret 引用。

HTTP/MCP/SANDBOX **不显示** `project_platform_id`；SANDBOX 禁止自由 shell textarea。

**归属边界（V1.13 冻结）**：超时与重试**不属于 `config`**，属 `execution_policy`（`deadline_ms` / `max_retries` / `backoff_ms`），表单上独立成组；分页配置**不属于 `config`**，属 `data_retrieval_policy`。

**Pagination**：Page/Offset/Cursor 各自字段 + items_path + total/has_more/next_cursor + max_pages/max_items/max_duration + duplicate-page detection；Page/Offset `items==[]` 固定兜底；`short_page_terminates` 默认开启（true），下游可能产生中间短页时必须允许显式关闭；保证“非最后页必满”才可安全依赖短页终止（V1.12 裁决）。



**平台选择与安全**：PLATFORM_SERVICE 的项目平台下拉使用 **`PLAT-API-01` 响应**中的 `name`/`key`/`configured`/`enabled`（`configured` 与 `enabled` 均为后端返回字段，前端不自行推导）；UNCONFIGURED 不能用于测试/启用运行并显示“待配置认证模板”。Builder 可选择已配置平台，但不能打开认证管理写操作。

**能力自身 `enabled` 的状态来源**：列表筛选、详情与编辑页展示的 `enabled` 取自 **`CAP-API-01` / `CAP-API-03` 响应**；Create（`CAP-API-02`）不接受 `enabled`，只有 Update（`CAP-API-04`）可改。

**列表查询**：通用契约见 FE-00 §3.4 `StandardListQuery`，本页领域筛选：`implementation_type`/`risk_level`/`invocation_policy`/`enabled`（另加 `keyword` 按名称/标识搜索），服务端筛选、改筛选重置 `page=1`、状态进 URL。

**能力测试（`CAP-API-05`，Z-04）**：契约与文案见 `../90-Console交互规格.md` §4.7（`FEAT-04-05`），组件为 `CMP-04-05 CapabilityTestPanel`。

| 项 | 前端规范 |
|---|---|
| 入口 | 能力**详情页顶部**「测试」按钮（与「编辑」并列）；列表行内**不放**测试（测试需要 JSON 输入，行内无法承载） |
| 权限 | **Builder + Admin**（与模块 07 `CAP-API-05` 授权列一致）；对 Builder **不隐藏**按钮 |
| 输入 | ① 测试输入：按该能力 `input_schema` 渲染的 JSON 编辑器，Schema 非法的 JSON **就地报错、不发请求**；② 测试用户 `test_user_id`：**仅当**该能力的 `auth_mode=USER_PLATFORM`（平台用户认证；凭据来源唯一由实现层 `auth_mode` 表达，能力本身不再声明）时显示，默认当前登录用户；③ 结果模式 `result_mode`：`INLINE` / `SUMMARY`，默认跟随后端实现策略 |
| 输出 | `ok` + 归一化输出：inline 直接展开；外置时显示 summary + `artifact_id` + `execution_id` 与「下载产物」入口（`GET /api/v1/executions/{execution_id}/artifacts/{artifact_id}/download`，**不下发对象存储直链**）；**耗时**取自 `stats.latency`（渲染为 `N ms`，**不使用客户端计时**）；显示响应 `trace_id`（可复制） |
| 统计行 | 显示 `stats` 的 `downstream_calls` / `pages` / `items` / `retries`：后端返回才渲染，**缺字段不渲染该项、前端不补 0** |
| 错误呈现 | 弹窗内错误条显示脱敏错误码/消息（如 `DRY_RUN_UNSUPPORTED`），**不清空已填输入**；不提供“自动改配置”快捷操作 |
| 副作用 | 测试结果**不产生运营统计口径的执行**（`execution_source=CAPABILITY_TEST`，仅能力测试面板可见，不进默认执行列表）：不影响运营统计，仅落审计；产物走 `EXE-API-06` 下载，必须用响应返回的 `execution_id` |

### 3.5 状态与数据流

```text
用户操作
→ 容器组件校验
→ services/04Service
→ 后端 API
→ 统一错误映射
→ query/local state
→ UI 重渲染
```

**API 映射**

| Service 方法/动作 | API ID | 方法/路径或契约 | 后端 Owner |
|---|---|---|---|
| Capability 列表 | `CAP-API-01` | `GET /api/v1/capabilities` | Capability Runtime |
| 新增 Capability | `CAP-API-02` | `POST /api/v1/capabilities` | Capability Runtime |
| Capability 详情 | `CAP-API-03` | `GET /api/v1/capabilities/{capability_id}` | Capability Runtime |
| 编辑 Capability | `CAP-API-04` | `PUT /api/v1/capabilities/{capability_id}` | Capability Runtime |
| 测试 Capability | `CAP-API-05` | `POST /api/v1/capabilities/{capability_id}/test` | Capability Runtime |
| 读取 Capability Contract | `CAP-API-06` | `GET /api/v1/capabilities/{capability_id}/contract` | Capability Runtime |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 列表 | Skeleton | 暂无能力 | 错误+重试 | 列表 |
| 动态实现表单 | 字段 schema loading | — | 字段校验定位 | typed form |
| 测试 | 按钮 loading | 无测试 | 错误详情 | 结果/Artifact |

### 3.7 样式与交互规范

- 不重复大页面标题/说明，左侧菜单 + breadcrumb 已表达当前位置。
- 列表页 page size 属于当前列表，不设置全局 page size。
- 行操作固定在最右侧；危险操作二次确认。
- 详情只读，编辑与详情分离。

### 3.8 可访问性与兼容性

- Modal/Drawer 打开后焦点进入容器，关闭后恢复触发点。
- 所有图标按钮具备可访问名称；Form 错误与字段关联。
- 表格主操作可键盘访问。

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|---|---|---|---|---|
| RISK-04-01 | 实现类型切换造成无效字段残留 | 高 | discriminated form + DTO 类型收敛 | E2E/Integration |
| RISK-04-02 | 大结果直接塞入浏览器/LLM | 高 | 详情显示 Artifact/摘要策略 | E2E/Integration |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| Console-V0.8#READONLY-DETAIL | required | 详情不得变成编辑入口 | §3.3/§3.7 | S-04-01 | applied |
| Console-V0.8#SERVICE-LAYER | required | API 统一从 services 层发起 | §3.5 | S-04-01 | applied |
| BACKEND-07#ADR-057-DIRECT-INVOCATION | required | `invocation_policy` 与 `side_effect`/`risk_level` 强制组合（非法 400 `CAPABILITY_CONTRACT_INVALID`） | §3.4 | E-04-01 | applied |

## 附录：后端追溯

该模块只定义前端状态与交互，不重复定义 DB。DB 字段/索引/约束以对应后端模块 `design-full.md` 为唯一事实源；本文件通过 API ID 追溯到后端 Owner。
