# 通用 Webchat 与模型多 Key 管理前端设计

> 文档编号：FE-AGENT-CAPACITY-WEBCHAT
> 版本：v0.6
> 创建日期：2026-10-16
> 状态：v0.6 评审稿；补齐系统设置、模型资源参数及网页非敏感配置投影，明确默认值/范围/生效说明；产品实现与验收尚未执行
> 来源：需求讨论及同目录后端设计；共同使用 spec-context.yml。

## 1. 文档控制

采用 design-frontend 模板，沿用 React 18、TypeScript、Semi、react-i18next、现有路由与构建产物。聊天作为独立产品入口，不增加 Console 第十二个菜单；管理功能归现有模型、用户、Agent 模块。首期页面和可复用组件使用同一服务/状态契约，后续业务平台只接入组件与认证初始化。

## 2. 需求分析

### 2.1 目标

普通聊天用户输入 Console 为其签发的 Token 后进入聊天、选择获授权入口、发送文字/附件、查看排队与流式回复、停止/确认和重连；管理员可以配置模型多 Key、签发/重置/撤销用户 Token 和设置有效期。参考用户提供的 DeepSeek 截图：桌面左侧为新建和历史会话，右侧为所选会话消息，底部为输入框；支持打开历史并继续聊天。每次展示一个会话，企微与网页分别保持各自会话。

### 2.2 功能方案

| 功能 | 前端职责 | 来源 |
|---|---|---|
| FEAT-F01 | 单个 Token 输入框、交换独立 CHAT Cookie、刷新恢复与过期重认证，无账号密码页 | 后端 FEAT-04、D-09、Q-04 |
| FEAT-F02 | 授权入口选择与所选会话消息流 | FEAT-05/06/10 |
| FEAT-F03 | 消息幂等、SSE、游标重连和刷新恢复 | FEAT-03/05 |
| FEAT-F04 | new/cancel/resume 与记忆确认交互 | FEAT-06 |
| FEAT-F05 | 附件上传、下载与会话消息分页 | FEAT-07/10 |
| FEAT-F06 | 通知列表、未读和已读 | FEAT-07 |
| FEAT-F07 | 物理 Key 管理、模型成员绑定、权重/额度与调用状态 | FEAT-01/09 |
| FEAT-F08 | ADMIN用户Token及容量/等待/网页设置，默认值/范围/单位/生效提示复用后端元信息；网页消费非敏感配置投影 | FEAT-04/08/09、Q-04、D-11 |
| FEAT-F09 | 左侧历史会话列表、新建、选择与原会话续聊 | 后端 FEAT-10、US-07、D-10、用户截图 |

### 2.3 范围与边界

交付 `/chat/:entryId?` 独立页面，以可选查询参数 `conversation=<id>` 保存选中会话，提供带历史侧栏的 WebchatShell 和可复用 ChatPanel。有效 CHAT Cookie 可经 /me 恢复，无 Cookie 或已过期则显示 TokenAccess 的单个输入框与“进入聊天”；输入管理员交付的 Token 后建立独立 CHAT 会话，不创建用户名、密码或 Console 管理账号。本期包括会话列表、新建、查看历史与续聊；业务平台的具体 JWT/JWE、通用认证方/公钥/身份映射配置、跨域嵌入的正式配置、同屏并排多个对话不在首期。聊天组件不解释凭据，也不允许用 props.userId 绕过认证。

OPEN-01 已闭合：用户已确认 Console 签发用户 Token；默认30天可配置，CHAT Cookie 默认12小时可配置且不能超过来源Token到期时间。Cookie 到期需重新输入仍有效的 Token，Token 到期联系管理员重签；撤销/重置关联登录会话一起失效，但历史会话保留。未来业务平台认证适配复用统一主体、聊天会话和客户端，首期不先做 JWT 签发 CLI 或宿主工程。

### 2.4 验收条件

所有用户流程归本需求，下面 E2E 必须保留真实浏览器、路由、HTTP/SSE、认证 PG、Runtime/Worker；模型/企微外部服务可以使用实际协议探针，不能 mock 掉服务之间的边界。

| 场景 | 功能 | 层级 | 操作和最终可见结果 |
|---|---|---|---|
| S-F01 | F01/F02/F08 | E2E | ADMIN 为已有用户生成 Token，用户直接打开 /chat 输入后进入聊天；有效 Cookie 刷新恢复，仅见获授权入口；无需注册/密码/管理账号，访问管理 URL 被拒 |
| S-F02 | F02/F03 | E2E | 发文字和附件，用户气泡先显示待受理，202 后绑定真实 Run；模型增量显示，终态只结算一次 |
| S-F03 | F03/F05 | E2E | 断网、页面刷新及服务实例切换后恢复正确 Run/文本/附件，不重复提交、不重复显示 |
| S-F04 | F04 | E2E | 与企微同时处理不同任务，网页停止/new/确认仅影响网页当前 Run/会话 |
| S-F05 | F05/F06 | E2E | 离线 Task 完成，重开拉取持久通知，定位结果、下载附件并标记已读 |
| S-F06 | F07/F08 | E2E | 模型 Key 新增/编辑/解绑、用户 Token 签发/重置/撤销及策略设置影响对应后续请求；关闭 SideSheet 不破坏列表，BUILDER/CHAT 不显示凭据维护入口且后端拒绝越权 |
| S-F07 | 全部 | E2E | 中英文、明暗主题、桌面和窄屏下状态/时间/操作可见；消息区域滚动不盖住输入和确认 |
| E-F01 | F01/F03 | E2E | CHAT 会话过期/撤销时关闭流并清私有状态，回到 Token 输入；提示重新输入有效 Token，无效/过期则联系管理员；不跳 Console 登录、不自动保存或交换Token、不保留他人消息缓存 |
| E-F02 | F02/F03 | E2E | 未授权入口、过载、受理超时和消息幂等冲突有明确状态；重试沿用原消息ID；不显示伪成功 |
| E-F03 | F03 | E2E | 慢消费者或事件缺口触发游标恢复；取消旧连接后迟到数据不得污染新入口 |
| E-F04 | F04/F05 | E2E | 确认过期、附件拒绝/失效和下载越权有本地化反馈，其余有效内容可继续 |
| B-F01 | F03/F05 | integration | 解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序 |
| B-F02 | F02/F07/F08 | integration | 请求序号和 AbortSignal 屏蔽迟到响应；空列表、空 children 和字段边界完整显示 |
| S-F08 | F02/F03/F04 | E2E | 控制模型/工具容量触发等待，显示“已收到，正在排队/正在等待模型资源/正在执行任务”；刷新恢复，取消后停止等待，状态不显示为模型正文 |
| E-F05 | F01/F08 | E2E | 伪造/过期/撤销 Token 与停用用户不能进入聊天；重新输入有效Token后恢复原用户，先前消息不自动重提；Token不进地址/持久缓存/日志，错误不回显Token或暴露他人身份 |
| S-F09 | F01/F02/F03 | E2E | 使用正式 Console 镜像及真实入口代理/浏览器/Gateway；直接打开并刷新 `/chat/{entryId}`、加载静态资源、交换用户Token和订阅SSE均成功；渲染部署清单确认未增加 Webchat 前端 Pod/Service，CHAT 不经过 Console 管理登录守卫 |
| S-F10 | F02/F05/F09 | E2E | 真实浏览器经 API 新建 A/B；侧栏显示标题与活动分组，打开 A 看原消息后继续提问，新增 Run 仍归 A；刷新含 conversation 的 URL 恢复 A；首屏50条，可加载超过200条保留消息 |
| S-F11 | F03/F04/F09 | E2E | A 流式输出时切 B，A 在后端继续执行；切回恢复全部已持久正文和真实状态，不重复、不被 B 迟到响应覆盖；WAITING_INPUT 回到原确认操作 |
| E-F06 | F02/F04/F09 | E2E | 会话越权/其他入口、归档不可续聊、同会话正在运行或授权撤销均有准确反馈；忙碌不自动新建/换会话，切换不隐式取消 |
| B-F03 | F03/F05/F09 | integration | entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标 |
| S-F12 | F01/F03/F05 | E2E | 同浏览器退出A并输入B的Token，验证旧流/消息/草稿/图片Blob已清，先完成B认证才拉B历史；共享Cookie切换导致旧页上下文不匹配时明确停用，不能自动互相换Token循环 |
| S-F13 | F03/F06/F09 | E2E | 查看 B 时 A 完成或待确认，服务端活动通知可见且能定位 A；隐藏页暂停轮询，回前台同步；重放不重复累计未读，打开 A 不清 B 的未读 |
| E-F07 | F03/F05 | E2E | 公开流合法跳号不重连；显式游标过期才恢复详情/消息/安全快照，内部工具/推理内容不显示；原消息不重提，恢复正文不重复 |
| E-F08 | F02/F05 | E2E | 已受理消息响应丢失且上传暂存后来过期，原身份重试仍恢复原 Run 与附件；未知受理不新建消息号，不因切换会话改投 |
| E-F09 | F03/F04/F06 | E2E | 外部任务结果待核实时清楚显示状态并定位原任务，不自动重试写操作或显示伪成功；新建/切换会话不改变该结果 |
| B-F04 | F01/F03/F05 | integration | X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界 |
| S-F14 | F01/F08/F09 | E2E | 真实 Console 设置/签发和浏览器Cookie；管理员可见Token到期时间且原文仅本次显示/复制，刷新详情不能再次读取；默认30天/12小时及自定义TTL均受后端期限约束；Cookie到期重新认证或Token重签后，同用户原历史仍可继续 |
| E-F10 | F01/F08 | E2E | 两个真实浏览器登录同用户后，ADMIN重置/撤销Token，旧Token和关联Cookie拒绝访问、长流有界关闭；重置响应丢失只刷新版本、不自动再次重置，用户Token原文不进入持久store，关闭结果框即清除 |
| S-F15 | F01/F03/F05/F07/F08 | E2E | 真实系统设置API、Gateway配置投影和浏览器；调整消息/附件/分页/轮询/重连值后新操作使用同一有效配置；页面展示单位/范围/生效说明，非法值按后端details落字段；不暴露服务地址/池大小/模型Key或另存默认值 |
| E-F11 | F01/F03/F08 | E2E | 配置加载失败、设置版本冲突及进程新旧transport_revision的真实HTTP边界；失败单独可重试，不重复交换Token/发送消息；传输上限变化只影响新连接，旧流不被中途换规则，历史与已受理附件仍可读取 |
| B-F05 | F03/F05/F08 | integration | 共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文 |

1000 用户允许有界排队的口径已确认；首字、首屏和最长等待数值待容量配置与 SLO 确定，不能复制旧文档的 Go/SQLite 估算。

## 3. 前端技术设计

### 3.1 技术选型

- React/TypeScript/Semi 沿用现有依赖，不新增 UI 框架。
- API 调用放 `modules/webchat/services/`，展示与 hooks 只消费 service。共享 `src/api/` 提供传输、追踪头、错误处理与流式解析，普通请求沿用共享 axios；SSE 的 fetch/ReadableStream 只出现在共享传输层。
- 认证上下文显式区分 CONSOLE 与 CHAT；CHAT使用独立Cookie、CSRF来源、Token交换和登出动作。首次 /me 成功可恢复原生聊天会话，401显示TokenAccess；Token仅在输入与一次认证调用的内存中短暂持有，不写持久store，不自动续期。共享传输携带服务端返回的X-Chat-Context并核对响应上下文，该头不是授权凭证。展示组件只接收验证后的主体，不接收任意user-id作为可信主体；未来宿主认证初始化在独立service接入，必须先验证当前宿主身份。
- 状态使用模块级 hooks/reducer；连接资源通过 AbortController 和 generation 管理，不新增跨模块全局 store。

### 3.2 页面与路由

| 路由/位置 | 主体与布局 | 行为 |
|---|---|---|
| `/chat/:entryId?`，查询参数 `conversation=<id>` | ChatLayout，未认证显示TokenAccess，认证后历史侧栏+单会话工作区 | /me恢复或输入Token交换身份；按URL核验并打开历史会话，展示消息、输入、排队/执行状态与通知；无ID时显示开始新对话空态；URL不含Token |
| 既有 `/models` | Console 原 Shell | ADMIN管理物理Key/账号额度组，在飞上限、RPM/TPM和成员权重按资源维护，模型最大输出预算必需显式；同Key复用而容量共计，读取仅configured；降额/改额度归属提示排空，沿用列表/SideSheet关系操作 |
| 既有 `/users` | ADMIN 路由 | 用户详情凭据区域增加Webchat Token单例卡片：状态/签发/到期时间、生成/重置/撤销；原文只在成功结果框显示一次并提供复制，不新增聊天密码字段 |
| 既有 `/agents` | 原管理权限 | 入口表单支持 WEBCHAT 的稳定账号和绑定 Agent，按通道显示字段 |
| 既有 `/settings` | 原设置页，ADMIN | 增加capacity/model_pool/webchat typed分组，字段详见后端§3.7；使用既有整份文档/版本/保存协议与后端默认/范围/单位元信息；生效说明区分新受理/新Run/签发/交换/清理/新连接，不展示部署连接池等启动配置编辑框 |

聊天页面由同一前端产物提供，部署单元数量不增加；未来嵌入平台复用ChatPanel和WebchatClient，只在认证service接入实际平台JWT适配与退出生命周期，不携带管理菜单。首期用户直接在页面输入Console签发的Token，无需另建宿主或CLI。

代码统一放在现有前端工程，以下为计划新增或扩展的位置，尚未实现：

```text
apps/console-platform/frontend/src/
├── App.tsx                         # 注册独立 CHAT 路由，按路由懒加载
├── api/                            # 共享 HTTP/SSE 传输及认证上下文
└── modules/webchat/
    ├── ChatPage.tsx                 # 页面入口与初始化编排
    ├── components/                 # TokenAccess、WebchatShell、历史侧栏、ChatPanel、消息与输入
    ├── hooks/                      # 会话列表/选择、消息分页、订阅与重连
    ├── services/                   # 聊天接口、WebchatClient 与独立认证service
    └── types.ts                    # DTO、Props 和状态类型
```

`App.tsx` 的 CHAT 路由使用自己的布局与认证初始化，不放进现有 Console `RequireAuth`/`AppLayout` 管理分支。共用工程和静态产物不表示共用管理登录会话；普通聊天主体按Token交换后的独立CHAT会话授权。

**构建与生产部署**：使用现有 `apps/console-platform/frontend/package.json`、Vite 配置与构建命令，产出同一份 `dist/`。现有 `apps/console-platform/Dockerfile` 已将前端构建产物复制到 `/app/static`，由 Console Python 后端 `spa.py` 提供静态资源与 SPA 回退；Webchat 沿用这一链路。无需单独启动或部署 Webchat 前端服务，不新增前端镜像、Pod、Service 或监听端口。Node 仅用于镜像构建，生产运行进程沿用 Console 后端；本地开发也使用现有前端开发命令与端口。

外部访问复用现有站点域名，入口代理按路径分流：`/chat/*` 和静态资源到 Console，`/api/v1/webchat/auth/*` 到 Console，其余 `/api/v1/webchat/*` 到 Gateway；更具体的认证规则优先匹配，普通 Console API 保持原路由。SSE 代理配置关闭响应缓冲，并使空闲超时覆盖心跳间隔；直接刷新聊天 URL 应由 SPA 回退返回页面，不能把未知 API 回退成 HTML。后续业务平台嵌入复用组件与客户端的源码边界，不要求先部署一个独立 Webchat 网站。

### 3.3 组件设计

```text
ChatPage（身份/入口/URL 编排）
├─ TokenAccess（未认证：单个Token输入框+进入聊天）
└─ WebchatShell（认证后）
   ├─ ConversationSidebar
   │  ├─ NewConversationButton
   │  ├─ GroupedConversationList（今天/昨天/更早，按活动时间）
   │  └─ HistoryLoadMore / UserProfile
   ├─ ChatPanel（当前所选会话）
   │  ├─ ChatHeader（标题、入口、真实运行状态）
   │  ├─ MessageThread / LoadOlderMessages
   │  │  ├─ MessageBubble / AttachmentCard
   │  │  ├─ ExecutionStatus / StreamingReply
   │  │  └─ ConfirmationCard
   │  └─ ChatComposer（文字、附件、发送/停止）
   └─ NotificationPanel（未读、定位结果）
```

容器持有服务与状态，展示组件只收 typed props 和事件。WebchatShell 组合历史导航与 ChatPanel；后者接收已构造的 WebchatClient、entryId、conversationId 和 locale。身份只由 client/bootstrap 的服务结果确定，不用 props.userId 跳过认证；conversationId 只是资源选择，后端仍核属。

新会话独立装配自身对话历史；用户级长期记忆仍沿用后端原有共享作用域。消息页只展示安全投影，续聊请求不上传浏览器拼接的全量模型上下文。

“新建会话”调用 API-08，返回新的 Conversation 后更新 URL 并显示空消息区；创建请求期间防重复，网络重试沿用同一个幂等键。第一条用户消息保存后以服务端标题替换“新会话”。选择历史只调用详情/消息接口并切视图，不取消旧 Run；侧栏显示其忙碌状态，切回按 Run 恢复。普通发送必须携带当前 conversation_id；新建、历史切换和入口切换不能让正在发送的消息改投另一会话。

### 3.4 组件接口契约

| 组件/端口 | Props/参数 | 事件/返回 |
|---|---|---|
| WebchatShell | `{client,entryId,selectedConversationId,locale}` | `onConversationSelected(id)`、`onAuthenticationRequired` |
| ConversationSidebar | `{items,selectedId,loading,hasMore,creating}` | `onNew()`、`onSelect(id)`、`onLoadMore()`；只显示服务端授权范围内条目 |
| ChatPanel | `{client:WebchatClient,entryId:string,conversationId:string|null,locale:SupportedLocale}` | `onAuthenticationRequired`、`onUnreadChange`；无 ID 时先建会话再发送 |
| TokenAccess | `{submitting,errorCode,onSubmit(token:string),onClear}` | Token临时受控输入，单飞提交、防重复；完成/卸载清空，错误仅本地化提示；不写URL或持久缓存，不提供“记住Token” |
| ChatAuthenticationService | `restore(signal)/exchangeToken(token,signal)/logout(signal)` | 返回统一验证主体/会话期限/authContextId；身份冲突停自动重试；普通请求不续期，未来平台适配只改认证初始化边界 |
| ChatComposer | `{disabled,inFlight,attachmentPolicy,pendingFiles}` | `onSend({text,files})`、`onCancel(runId)`；附件使用 state，不塞未注册 Form 字段 |
| StreamingReply | `{runId,text,phase,queuedAt,queueReason,queueDeadlineAt,startedAt,connectionState}` | 按真实阶段展示，等待与执行计时分开；不显示不可靠名次/预计耗时，waiting/terminal 停止活动计时 |
| ConfirmationCard | `{runId,submissionId,sourceEventId,prompt,options,expiresAt}` | `onConfirm({selection,messageId})`；完整展示候选，不重新编号，不自行解释授权 |
| WebchatClient | `bootstrap/exchangeToken/logout/getClientConfig/listEntries/listConversations/createConversation/getConversation/submit/openEvents/cancel/resume/history/upload/download/notifications/read` | submit必须含conversationId；严格DTO/AbortSignal，认证委托独立service，后续平台适配不改变消息方法；getClientConfig消费API-19 |
| UserChatTokenCard | `{userId,metadata,onChanged}`，仅ADMIN | 单例卡片显示状态/签发/到期，生成/重置携expected_revision，撤销也携指定版本且与重置均走ConfirmAction；原文仅临时结果框显示并用CopyButton复制，关闭后清空，GET不回明文 |
| ModelCredentialForm | `{credential?,onSaved}`，仅 ADMIN | 连接/Key/全局容量/额度组；密钥仅写入，留空保留原值，替换明确提交；相同物理 Key 去重 |
| ModelDeploymentForm | `{modelId,member?,eligibleCredentials,onSaved}`，仅 ADMIN 维护关系 | 选择 credential_id 和权重，不复制或再次填写密钥；展示共享容量和绑定状态，保存后影响后续 Run |

### 3.5 状态与数据流

| 状态 | 作用域与形状 | 一致性要求 |
|---|---|---|
| principal | 页面 `{tenantId,userId,displayName,sessionState}` | 服务端来源；退出清消息/连接，不落明文令牌 |
| authentication | 页面 `{state,authMethod,authContextId,expiresAt,generation}` | /me恢复或显式Token交换后才能读取私有数据；到期停止流并清状态后显示Token输入，重新验证同用户才恢复历史；Token不存页面reducer/持久缓存，不滑动续期 |
| clientConfig | 页面 `{settingsRevision,transportRevision,webchat,transport,loadState}` | 认证后由API-19加载非敏感投影，首次成功前不开放发送/上传/订阅；不回落前端猜测默认，刷新新连接读取有效版本；退出/切主体清理 |
| entry/conversation | 页面 `{entryId,conversationId,generation}` | URL 保存所选会话 ID；入口/会话切换失效旧请求，不依赖同用户全局最近会话 |
| conversationIndex | 入口内 `{items,page,total,loading,creating}` | 按最近活动倒序并按日期分组，滚动加载；活动重排时合并去重并刷新首屏，不以列表下标决定选中项 |
| messages | 会话内 `{items,historyPage,throughSeq,scrollAnchor}` | 首屏50条，每页≤100，可继续向前加载；按 conversation/run/event 身份合并，离屏页可回收；不设200条历史总量上限 |
| activeRun | 会话内 `{runId,status,executionStatus,lastAppliedSeq,text,submissionId}` | executionStatus 保存持久阶段/排队时间/原因；delta仅追加一次，snapshot整体替换；游标在应用成功后推进 |
| connection | `{state,retries,lastError}` | 每页面/当前Run至多一条回复订阅；断流只重连GET，不再POST |
| pendingSend | `{conversationId,messageId,idempotencyKey,text,attachments,state}` | 发送发起时固定会话，切换不改投；202前显示待受理，结果不明时保留原身份和输入重试 |
| files/notifications | 组件/页面有界集合 | 上传前后都校验，前端预检不替代后端；已读以持久回执为准 |

完整流程：用户操作→hook→service→受信API→DTO/事件解析→reducer→展示。流的 EOF 不直接等同 Run 完成；收到持久终态/等待输入才改变业务状态。新入口/新Run/退出时终止旧订阅并检查 generation，迟到帧不写新状态。

公开事件是过滤后的持久流，seq跳号合法，不能按数值连续性启动补拉；只有显式游标过期或协议校验失败才执行相应恢复。checkpoint仅推进已扫描的安全水位，不追加正文；心跳不推进游标，重复delta按Run/seq去重。单帧大小来自API-19，snapshot分片按run/reply_generation/seq和part_index/part_count收集；全部到齐后才原子替换正文及推进已应用seq，缺片或断流丢弃未齐组，期间不应用跨越该组的checkpoint。重连次数/退避消费同一配置投影，终态确认后停止重连。

切换会话时中止当前视图 GET/SSE 与旧页请求，释放浏览器连接，后端 Run 继续执行；每次只订阅可见会话的活跃 Run。按会话暂存有界草稿/已应用游标，退出或切主体清理；切回先读 API-15 的权威状态及 API-09 消息页。无本地游标时从活跃 Run 起点或服务端安全快照恢复，不能把消息页 through_seq 当作已渲染过正文的证明。历史会话无活跃 Run 时发送产生新 Run；忙碌时展示原 Run，WAITING_INPUT 显式确认，终态 Run 不重新打开。

service分别映射后端API-04～11、API-14/15及API-19；控制台Key/用户Token/系统设置service映射API-01/02/03/13/16。请求头X-Locale/X-Request-Id/CSRF/X-Chat-Context来自共享传输层，Token只经exchange POST body，管理员签发响应不进通用缓存。共享客户端按CONSOLE/CHAT区分401处理：CONSOLE保留原登录跳转，CHAT关闭流并回到TokenAccess，/me探测失败不弹启动Toast；不能保留“所有401都跳Console登录”的旧分支来拦聊天用户。AUTH_CONTEXT_CHANGED停止自动重试，需用户明确重新进入，不自动互换Cookie。认证提交singleflight且响应丢失不自动发送消息；重新认证只重建GET订阅，用户选择重试原消息时沿用原幂等身份。

管理员首签/重置默认读取后端策略，不额外填写用户级TTL；页面展示实际expires_at。重置/撤销明确确认关联登录会话将失效，生成按钮有inFlight守卫，响应丢失后只GET刷新版本/状态，不能自动重置，也不能从GET恢复原文。有效期设置走既有整份platform-settings保存/版本冲突/历史语义，不建设独立认证策略管理页。

配置表单从后端groups元信息取得默认/范围/单位/生效标记，两种语言即时渲染，提交保留整份settings与revision；使用details[].path把后端跨字段错误落到字段，不静默裁剪数值。普通聊天初始化先恢复/交换身份，再加载API-19后显示可操作入口；失败显示独立配置加载重试，不反复交换Token或伪造空授权。分页默认、UTF-8文本字节上限、附件大小/数量、轮询与重连只消费clientConfig，展示组件不散落计时常量；后端仍负责最终校验。新连接绑定有效transportRevision，活跃连接使用原参数至关闭；配置变更不移动会话/延长Run或重新提交旧消息。

侧栏活动来自 API-11 的有界增量轮询，每次只订阅当前 Run；轮询带抖动且隐藏页暂停，回前台立即同步通知与会话状态。通知已读、轮询游标和消息已应用游标分别保存；未读数取服务端权威结果，不按重放帧累加。身份变化清除所有通知/历史/草稿和缓存，并撤销附件 Blob URL；私有内容不写持久浏览器缓存，下载/预览统一经带认证上下文的 service。

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| 认证初始化/Token输入 | /me骨架、交换中禁重复提交 | 单个Token输入框+进入聊天，提示向管理员获取 | Token无效或已过期，提示获取有效Token；不回显输入或暴露他人身份 | 身份和授权入口可见，输入原文清空 |
| 配置加载/编辑 | 认证后加载白名单配置，ADMIN保存禁重复 | schema给出合法默认，不自行构造空配置 | 加载失败可重试，范围/联动/版本冲突落字段 | 展示实际有效值/单位/生效时机，不把默认当容量实测 |
| 入口/历史 | 初载骨架，后台刷新保留内容 | 无授权入口/暂无消息分别展示 | 错误可重试，不伪造空成功 | 真实消息与附件 |
| 历史侧栏/新建 | 侧栏骨架、加载更多与新建中 | 尚无会话，提供新建入口 | 侧栏内重试，不清空当前有效消息 | 标题、活动分组与所选高亮 |
| 历史切换/旧消息 | 所选消息骨架、向前加载指示 | 新会话输入提示 | 原区域展示无权限/不存在/不可续聊 | 原消息、活动 Run 或可继续输入状态 |
| 发送/回复 | 待受理/排队/执行区分 | 不显示空占位助手正文 | 明确失败/恢复状态与重试动作 | 增量内容和最终结果 |
| 确认 | 提交中防重复 | 无待确认不占位 | 过期/已消费/无权限 | 用户可见确认效果 |
| 附件 | 进度与可取消 | 无附件不留空卡片 | 大小/类型/归属/失效错误 | 图片或下载卡片 |
| 通知 | 后台刷新不替换主消息流 | 无未读 | 面板内可重试，保留已有结果 | 持久结果与已读状态 |
| Key/用户Token/有效期管理 | 原列表请求竞态守卫，签发/重置禁重复 | Token未签发显示生成按钮 | 字段/版本冲突/响应不明有明确动作 | 保存后刷新；Token仅本次结果框显示，过期时间来自后端 |
| 身份变更/结果待核实 | 认证中隐藏私有内容；核实不显示虚构进度 | 需要重新输入Token或联系管理员 | 主体冲突/权限撤销/执行结果未知有明确反馈 | 只在验证主体或权威结果明确后显示内容 |

### 3.7 样式方案

沿用 Semi ConfigProvider、主题和 i18n；文案来自 zh-CN/en-US 扁平 JSON，时间复用 DateTimeText，图标来自 semi-icons。模型/用户列表使用 RemoteTable，详情使用固定共享 DetailSideSheet/DetailGrid，关系主操作位于 Tab 工具行，危险确认复用 ConfirmAction。

聊天采用截图中的交互结构，视觉沿用项目 Semi 主题：桌面为可折叠历史侧栏、居中的消息工作区与底部输入框；窄屏把侧栏收成抽屉，选择后回到会话，输入与停止/确认始终可见。会话标题截断展示、选中项高亮，日期按页面时区分组；不把侧栏做成管理表格。Markdown 禁执行 HTML/MDX，链接限定安全协议；附件名仅展示不拼路径。样式走既有 token 与独立模块样式，主题值不散落组件。

### 3.8 可访问性

Token输入/提交/复制、生成/重置/撤销、新建、历史条目、侧栏开关、输入、附件、停止、重试、确认、退出有标签并可键盘操作，Token字段默认遮蔽，所选条目有aria-current；Enter发送、Shift+Enter换行。流式区低频aria-live，不逐token播报。向前加载保持滚动锚点，用户查看旧消息时不强制滚底，有新回复提示；切换或关闭面板释放连接并恢复焦点。

## 4. 风险与依赖

| 风险 | 应对 | 验证 |
|---|---|---|
| RISK-F01 两种会话与401行为混淆 | 明确authScope，CHAT独立Cookie与TokenAccess，401不跳管理登录，不能误用Console主体 | S-F01、E-F01、E-F05 |
| RISK-F02 重连追加重复或跨入口迟到 | Run/seq身份去重、快照替换、generation守卫 | S-F03、E-F03、B-F01 |
| RISK-F03 来源确认/取消定位错误 | 真实当前Run/submission/source事件，不用用户+Agent取消最新Run | S-F04、E-F04 |
| RISK-F04 管理面 Key 配置和实际模型调用漂移 | 后端冻结版本、字段范围明确；UI展示真实成员审计，未知/缺失不猜测 | S-F06 |
| RISK-F05 组件被嵌入后认证/同源假设变化 | 独立认证service/WebchatClient统一主体，业务JWT/JWE、CORS和退出对接后续另验，不提前建设通用JWT配置 | S-F01、E-F05；外部业务集成另立需求 |
| RISK-F06 排队被误认为卡死或已经完成 | 展示持久阶段与原因，可取消；刷新/重连恢复状态，不拿连接状态推断执行结果 | S-F08、E-F02 |
| RISK-F07 历史切换后消息串写、旧 Run 被取消或漏正文 | URL/请求显式绑定会话、generation 守卫、切换仅断订阅、正确恢复 Run 游标 | S-F10/11、E-F06、B-F03 |
| RISK-F08 长列表和消息全量渲染拖慢界面 | 分页侧栏与消息、离屏页回收、滚动锚点及按页去重 | S-F10、B-F03 |
| RISK-F09 切用户但旧聊天Cookie/缓存仍有效 | 显式退出再输入Token、X-Chat-Context比对、主体变化清理与停止自动重试；未来宿主初始化须验证当前身份 | S-F12、B-F04 |
| RISK-F10 后台会话完成/待确认不可见 | 持久活动通知、前台同步及独立已读语义 | S-F13 |
| RISK-F11 过滤后的合法序号跳号被误判或恢复时泄露内部数据 | 公开 DTO 白名单、显式游标错误分类、checkpoint/重复帧处理 | E-F07、B-F04 |
| RISK-F12 Token签发原文丢失/被缓存、Cookie超出来源期限或重置后旧会话仍访问 | 一次显示/复制、结果关闭清空、后端到期权威、响应不明只刷新元信息、关联撤销 | S-F14、E-F10 |
| RISK-F13 前后端阈值漂移、启动预算被误放系统设置或旧连接被新配置破坏 | 后端schema元信息/API-19白名单唯一来源，生效标记与传输版本，snapshot分片完整后提交 | S-F15、E-F11、B-F05 |

## Spec Compliance Matrix

与后端共用Context，完整27条规则见后端矩阵；本文件承担以下前端落点。verifier使用对应Spec frontmatter的命令，下面场景是新增功能必须保留的验收。用户确认的Token入口及配置方向见后端§1.2，配置契约见后端§3.7，v0.6综合复核见后端§7；产品实现/验收尚未执行。

| Spec / Rule | enforcement | 设计影响与具体落点 | 验证场景 / verifier | 状态 |
|---|---|---|---|---|
| harness-frontend / RULE-front-001 | required | §3.1/3.2/3.5：模块service/共享传输、CHAT独立认证、API-19白名单配置与版本、分片解析，同产物聊天路由 | S-F01～03/09～15、E-F01/05/07/08/10/11、B-F01～05；harness-frontend#RULE-front-001 | 已设计；实现后同步CHAT传输约定 |
| harness-i18n / RULE-i18n-001 | required | §3.5/3.7：双语词条、统一X-Locale/API错误；设置字段/单位/生效时机与范围错误即时本地化 | S-F07/15、E-F01/02/04/11；harness-i18n#RULE-i18n-001 | 已设计 |
| harness-ui / RULE-ui-001 | required | §1/3.2/3.7：Console 固定菜单、Semi 与 RemoteTable；聊天独立路由，历史侧栏与单会话工作区自适应 | S-F06/07/10；harness-ui#RULE-ui-001 | 已设计 |
| harness-ui-detail / RULE-ui-detail-001 | required | §3.2/3.7：共享详情SideSheet、用户Token凭据卡片、ConfirmAction及一次显示/复制，列表对象动作与关系保存 | S-F06/14、E-F10、B-F02；harness-ui-detail#RULE-ui-detail-001 | 已设计 |
| harness-time / RULE-time-001 | required | §3.4/3.7：DateTimeText展示Token/会话到期时间，运行计时不取代持久状态 | S-F07/14；harness-time#RULE-time-001 | 已设计 |
| harness-test / RULE-test-001 | required | §2.4：Token及配置生效/版本/联动真实跨界验收，snapshot分片/公开游标等解析另验，不降级E2E | S-F01～15、E-F01～11、B-F01～05；harness-test#RULE-test-001 | 已设计 |
