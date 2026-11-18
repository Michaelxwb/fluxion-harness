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
  | 'CHECKSUM_REQUIRED'
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

/** 工具执行进度（公开边只带工具名/阶段/终态，不含参数与结果）。 */
export interface WebchatToolProgress {
  name: string;
  phase: 'STARTED' | 'FINISHED';
  status: 'OK' | 'ERROR' | null;
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

/** API-16 删除回执：`deleted` 恒为 true（重复删除同形，幂等由契约保证）。 */
export interface WebchatConversationDeletion {
  conversation_id: string;
  deleted: true;
}

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
  /**
   * 定时任务 Draft 卡片（`schedule.draft.ready` 的持久投影）：它不是普通消息，但必须以同一条
   * 持久事实回到历史里，刷新/重连后仍可确认。普通消息线格式不带这个键。
   */
  draft?: WebchatScheduleDraftReady | null;
  /**
   * 卡片的确认状态（服务端按 Owner Schema 只读投影出来）：刷新后直接据它渲染。
   * 缺省/未知一律按「不可确认」处理——不得当成 PENDING。
   */
  draft_status?: ScheduleDraftStatus | null;
  /** canonical 事件落库时间（消息发送/接收的权威时刻）。 */
  created_at: string;
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

/** 一个可上传格式的浏览器投影：媒体类型 + 用户可见扩展名（含点）。 */
export interface WebchatUploadFormat {
  readonly media_type: string;
  readonly extensions: readonly string[];
}

/** API-19 浏览器可见的业务上限（唯一来源是后端 schema 派生投影）。
 * `upload_formats` 与门控同一份清单，提示与文件选择器都只认它。 */
export interface WebchatClientLimits {
  history_page_size: number;
  text_max_bytes: number;
  upload_max_bytes: number;
  upload_max_per_message: number;
  notification_poll_sec: number;
  upload_formats: readonly WebchatUploadFormat[];
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

/** API-10 取件对象：目标会话（授权按它裁决）+ 卡片上的版本（同一产物可能发布过多次）。 */
export interface WebchatAttachmentQuery {
  conversationId: string;
  checksum: string;
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
  deleteConversation(
    conversationId: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatConversationDeletion>;
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
  download(
    artifactId: string,
    query: WebchatAttachmentQuery,
    options?: WebchatRequestOptions
  ): Promise<Blob>;
  notifications(
    query?: WebchatNotificationQuery,
    options?: WebchatRequestOptions
  ): Promise<WebchatNotificationPage>;
  read(
    notificationId: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatNotificationReadReceipt>;
}

// ---------------------------------------------------------------------------------------------
// 定时任务中心与 Draft 确认卡片（scheduled-task-v2 frontend design §3.4、§3.9；TASK-026）
//
// 字段名与 `muad_contracts.scheduling`/`webchat` 的服务端 wire 形状逐一对应，保持 snake_case；
// 时间一律是 ISO-8601 字符串，由 `DateTimeText` 转本地展示，前端不做时区推断。
// ---------------------------------------------------------------------------------------------

export type ScheduleMode = 'REMINDER' | 'SKILL' | 'AGENTIC';
export type ScheduleStatus = 'ACTIVE' | 'PAUSED' | 'NEEDS_ATTENTION' | 'ENDED' | 'CANCELLED';
export type CancelScope = 'FUTURE_ONLY' | 'ALL_ACTIVE';
export type ScheduleDraftKind = 'CREATE' | 'UPDATE';
export type DeliveryIntent = 'PUSH' | 'NONE';
export type FireStatus =
  | 'CREATED'
  | 'EXECUTION_CREATED'
  | 'SKIPPED'
  | 'FAILED_ADMISSION'
  | 'CANCELLED';
export type ScheduleExecutionStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'WAITING_EXTERNAL'
  | 'WAITING_HUMAN'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';
export type ScheduleWaitKind = 'ASYNC_OPERATION' | 'EXTERNAL_POLL' | 'BATCH_FANIN';

/** 后台任务类型（`muad_contracts.enums.TaskType`）；BATCH 只在一次执行内部扇出。 */
export type TaskType = 'SKILL' | 'REMINDER' | 'AGENTIC' | 'BATCH';

/** 逻辑结果计数；部分失败由父任务状态与 `BEST_EFFORT` 共同表达。 */
export interface TaskResultCounts {
  total: number;
  succeeded: number;
  failed: number;
  cancelled: number;
}

/** UPDATE 预览的一项前后差异（`field` 是载荷里的顶层键，值原样取自 Revision 与 Draft）。 */
export interface ScheduleDiffEntry {
  field: string;
  before: unknown;
  after: unknown;
}

/** 试跑的真实产物引用（与任务中心同一份 `TaskArtifactRef` 公开字段）。 */
export interface ScheduleTrialArtifact {
  artifact_id: string;
  name: string;
  media_type: string;
  size: number;
  checksum: string;
  version: string;
}

/**
 * 一次真实试跑的事实（Draft 卡片展示）。
 *
 * 它不是配置：不属于 `ScheduleJob`，也不进入确认后冻结的 Revision；只是卡片上的执行事实。
 */
export interface ScheduleTrialFact {
  execution_id: string;
  status: ScheduleExecutionStatus;
  error_code: string | null;
  repair: string | null;
  repair_hint: string | null;
  result_summary: string | null;
  artifacts: ScheduleTrialArtifact[];
  started_at: string | null;
  finished_at: string | null;
}

/** 确认卡片事实：做什么、何时（含 IANA 时区）、结果去哪看、依赖（AGENTIC 即工具 allowlist）、预算。 */
export interface SchedulePreview {
  mode: ScheduleMode;
  payload: Record<string, unknown> & { kind: 'REMINDER' | 'SKILL_PLAN' | 'AGENT_TURN' };
  session_target: 'isolated' | 'current' | 'main' | `session:${string}`;
  what: string;
  when: string;
  first_fire_at: string;
  delivery: DeliveryIntent;
  push_target_label?: string | null;
  tools_allow: string[];
  /** `PLATFORM` = 平台设置；`NONE` = 该 payload 不消费模型预算。 */
  budget_source: 'PLATFORM' | 'NONE';
  dependencies: string[];
  budget: Record<string, unknown>;
  /** 只有 UPDATE Draft 才有：CREATE 没有「之前」，空列表就是没有旧配置可比。 */
  diff: ScheduleDiffEntry[];
  /** 真的跑过一次才有：真实执行 id/状态/结果/产物，不是渲染预览。 */
  trial?: ScheduleTrialFact | null;
}

/**
 * `schedule.draft.ready` 的卡片公开 DTO。
 *
 * 它**不是** Run 的 `confirmation.required`：后者绑定同一次 Run 的 interrupt + resume，
 * 确认的是「这个 Run 是否继续」；这张卡片确认的是「这条配置是否生效」，按钮调 surface 的
 * Draft 确认端点（API-31 → API-27），与 Run 生命周期无关。
 */
export interface WebchatScheduleDraftReady {
  draft_id: string;
  draft_hash: string;
  kind: ScheduleDraftKind;
  mode: ScheduleMode;
  preview: SchedulePreview;
  expires_at: string;
}

/** API-27 / API-31 确认请求：`name` 只在 CREATE 时生效，缺省由服务端从 instruction 派生。 */
export interface ScheduleDraftConfirmInput {
  draft_hash: string;
  name?: string;
}

/** Draft 的确认状态：历史卡片刷新后据此决定还能不能确认。 */
export type ScheduleDraftStatus = 'PENDING' | 'CONFIRMED' | 'CANCELLED' | 'EXPIRED' | 'REJECTED';

/** API-27 确认回执：确认即生效后的真实 Schedule 事实。 */
export interface ScheduleDraftConfirmReceipt {
  schedule_id: string;
  status: ScheduleStatus;
  revision_no: number;
  config_version: number;
  next_fire_at: string | null;
}

export interface ScheduleLastFire {
  scheduled_at: string;
  status: FireStatus;
  execution_status: ScheduleExecutionStatus | null;
  missed_count: number;
  dst_resolution: string | null;
}

export interface ScheduleListItem {
  schedule_id: string;
  agent_id: string;
  name: string;
  mode: ScheduleMode;
  status: ScheduleStatus;
  timezone: string;
  config_version: number;
  next_fire_at: string | null;
  last_fire: ScheduleLastFire | null;
  needs_attention: boolean;
  pending_human: boolean;
}

export interface ScheduleListPage extends WebchatPage<ScheduleListItem> {}

/** `ENDED` 的结束原因：次级事实，展示为标签，不当作失败。 */
export type ScheduleEndReason =
  | 'COMPLETED'
  | 'MISSED'
  | 'EXHAUSTED'
  | 'ADMISSION_FAILED'
  | 'USER_CANCELLED';

/**
 * 详情 = 列表行 + Revision 事实。`instruction`/`normalized_goal`/`context` 是服务端返回的
 * 内部配置（Prompt 与参数），页面不得展示；页面只用公开摘要、依赖与策略字段。
 */
export interface ScheduleDetail extends ScheduleListItem {
  revision_no: number;
  instruction: string;
  normalized_goal: string;
  context: Record<string, unknown>;
  dependencies: string[];
  delivery_intent: DeliveryIntent;
  policy: Record<string, unknown>;
  budget: Record<string, unknown> | null;
  memory_mode: 'READ_ONLY' | null;
  end_reason: ScheduleEndReason | null;
  attention_reason: string | null;
}

export interface HumanPendingItem {
  kind: 'HUMAN';
  interaction_id: string;
  schedule_id: string;
  execution_id: string;
  schedule_name: string;
  question: string;
  deadline_at: string;
  input_schema: Record<string, unknown> | null;
}

export interface AttentionPendingItem {
  kind: 'NEEDS_ATTENTION';
  schedule_id: string;
  schedule_name: string;
  reason: string;
  action: string;
}

export type PendingItem = HumanPendingItem | AttentionPendingItem;

export interface PendingPage extends WebchatPage<PendingItem> {}

export interface TaskCenterBadges {
  pending_human: number;
  needs_attention: number;
}

export interface FireItem {
  fire_id: string;
  scheduled_at: string;
  status: FireStatus;
  skip_reason: string | null;
  missed_count: number;
  dst_resolution: string | null;
  admission_error_code: string | null;
  execution_id: string | null;
  execution_status: ScheduleExecutionStatus | null;
}

export interface DstEvent {
  event_id: string;
  event_type: 'DST_GAP_SKIPPED' | 'DST_FOLD_SECOND_SKIPPED';
  local_date: string;
  local_time: string;
  timezone: string;
  occurred_at: string;
}

/** 历史保留口径：空页不等于没发生过——早于 `cleaned_before` 的 Fire 可能已按策略回收。 */
export interface HistoryRetention {
  fire_history_days: number;
  execution_history_days: number;
  cleaned_before: string;
  oldest_retained_at: string | null;
}

export interface FireHistoryPage extends WebchatPage<FireItem> {
  dst_events: DstEvent[];
  retention: HistoryRetention | null;
}

export interface TaskArtifactDelivery {
  target_key: string;
  /** 安全标签（如“企微”）；UI 不得展示 target_key 里的内部 ID。 */
  target_label?: string | null;
  status: 'PENDING' | 'DELIVERED' | 'DEGRADED' | 'UNKNOWN' | 'FAILED';
  error_code: string | null;
  retryable: boolean;
  fallback_url: string | null;
}

export interface TaskArtifact {
  artifact_id: string;
  name: string;
  media_type: string;
  size: number;
  checksum: string;
  version: string;
  /**
   * 每个投递目标一项；空数组表示未请求推送（NONE）。
   * 字段缺失表示统一 Artifact 投递契约尚未提供——隐藏徽标，不伪造 `PENDING`。
   */
  deliveries?: TaskArtifactDelivery[];
}

/** 后台任务列表项（`TaskCenterTaskItem`）；不含结果正文，只给摘要事实。 */
export interface TaskCenterTaskItem {
  task_id: string;
  agent_id: string;
  task_type: TaskType;
  skill_name: string;
  status: ScheduleExecutionStatus;
  counts: TaskResultCounts;
  partial_failure: boolean;
  child_total: number;
  child_finished: number;
  has_artifacts: boolean;
  create_time: string;
  finished_at: string | null;
}

export interface TaskCenterTaskPage extends WebchatPage<TaskCenterTaskItem> {}

/** 子任务行：失败摘要只给首行（≤200），完整原文要打开该子任务的执行详情。 */
export interface TaskChildItem {
  task_id: string;
  item_key: string | null;
  label: string;
  status: ScheduleExecutionStatus;
  error_code: string | null;
  error_message_summary: string | null;
  artifact_count: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface TaskChildPage extends WebchatPage<TaskChildItem> {}

/** 后台与定时任务详情共用的执行事实；后台专属字段对旧读面可缺省。 */
export interface ExecutionDetail {
  execution_id: string;
  agent_id: string | null;
  task_type: TaskType;
  skill_name: string;
  parent_id: string | null;
  item_key: string | null;
  status: ScheduleExecutionStatus;
  wait_kind: ScheduleWaitKind | null;
  wait_payload: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  result_artifacts: TaskArtifact[];
  artifact_total: number;
  counts: TaskResultCounts;
  partial_failure: boolean;
  children: TaskChildPage;
  error_code: string | null;
  error_message: string | null;
  failure_class: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** 冻结 Snapshot 的摘要事实（schema 版本与字节数）；明细不对外。 */
  snapshot_schema_version: number;
  snapshot_bytes: number;
}

/** API-18 回答回执：服务端按 `input_schema` 二次校验后的真实状态。 */
export interface HumanRespondReceipt {
  interaction_id: string;
  status: string;
  execution_id: string;
  execution_status: string;
}

/** 任务中心列表查询；默认值与上限由服务端枚举，前端不复制默认常量。 */
export interface TaskCenterScheduleQuery extends WebchatPageQuery {
  agent_id?: string;
  status?: ScheduleStatus;
  mode?: ScheduleMode;
}

/** 后台任务列表查询：只给任务记录摘要，过滤由后端按授权 Agent 集合取交集。 */
export interface TaskCenterTaskQuery extends WebchatPageQuery {
  agent_id?: string;
  status?: ScheduleExecutionStatus;
}

/** 执行详情查询：子任务分页复用同一详情路由，不另开 children 端点。 */
export interface TaskCenterExecutionQuery {
  children_page?: number;
  children_page_size?: number;
}

/** 待处理查询：`kind` 缺省时 HUMAN 与 NEEDS_ATTENTION 混排。 */
export interface TaskCenterPendingQuery extends WebchatPageQuery {
  agent_id?: string;
  kind?: 'HUMAN' | 'NEEDS_ATTENTION';
}

export interface TaskCenterAgentQuery {
  agent_id?: string;
}

/** 任务动作请求（API-19）；只有 cancel 有范围。 */
export interface ScheduleActionInput {
  action: 'pause' | 'resume' | 'cancel';
  scope?: CancelScope;
}

/**
 * 任务中心 typed 客户端（design §3.9）：模块 service 的唯一 HTTP 出口面。
 *
 * 只经共享 `src/api/client` 发请求：语言/追踪/CSRF/CHAT 上下文由拦截器注入，service 不拼头、
 * 不读 Cookie；写操作必须由调用方持有 `Idempotency-Key`（重试沿用同一个键）。
 */
export interface TaskCenterClient {
  listSchedules(
    query?: TaskCenterScheduleQuery,
    options?: WebchatRequestOptions
  ): Promise<ScheduleListPage>;
  listTasks(
    query?: TaskCenterTaskQuery,
    options?: WebchatRequestOptions
  ): Promise<TaskCenterTaskPage>;
  getSchedule(
    scheduleId: string,
    query?: TaskCenterAgentQuery,
    options?: WebchatRequestOptions
  ): Promise<ScheduleDetail>;
  listFires(
    scheduleId: string,
    query?: WebchatPageQuery,
    options?: WebchatRequestOptions
  ): Promise<FireHistoryPage>;
  getExecution(
    executionId: string,
    query?: TaskCenterExecutionQuery,
    options?: WebchatRequestOptions
  ): Promise<ExecutionDetail>;
  listPending(
    query?: TaskCenterPendingQuery,
    options?: WebchatRequestOptions
  ): Promise<PendingPage>;
  getBadges(
    query?: TaskCenterAgentQuery,
    options?: WebchatRequestOptions
  ): Promise<TaskCenterBadges>;
  respondHuman(
    interactionId: string,
    response: Record<string, unknown>,
    options: WebchatWriteOptions
  ): Promise<HumanRespondReceipt>;
  scheduleAction(
    scheduleId: string,
    input: ScheduleActionInput,
    options: WebchatWriteOptions
  ): Promise<ScheduleDetail>;
  downloadArtifact(
    artifactId: string,
    version: string,
    checksum: string,
    options?: WebchatRequestOptions
  ): Promise<Blob>;
  /** API-31：surface 侧的用户确认（带可信 actor 与确认来源），不走 Run resume。 */
  confirmDraft(
    draftId: string,
    input: ScheduleDraftConfirmInput,
    options: WebchatWriteOptions
  ): Promise<ScheduleDraftConfirmReceipt>;
}
