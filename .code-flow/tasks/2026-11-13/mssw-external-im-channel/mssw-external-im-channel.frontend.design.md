---
design_template: frontend
---

# MSSW 外部 IM 入口与现有 WebChat 前端设计

> **文档编号**: FE-MSSW-IM-1.0
> **文档版本**: v1.2
> **创建日期**: 2026-11-13
> **文档状态**: 设计评审中

**评审边界说明**:
- **需求评审**: 第 2 章 → 通过后锁定需求基线
- **设计评审**: 第 3 章 → 通过后锁定设计基线

**ID 体系**: US（来自 PRD）、FEAT、CMP、NFR；场景引用 `mssw-external-im-channel.scenarios.md` 的 S-/E-/B-。

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | fluxion-harness | 技术方案、代码实现 |
| 设计/交互 | fluxion-harness | 交互与视觉（复用现有 Console 规范） |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-11-13 | fluxion-harness | 初始设计：MSSWWEB 入口表单、用户直绑、平台状态提示 |
| v1.1 | 2026-11-13 | fluxion-harness | 对齐修正：用户级外部身份 + 入口授权；编辑身份；平台元数据/identity_keys；共享数据 |
| v1.2 | 2026-11-13 | fluxion-harness | 补齐现有 WebChat 共享列表、跨入口选择/刷新/发送、当前入口与来源分离、通知水位、撤权状态及管理 DTO 修正 |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | Console 管理面与现有 WebChat |
| **需求类型** | 功能扩展（复用现有 Agent/用户管理页） |
| **业务背景** | 管理员需要创建 MSSWWEB 入口、查看平台配置状态，并把 MSSW 业务身份标识绑定到平台用户。 |
| **核心目标** | 在现有管理页维护身份/入口授权；修复现有 WebChat 对共享数据的入口限制。业务平台负责自己的 MSSWWEB 聊天页。 |

### 2.2 功能方案

| 功能ID | 功能名称 | 说明 | 优先级 | 来源 |
|--------|---------|------|--------|------|
| FEAT-11a | MSSWWEB 入口表单 | Agent 详情 → 通道：新增 MSSWWEB 类型；无 secret；填写入口标识 `bot_id`；平台状态提示 | P1 | US-06 |
| FEAT-11b | 用户级外部身份 | 用户详情 → 外部身份：创建/编辑一个用户级身份 JSON（平台级字段集合）；不再按入口拆身份 | P1 | US-06/11 |
| FEAT-11c | 入口授权 | 同一外部身份下增加/移除入口授权；解绑只删授权，不删身份 | P1 | US-06/10 |
| FEAT-11d | 平台状态展示 | 表单/状态卡显示 `mssw` 是否已配置、平台 `identity_keys`；不显示因子 | P1 | US-07 |
| FEAT-05/08 | WebChat 跨入口会话 | 共享列表、跨来源选择与 URL 刷新；从当前有权入口续聊，不改创建来源 | P0 | US-01/03/07 |
| FEAT-06 | WebChat 已读水位 | 展示配对的详情/历史后标记通知已读，保留并发新通知 | P0 | US-04 |

### 2.3 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | Console 入口/身份/授权管理；现有 WebChat 的 API service、共享列表、会话选择、初始化/URL 刷新、发送上传、通知水位及授权变化状态。 |
| **非范围（Out of Scope）** | 业务平台前端/BFF；MSSWWEB 聊天页面；因子编辑/轮换；MSSP。 |
| **有意妥协 / 技术债** | 平台状态只读；因子只能通过部署 Secret 修改；入口列表跨 Agent，分页加载。 |

### 2.4 验收条件

> 场景唯一源：`mssw-external-im-channel.scenarios.md`。

| 场景ID | 前端设计影响 |
|--------|-------------|
| S-02 | 管理员在用户详情创建用户级外部身份（JSON），再授权入口；解绑入口后身份仍在 |
| S-01 | 管理员在 Agent 通道创建 MSSWWEB 入口，填写入口标识、无 secret，平台状态显示已配置 |
| S-09 | 管理员编辑外部身份 JSON（多字段），影响名下所有入口授权；历史会话不丢 |
| E-03 | Secret 未配置时，表单显示“平台未配置”，平台元数据仍可见；入口可创建但不可用 |
| S-11/E-04 | 实际 WebChat 页面可从列表或 URL 打开 MSSW 创建的同用户同 Agent 会话，且能续聊 |
| S-05/B-05 | 详情返回通知水位；正文/确认展示成功后才标读；水位域不能混用 |
| E-05/E-10/E-13 | 撤权、停用与同步待恢复有明确表现；不把 Agent 撤权误判为登录过期 |

#### 2.4.1 验证环境假设

| 维度 | 假设 | 验证方式 | 不满足时 |
|------|------|---------|---------|
| 容量 | 单 Agent 少量入口；用户少量绑定 | 组件测试 + 手工 | 记录 |
| 硬件 | 现有 CI/浏览器矩阵 | 人工检查 | 记录 |
| 依赖 | Console 后端 API、PATCH 权限、i18n 词条 | 人工检查 | 记录 |
| 测试预算 | component + contract；E2E 留在 CI | 任务文档声明 | 记录 |

---

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 理由 |
|------|------|------|------|
| 框架 | React + TypeScript | 现有 | 现有 Console |
| 状态 | 组件局部状态 + 现有 `useAsyncList` | 现有 | 不引入全局 store |
| UI | Semi Design | 现有 | 现有表单/弹窗/表格 |
| 数据请求 | `api` 封装 + service 模块 | 现有 | 复用拦截器与错误提示 |
| 样式 | 现有 CSS/class | 现有 | 不新增视觉体系 |

### 3.2 页面与路由结构

不新增路由，复用现有页面：

| 页面 | 路由 | 位置 | 说明 |
|------|------|------|------|
| Agent 详情 | 现有 Agent 管理 | 通道 Tab | `ChannelFormModal` 增加 MSSWWEB 类型 |
| 用户详情 | 现有用户管理 | 身份 Tab | `IdentityTab` 增加直绑按钮/弹窗 |
| 平台状态 | 不新增页面 | 通道表单内 Banner | 读取 `GET /external-platforms`，只读 |

### 3.3 组件设计

```text
<ChannelFormModal>                 # 现有容器，扩展 MSSWWEB
├─ <ChannelKindSelect>             # WECOM / WEBCHAT / MSSWWEB
├─ <MsswwebPlatformBanner>        # 平台 mssw 配置状态 + identity_keys
└─ <ConfigEditor>                 # MSSWWEB 时隐藏/只读

<IdentityTab>                      # 用户详情
├─ <BindCodeButton>                # 旧通道绑定码，保留
├─ <ExternalIdentitySection>       # 新：用户级外部身份
│   ├─ <ExternalIdentityRow>       # 身份 JSON、平台、状态、编辑
│   └─ <EntryGrantList>            # 该身份下的入口授权；增加/移除
├─ <MsswwebIdentityModal>          # 创建/编辑用户级身份 JSON
├─ <EntryGrantModal>               # 选择入口并授权
└─ <IdentityTable>                 # 旧 WECOM/WebChat 身份，保留
```

| 组件ID | 组件名 | 类型 | 复用来源 | 职责 |
|--------|--------|------|---------|------|
| CMP-01 | `ChannelFormModal` | 容器 | 现有 | 增加 MSSWWEB 分支；提交 `channel/name/bot_id/enabled`；MSSWWEB 不提交 secret |
| CMP-02 | `MsswwebPlatformBanner` | 展示 | 新建 | 显示 `mssw` 配置状态与平台 `identity_keys`；不显示因子 |
| CMP-03 | `MsswwebIdentityModal` | 容器 | 新建 | 创建/编辑**用户级**身份 JSON；不含入口选择 |
| CMP-04 | `ExternalIdentitySection` | 容器 | 新建 | 身份行 + 入口授权列表；编辑/授权/解绑 |
| CMP-05 | `EntryGrantList` / `EntryGrantModal` | 展示/容器 | 新建 | 列出/增加/移除入口授权；解绑只删 grant |
| CMP-06 | `IdentityTable` | 展示 | 现有 | 旧 WECOM/WebChat 身份；不参与 MSSWWEB 用户级模型 |

### 3.4 组件接口契约

**CMP-01 `ChannelFormModal`（扩展）**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `agentId` | string | 是 | — | 现有 |
| `channel` | `AgentChannelItem \| null` | 是 | — | 编辑时传入 |
| `platforms` | `ExternalPlatformItem[]` | 否 | `[]` | 用于 MSSWWEB 状态提示 |

- MSSWWEB 时：
  - `secret` 字段隐藏；
  - `config` 字段隐藏（平台配置在 Secret，不在页面）；
  - `bot_id` 字段保留，标签为“入口标识”，必填，与 WebChat 一致；
  - 顶部 Banner 显示 `platform_key=mssw`、`configured` 与平台 `identity_keys`。

**CMP-02 `MsswwebPlatformBanner`**

| Props | 类型 | 必填 | 说明 |
|-------|------|------|------|
| `configured` | boolean | 是 | 平台是否已配置 |
| `platformKey` | string | 是 | 固定 `mssw` |

**CMP-03 `MsswwebIdentityModal`**

| Props | 类型 | 必填 | 说明 |
|-------|------|------|------|
| `visible` | boolean | 是 | 弹窗开关 |
| `userId` | string | 是 | 平台用户 id |
| `platform` | `ExternalPlatformItem` | 是 | 平台元数据，含 `identity_keys` |
| `identity` | `{id: string; platform_key: string; json: Record<string, string>} \| null` | 否 | 编辑时传入；`null` = 新增 |
| `onCancel` | `() => void` | 是 | 关闭 |
| `onSaved` | `() => void` | 是 | 成功后刷新列表 |

| Events | 载荷 | 触发 |
|--------|------|------|
| `onSubmit` | `{identityId?: string, platformKey: string, identity: Record<string, string>}` | 点击“保存” |

**CMP-05 `EntryGrantModal`**

| Props | 类型 | 必填 | 说明 |
|-------|------|------|------|
| `identityId` | string | 是 | 外部身份 id |
| `entries` | `MsswwebEntryOption[]` | 是 | 来自 API-19 |
| `existingGrantEntryIds` | string[] | 是 | 已授权入口，避免重复 |
| `onSearch` | `(query: string) => void` | 否 | 入口多时按名称搜索 |
| `onLoadMore` | `() => void` | 否 | API-19 分页加载 |
| `onSubmit` | `{channelAccountId}` | 是 | 授权入口 |

**CMP-04 `ExternalIdentitySection`**

- 展示用户级外部身份（平台、身份 JSON、状态、grants）；
- “编辑”打开 `MsswwebIdentityModal`；
- “添加入口”打开 `EntryGrantModal`；
- 行内移除 grant（二次确认），只删授权，不删身份；
- 成功后 reload；错误 `IDENTITY_ALREADY_BOUND` / `IDENTITY_KEYS_MISMATCH` 字段提示。

### 3.5 状态与数据流

```text
ChannelFormModal
  ├─ mount → GET /api/v1/external-platforms （平台状态 + identity_keys）
  ├─ submit → POST/PUT /api/v1/agents/{id}/channels
  └─ 成功后 onSaved → 现有列表刷新

ExternalIdentitySection
  ├─ mount → GET /users/{id}/external-identities
  ├─ 新增身份 → PUT /users/{id}/external-identities {platform_key, identity:{...}}
  ├─ 编辑身份 → PUT /users/{id}/external-identities/{identity_id} {identity:{...}}
  ├─ 授权入口 → POST /users/{id}/external-identities/{id}/grants {channel_account_id}
  ├─ 解绑入口 → DELETE /users/{id}/external-identities/{id}/grants/{entry_id}
  └─ 成功 → reload external identities + grants
```

- 表单校验：
  - `bot_id`：所有通道必填（1..256，与 WebChat 一致）；
  - `platform_key`：来自平台元数据，固定不可编辑；
  - `identity`：必填 JSON 对象，至少 1 个键；值为非空字符串；
  - key 集合必须与平台 `identity_keys` 完全一致；多字段 AND；
  - `channel_account_id`：只在授权入口时选择；身份编辑不改入口。
- 不缓存平台状态；每次打开表单取一次。
- 不在前端保存/展示 salt、公钥、issuer/audience。
- 编辑 DTO 不发送 `platform_key`，平台只用于显示；后端禁止额外字段。创建/编辑分别用明确的 service 类型，不复用宽泛 payload。
- 搜索传 API-19 `query/page/page_size`；改变 query 重置页码及候选列表，丢弃旧搜索返回；授权成功刷新 grants。不能用只过滤已加载第一页的前端搜索冒充完整搜索。

### 3.6 UI 状态

| 状态 | 表现 |
|------|------|
| 平台已配置 | 绿色/中性 Banner：`mssw 已配置` |
| 平台未配置 | 橙色 Banner：`平台未配置，入口可创建但不可用`；保存不禁用（便于先建入口） |
| 入口列表为空 | 弹窗内空态：“当前没有可绑定的 MSSWWEB 入口，请先在 Agent 通道创建” |
| 身份创建/编辑成功 | 关闭弹窗 + Toast + 身份列表刷新 |
| 授权/解绑入口成功 | Toast + grants 列表刷新 |
| 已绑其他用户 | 弹窗内错误提示 `IDENTITY_ALREADY_BOUND`，保留输入 |
| 身份字段集合不匹配 | 字段提示 `IDENTITY_KEYS_MISMATCH`，展示平台要求的 keys（如 `expert_id, tenant_id`） |
| 平台元数据缺失 | Banner 提示“平台未注册”；身份创建禁用 |
| 入口已禁用 | 下拉项显示“已禁用”，可选但提示不可用 |
| 加载失败 | 弹窗内错误 + 重试；不关闭 |
| 授权变更同步待恢复/请求超时 | 不显示成功；显示“变更状态确认中”，查授权同步状态及实际资源后刷新；不自动执行相反动作 |

> 通道列表的“密钥”列：MSSWWEB/WEBCHAT 显示“不适用”，不能显示“密钥未配置”。

> 入口选择器在入口较多时支持按名称搜索与分页；不能只靠前端一次性拉全量。

### 3.7 样式方案

- 复用现有 `detail-section-title`、`detail-hint`、`Banner`、`FormModal`、`DataTable`。
- 不新增全局 CSS；MSSWWEB 状态 Banner 用 Semi `Banner` 的 `type="warning"` / `type="info"`。
- 保持现有表单间距、按钮位置、表格列宽。

### 3.8 可访问性与兼容性

- 弹窗表单使用 `Form` 的 label/field，键盘可提交；
- 错误提示与字段关联（Semi Form 默认）；
- 不引入新浏览器 API；与现有 Console 浏览器矩阵一致。

### 3.9 现有 WebChat 必须适配的代码

当前 `services/webchatApi.ts` 的 `listConversations(entryId)` 调用入口列表；`ChatPage.tsx` 的初始化与 `conversationState.ts` 的选择逻辑都拒绝 `detail.entry_id !== currentEntry`。后端改共享列表后，这三处仍会造成“看见但打不开”，必须随本需求修复。

| 状态 | 含义与规则 |
|---|---|
| `agentId` | 由当前授权入口选择得到；共享侧栏的查询维度，校验详情仍必须匹配此 Agent |
| `sendEntryId` | 本次实际发送/新建/上传的入口，必须来自当前用户可用入口；切换入口不重写历史来源 |
| `conversationOrigin` | 详情/列表中的 `entry_id/channel`，是创建来源，可空；不用于拒绝同用户同 Agent 的会话 |
| `conversationId` | 当前显式会话；所属用户由服务端核验，前端不得用 URL 或来源字段自报身份 |
| `viewWatermarks` | 同次详情的 `{historyThroughSeq:last_seq, notificationThroughSeq:notification_through_seq}`；只对完成展示的对应视图提交已读 |

1. `webchatApi.listConversations(agentId, paging)` 改调 `GET /api/v1/webchat/conversations?agent_id=...`，不用入口维度列表。所有请求仍通过 service 层，组件/状态模块不得另写 fetch。
2. `ChatPage.applyConversation`、会话选择和 URL 恢复移除来源入口相等判断，保留 Agent 匹配、有效响应及服务端 403/404 处理；无可用来源标签不阻止加载。
3. 对来自 A 的会话，在 B 发消息仍用 `/entries/B/messages` 并显式传该 conversation_id；上传也用 B。不能把 A 回写当前入口，也不能在 URL 刷新时偷偷切换用户或 Agent。等待确认时对现有 Run resume，不能新建替代 Run。
4. 列表读取、详情/历史读取、发送及 SSE 使用可取消请求或选择代次，丢弃切换前晚到的响应。跨 Agent/用户切换时清空旧列表、详情、流、上传暂存和水位；同 Agent 换入口保留有权会话。
5. 发送入口解绑/停用时刷新可用入口：尚有同 Agent 入口时允许用户选择继续，否则禁用发送/上传并说明原因。Agent 撤权清空该 Agent 受保护视图、停止 SSE，不强制退出仍有效的 WebChat 登录；用户停用/会话撤销按认证失败处理。
6. 凭证/权限 miss 由服务端重建，前端不靠定时调用 `/identity` 或重新登录实现撤权。服务端拒绝后不得继续展示“发送成功”或把旧 optimistic 状态当持久记录。

### 3.10 已读与流式状态

- 先取详情得到 C/N，历史从第一页即传 `through_seq=C`；显示正文、当前状态及确认/结果提示成功后，才用 N 调会话 read。加载失败、标签页隐藏或详情已切换时不提交；返回零更新也是成功。
- SSE 期间不对每个增量标读。终态/待确认后取新的详情和对应历史，展示成功再提交新的 N；N 不等于 SSE id，页面不能自己推算。
- `has_unread` 只有在 read 成功后更新，再刷新共享侧栏；并发新通知仍可能使红点存在，这是正确结果。WebChat 通知中心继续使用自己的用户 feed 游标，不能拿来覆盖消息历史游标。
- 复用现有流 reducer，并检查整组 snapshot 原子应用、generation 切换与重复帧处理；重连只发送最后完整应用的 seq。半组 snapshot 到达时不得更新恢复游标；EOF 不等于完成，游标错误按历史重建流程处理。

### 3.11 来源是否展示

后台保留来源供审计、排障和投递使用。聊天主流程不强制增加标签、筛选器或新的必填项；若已有详情/提示位需要显示，可以显示“创建于 WebChat/MSSW”，未知来源显示“来源未知”或省略。不能用该标记限制资源访问，不能把会话创建来源称作当前 Run 来源。

### 3.12 自动回归建议（后续 Task 声明）

- service/contract：共享列表路径与分页、身份新增/编辑请求体、搜索参数、通知水位类型。
- component/state：跨来源会话初始化/选择/URL 刷新/续聊/上传；不允许跨 Agent，切换后旧响应不覆盖当前状态。
- state/stream：半组 snapshot 断线后重放无正文丢失，重复帧不重复追加；收到普通 EOF 不显示完成。
- 管理页：解绑成功后刷新；同步待恢复不假报成功；Agent 撤权后其他 Agent 登录可用。

---

## 4. 风险与依赖

| 风险 | 描述 | 应对 |
|------|------|------|
| FE-R01 | 入口列表跨 Agent，需要 API-19 | 后端先提供；前端空态兜底 |
| FE-R02 | 平台未配置时仍可创建入口 | Banner 明确提示；运行期由后端 503 |
| FE-R03 | `bot_id` 全局唯一冲突 | 表单捕获 `BOT_ID_EXISTS`，提示更换入口标识 |
| FE-R04 | 身份冲突/字段集合不匹配 | 捕获 `IDENTITY_ALREADY_BOUND` / `IDENTITY_KEYS_MISMATCH`，保留输入并提示 |
| FE-R05 | 编辑后 hash 变化/冲突 | 后端重算；前端只提交 JSON；冲突提示不覆盖原身份 |
| FE-R06 | grant 解绑误解为删身份 | UI 文案明确“仅移除入口授权”；身份保留 |
| FE-R07 | i18n 词条遗漏 | 新增词条同时补 zh/en；契约测试 |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| MSSWWEB | Console 中的新通道类型；平台 key `mssw` |
| 外部身份 | 用户级：一个身份 JSON → 一个 `platform_user`；可授权多个入口 |
| 入口授权（grant） | 外部身份对某个入口的授权；解绑只删授权，不删身份 |
| identity_json | 外部身份的完整 JSON；key 是 JWT 顶层 claim 名 |
| identity_keys | 平台级字段集合；绑定/编辑必须一致 |
| 入口标识 | `bot_account.bot_id`；管理员填写，BFF 不感知 |

---

*文档结束*
