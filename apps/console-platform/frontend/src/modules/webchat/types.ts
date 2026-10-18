/**
 * Webchat 模块的 DTO、请求输入与 `WebchatClient` 端口（design frontend §3.1/§3.4/§3.5）。
 *
 * 字段名与 `muad_contracts.webchat`（TASK-005）逐一对应，全部保持后端 snake_case：wire 形状只有
 * 一处事实，前端不重命名、不补充后端未承诺的字段、不用 `any` 兜底。时间字段一律是后端返回的
 * ISO-8601 字符串，不在前端做时区推断或补零。
 *
 * 本文件只放类型；HTTP 实现见 `services/webchatApi.ts`，展示组件与 hooks 只消费该 service。
 */

/** CHAT 认证方式；首期只有 Console 签发的用户 Token 交换（design §3.4 API-04）。 */
export type ChatAuthMethod = 'USER_TOKEN';

/** Run 持久状态（`muad_contracts.enums.RunStatus`）。 */
export type RunStatus =
  | 'CREATED'
  | 'RUNNING'
  | 'WAITING_TOOL'
  | 'WAITING_INPUT'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

/** 取消请求已受理但尚未落地时的展示状态（Runtime `display_run_status`）。 */
export type WebchatRunDisplayStatus = RunStatus | 'CANCELLING';

/** 会话可携带的活跃 Run 状态；终态 Run 不重新打开（design §3.5）。 */
export type ActiveRunStatus = 'CREATED' | 'RUNNING' | 'WAITING_TOOL' | 'WAITING_INPUT';

/** 执行阶段；排队等待与执行计时分开（design §3.6）。 */
export type ExecutionStage =
  | 'WAITING_RUNTIME'
  | 'WAITING_MODEL'
  | 'RUNNING'
  | 'WAITING_TOOL'
  | 'WAITING_INPUT'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

export type ConversationStatus = 'ACTIVE' | 'ARCHIVED';

/** 附件种类（`muad_contracts.channel.AttachmentKind`）。 */
export type AttachmentKind = 'IMAGE' | 'DOCUMENT' | 'OTHER';

/** 用户通知种类；Run 完成/待确认与 Worker Task 结果共用同一 feed。 */
export type WebchatNotificationKind = 'RUN_COMPLETED' | 'RUN_WAITING_INPUT' | 'TASK_RESULT';

export type WebchatMessageRole = 'USER' | 'ASSISTANT';

/** 模块请求前置校验的稳定失败码；发请求前失败，避免用服务端 4xx 表达前端缺参。 */
export type WebchatRequestErrorCode =
  | 'TOKEN_REQUIRED'
  | 'ENTRY_REQUIRED'
  | 'CONVERSATION_REQUIRED'
  | 'RUN_REQUIRED'
  | 'ARTIFACT_REQUIRED'
  | 'NOTIFICATION_REQUIRED'
  | 'MESSAGE_ID_REQUIRED'
  | 'MESSAGE_EMPTY'
  | 'IDEMPOTENCY_KEY_REQUIRED'
  | 'SUBMISSION_REQUIRED'
  | 'SOURCE_EVENT_REQUIRED'
  | 'SELECTION_REQUIRED';

/** 后端统一列表封套（API-05/09/11/14）。 */
export interface WebchatPage<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

/** API-04 交换/恢复的统一主体七字段（`WebchatSessionInfo`）。 */
export interface WebchatSessionInfo {
  tenant_id: string;
  user_id: string;
  display_name: string;
  session_id: string;
  expires_at: string;
  auth_context_id: string;
  auth_method: ChatAuthMethod;
}

/** API-04 登出回执：只撤本 CHAT 会话，不撤销来源 Token。 */
export interface WebchatLogoutReceipt {
  logged_out: true;
}

/** API-05 入口：仅当前用户获授权的 Agent 入口，不含指令/模型/授权明细。 */
export interface WebchatEntry {
  entry_id: string;
  agent_id: string;
  display_name: string;
}

export interface WebchatEntryPage extends WebchatPage<WebchatEntry> {}

/** 排队/执行状态：`reason` 与队列时间来自持久阶段，前端不推算名次或预计耗时。 */
export interface WebchatExecutionStatus {
  stage: ExecutionStage;
  reason: string | null;
  queued_at: string | null;
  deadline_at: string | null;
  started_at: string | null;
}

export interface WebchatConfirmationOption {
  value: string;
  label: string;
}

/** 待确认引用：完整候选 + 原 submission/source，前端不重新编号、不自行解释授权。 */
export interface WebchatConfirmation {
  submission_id: string;
  source_event_id: string;
  prompt: string;
  options: WebchatConfirmationOption[];
  expires_at: string;
}

/** 会话详情里的活跃 Run（API-15）；终态 Run 不出现在这里。 */
export interface WebchatActiveRun {
  id: string;
  status: ActiveRunStatus;
  execution_status: WebchatExecutionStatus;
  confirmation: WebchatConfirmation | null;
}

/** API-08 新建回执：身份与标题由服务端持有，空会话水位固定为 0。 */
export interface WebchatConversationCreated {
  conversation_id: string;
  entry_id: string;
  title: string;
  status: 'ACTIVE';
  last_seq: 0;
  active_run: null;
}

/** API-14 会话索引项；活跃状态批量关联，不按列表下标决定选中项。 */
export interface WebchatConversationItem {
  conversation_id: string;
  title: string;
  create_time: string;
  last_message_at: string;
  last_run_id: string | null;
  status: ConversationStatus;
  active_run_status: ActiveRunStatus | null;
}

export interface WebchatConversationPage extends WebchatPage<WebchatConversationItem> {}

/** API-15 会话详情：打开历史后按活跃 Run 恢复订阅。 */
export interface WebchatConversationDetail {
  conversation_id: string;
  entry_id: string;
  agent_id: string;
  title: string;
  status: ConversationStatus;
  last_seq: number;
  last_run_id: string | null;
  active_run: WebchatActiveRun | null;
}

/** API-06 提交回执（202）：新 Run 的真实身份与持久阶段。 */
export interface WebchatMessageAccepted {
  conversation_id: string;
  run_id: string;
  status: RunStatus;
  execution_status: WebchatExecutionStatus;
}

/** 附件权威元信息（上传登记与下载共用的白名单投影）。 */
export interface WebchatFileMetadata {
  kind: AttachmentKind;
  media_type: string;
  size: number;
  filename: string | null;
  checksum: string;
}

/** API-10 上传登记回执：只返回 `upload_id`，不伪造提前存在的 `artifact_id`。 */
export interface WebchatUpload extends WebchatFileMetadata {
  upload_id: string;
  expires_at: string;
}

/** 历史消息里的制品引用：是否仍可取件由服务端 `available` 决定。 */
export interface WebchatAttachment extends WebchatFileMetadata {
  artifact_id: string;
  available: boolean;
}

/** API-09 历史消息（安全公开投影）。 */
export interface WebchatHistoryMessage {
  event_id: string;
  seq: number;
  run_id: string;
  role: WebchatMessageRole;
  text: string;
  attachments: WebchatAttachment[];
}

/** API-09 消息页：`through_seq` 是冻结水位，不能当作活跃 Run 已应用游标的证明。 */
export interface WebchatMessagePage extends WebchatPage<WebchatHistoryMessage> {
  through_seq: number;
}

/** API-11 用户通知（安全摘要 + 引用）。 */
export interface WebchatNotification {
  id: string;
  seq: number;
  entry_id: string;
  agent_id: string;
  conversation_id: string;
  run_id: string;
  task_id: string | null;
  event_id: string | null;
  kind: WebchatNotificationKind;
  summary: string;
  attachments: WebchatAttachment[];
  created_at: string;
  read_at: string | null;
}

/** API-11 通知页：`next_seq` 是扫描水位，与已读、消息已应用游标分别保存。 */
export interface WebchatNotificationPage extends WebchatPage<WebchatNotification> {
  through_seq: number;
  next_seq: number;
  unread_count: number;
}

/** API-11 已读回执：已读以持久回执为准，不按重放帧累加。 */
export interface WebchatNotificationReadReceipt {
  id: string;
  read_at: string;
}

/** API-08 取消回执；cancel 可重放，重复提交不产生第二次副作用。 */
export interface WebchatRunCancelResult {
  run_id: string;
  status: WebchatRunDisplayStatus;
  cancel_requested: boolean;
}

/** API-08 恢复回执：原 Run 继续执行后的真实阶段（不伪造进度）。 */
export interface WebchatResumeReceipt {
  run_id: string;
  submission_id: string;
  status: RunStatus;
  execution_status: WebchatExecutionStatus;
}

/** API-19 浏览器可见的业务上限（唯一来源是后端 schema 派生投影）。 */
export interface WebchatClientLimits {
  history_page_size: number;
  text_max_bytes: number;
  upload_max_bytes: number;
  upload_max_per_message: number;
  notification_poll_sec: number;
}

/** API-19 传输参数；新连接绑定 `transport_revision`，活跃连接不突变。 */
export interface WebchatClientTransport {
  sse_event_max_bytes: number;
  sse_heartbeat_sec: number;
  reconnect_base_ms: number;
  reconnect_cap_ms: number;
  reconnect_max_attempts: number;
}

/** API-19 非敏感配置投影：不含服务地址、连接池、模型额度或密钥。 */
export interface WebchatClientConfig {
  settings_revision: number;
  transport_revision: string;
  webchat: WebchatClientLimits;
  transport: WebchatClientTransport;
}

/** 列表分页查询；默认值由后端/clientConfig 决定，前端不复制默认常量。 */
export interface WebchatPageQuery {
  page?: number;
  page_size?: number;
}

/** API-09 历史查询：`page > 1` 必须沿用冻结水位。 */
export interface WebchatHistoryQuery extends WebchatPageQuery {
  through_seq?: number;
}

/** API-11 增量查询：`after_seq` 取上一次响应的 `next_seq`。 */
export interface WebchatNotificationQuery extends WebchatPageQuery {
  after_seq?: number;
}

/** API-06 提交体；`conversation_id` 必填——新建、历史切换与入口切换不得改投另一会话。 */
export interface WebchatSubmitInput {
  conversation_id: string;
  message_id: string;
  text?: string;
  upload_ids?: string[];
}

/** API-08 恢复体：显式绑定 WAITING_INPUT 的原 Run/submission/source。 */
export interface WebchatResumeInput {
  submission_id: string;
  source_event_id: string;
  message_id: string;
  selection: string;
}

/** 连接资源经 AbortController 管理：调用方持有信号，service 原样透传。 */
export interface WebchatRequestOptions {
  signal?: AbortSignal;
}

/** 写操作额外携带调用方持有的幂等键（网络重试沿用同一个键与原身份）。 */
export interface WebchatWriteOptions extends WebchatRequestOptions {
  idempotencyKey: string;
}

/**
 * design §3.4 的 WebchatClient 端口：模块 service 的实现面。
 *
 * 认证委托独立 service（TASK-074）；入口/会话/消息/附件/通知/控制方法在后续平台适配中保持不变。
 * `openEvents`（回复订阅）属共享流层（TASK-072 `src/api/stream.ts`），由 TASK-082 接入，不在本任务伪造。
 */
export interface WebchatClient {
  bootstrap(options?: WebchatRequestOptions): Promise<WebchatSessionInfo>;
  exchangeToken(token: string, options?: WebchatRequestOptions): Promise<WebchatSessionInfo>;
  logout(options?: WebchatRequestOptions): Promise<WebchatLogoutReceipt>;
  getClientConfig(options?: WebchatRequestOptions): Promise<WebchatClientConfig>;
  listEntries(options?: WebchatRequestOptions): Promise<WebchatEntryPage>;
  listConversations(
    entryId: string,
    query?: WebchatPageQuery,
    options?: WebchatRequestOptions
  ): Promise<WebchatConversationPage>;
  createConversation(
    entryId: string,
    idempotencyKey: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatConversationCreated>;
  getConversation(
    conversationId: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatConversationDetail>;
  submit(
    entryId: string,
    input: WebchatSubmitInput,
    options: WebchatWriteOptions
  ): Promise<WebchatMessageAccepted>;
  cancel(runId: string, options?: WebchatRequestOptions): Promise<WebchatRunCancelResult>;
  resume(
    runId: string,
    input: WebchatResumeInput,
    options?: WebchatRequestOptions
  ): Promise<WebchatResumeReceipt>;
  history(
    conversationId: string,
    query?: WebchatHistoryQuery,
    options?: WebchatRequestOptions
  ): Promise<WebchatMessagePage>;
  upload(entryId: string, file: Blob, options?: WebchatRequestOptions): Promise<WebchatUpload>;
  download(artifactId: string, options?: WebchatRequestOptions): Promise<Blob>;
  notifications(
    query?: WebchatNotificationQuery,
    options?: WebchatRequestOptions
  ): Promise<WebchatNotificationPage>;
  read(
    notificationId: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatNotificationReadReceipt>;
}
