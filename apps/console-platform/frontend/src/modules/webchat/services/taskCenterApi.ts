/**
 * 定时任务中心的 typed service（design frontend §3.9；TASK-026）。
 *
 * 唯一 HTTP 出口面：所有调用都走共享 `src/api/client` 的 axios 实例——语言、追踪 ID、CSRF 与
 * CHAT 会话上下文头由拦截器按 `src/api/authScope.ts` 解析的认证域自动附加。本模块**不拼装传输头**、
 * 不读 Cookie、不读/写持久存储，也不在组件里裸用 axios/fetch。
 *
 * 写操作（回答 Human、pause/resume/cancel、Draft 确认）必须由调用方持有 `Idempotency-Key`：
 * 同一逻辑动作的网络重试沿用同一个键与原参数，服务端按首次结果重放；缺参在发请求前以
 * `TaskCenterRequestError` 显式失败，不用服务端 4xx 表达前端缺参。
 *
 * 分页默认值与上限来自服务端；service 只发送调用方给出的查询参数，不复制默认常量。
 *
 * Draft 确认（API-31）是**独立的用户确认动作**：它确认的是「这条配置是否生效」，调 surface 路由
 * `/schedule-drafts/{id}/confirm`，与 Run 的 `confirmation.required`/resume 通道无关。两者共用
 * 一个词表会让客户端调错端点，所以本模块不提供任何 Run resume 能力。
 */

import { api, type ApiResponse } from '../../../api/client';
import { webchatErrorCode } from './webchatApi';
import type {
  FireHistoryPage,
  HumanRespondReceipt,
  PendingPage,
  ScheduleActionInput,
  ScheduleDetail,
  ScheduleDraftConfirmInput,
  ScheduleDraftConfirmReceipt,
  ScheduleExecutionDetail,
  ScheduleListPage,
  TaskCenterAgentQuery,
  TaskCenterBadges,
  TaskCenterClient,
  TaskCenterPendingQuery,
  TaskCenterScheduleQuery,
  WebchatPageQuery,
  WebchatRequestOptions,
  WebchatWriteOptions
} from '../types';

const TASK_CENTER_BASE = '/webchat/task-center';

/** 模块前置校验的稳定失败码；发请求前失败，避免用服务端 4xx 表达前端缺参。 */
export type TaskCenterRequestErrorCode =
  | 'SCHEDULE_ID_REQUIRED'
  | 'EXECUTION_ID_REQUIRED'
  | 'INTERACTION_ID_REQUIRED'
  | 'ARTIFACT_ID_REQUIRED'
  | 'DRAFT_ID_REQUIRED'
  | 'DRAFT_HASH_REQUIRED'
  | 'DRAFT_NAME_INVALID'
  | 'VERSION_REQUIRED'
  | 'CHECKSUM_REQUIRED'
  | 'IDEMPOTENCY_KEY_REQUIRED'
  | 'ACTION_INVALID';

/** 本地显式失败：稳定码 + 零 HTTP 副作用。 */
export class TaskCenterRequestError extends Error {
  readonly code: TaskCenterRequestErrorCode;

  constructor(code: TaskCenterRequestErrorCode) {
    super(code);
    this.name = 'TaskCenterRequestError';
    this.code = code;
  }
}

const ACTIONS = new Set<ScheduleActionInput['action']>(['pause', 'resume', 'cancel']);

function requireText(value: string | undefined, code: TaskCenterRequestErrorCode): string {
  const trimmed = value?.trim() ?? '';
  if (trimmed === '') throw new TaskCenterRequestError(code);
  return trimmed;
}

function requireKey(options: WebchatWriteOptions): string {
  return requireText(options.idempotencyKey, 'IDEMPOTENCY_KEY_REQUIRED');
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

/** 只发送调用方给出的查询参数；凭 undefined/空串占位不算“调用方给了值”。 */
function definedQuery(
  source: Record<string, string | number | undefined>
): Record<string, string | number> {
  const query: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(source)) {
    if (value === undefined || value === '') continue;
    query[key] = value;
  }
  return query;
}

/** 稳定目录码；本地缺参码原样，传输失败/未知异常有显式码（不把异常原文当业务码）。 */
export function taskCenterErrorCode(error: unknown): string {
  if (error instanceof TaskCenterRequestError) return error.code;
  return webchatErrorCode(error);
}

function schedulePath(scheduleId: string | undefined): string {
  return `${TASK_CENTER_BASE}/schedules/${requireText(scheduleId, 'SCHEDULE_ID_REQUIRED')}`;
}

/** API-12：授权 Agent 范围内的任务列表（`agent_id` 只与当前授权集合取交集）。 */
export async function listSchedules(
  query: TaskCenterScheduleQuery = {},
  options: WebchatRequestOptions = {}
): Promise<ScheduleListPage> {
  return unwrap(
    await api.get<ApiResponse<ScheduleListPage>>(`${TASK_CENTER_BASE}/schedules`, {
      params: definedQuery({
        agent_id: query.agent_id,
        status: query.status,
        mode: query.mode,
        page: query.page,
        page_size: query.page_size
      }),
      signal: options.signal
    })
  );
}

/** API-13：详情与列表同一范围；撤销授权/不存在都是 404。 */
export async function getSchedule(
  scheduleId: string,
  query: TaskCenterAgentQuery = {},
  options: WebchatRequestOptions = {}
): Promise<ScheduleDetail> {
  return unwrap(
    await api.get<ApiResponse<ScheduleDetail>>(schedulePath(scheduleId), {
      params: definedQuery({ agent_id: query.agent_id }),
      signal: options.signal
    })
  );
}

/** API-14：真实 Fire 台账 + DST 事件（不伪造 Fire）。 */
export async function listFires(
  scheduleId: string,
  query: WebchatPageQuery = {},
  options: WebchatRequestOptions = {}
): Promise<FireHistoryPage> {
  return unwrap(
    await api.get<ApiResponse<FireHistoryPage>>(`${schedulePath(scheduleId)}/fires`, {
      params: definedQuery({ page: query.page, page_size: query.page_size }),
      signal: options.signal
    })
  );
}

/** API-15：Execution 详情（结果/错误/等待类型/产物引用）。 */
export async function getExecution(
  executionId: string,
  options: WebchatRequestOptions = {}
): Promise<ScheduleExecutionDetail> {
  return unwrap(
    await api.get<ApiResponse<ScheduleExecutionDetail>>(
      `${TASK_CENTER_BASE}/executions/${requireText(executionId, 'EXECUTION_ID_REQUIRED')}`,
      { signal: options.signal }
    )
  );
}

/** API-16：待处理（HUMAN 与 NEEDS_ATTENTION 混排，统一分页）。 */
export async function listPending(
  query: TaskCenterPendingQuery = {},
  options: WebchatRequestOptions = {}
): Promise<PendingPage> {
  return unwrap(
    await api.get<ApiResponse<PendingPage>>(`${TASK_CENTER_BASE}/pending`, {
      params: definedQuery({
        agent_id: query.agent_id,
        kind: query.kind,
        page: query.page,
        page_size: query.page_size
      }),
      signal: options.signal
    })
  );
}

/** API-17：徽标（与列表/待处理同一 Agent 范围）。 */
export async function getBadges(
  query: TaskCenterAgentQuery = {},
  options: WebchatRequestOptions = {}
): Promise<TaskCenterBadges> {
  return unwrap(
    await api.get<ApiResponse<TaskCenterBadges>>(`${TASK_CENTER_BASE}/badges`, {
      params: definedQuery({ agent_id: query.agent_id }),
      signal: options.signal
    })
  );
}

/** API-18：回答 Human 提问（服务端按 input_schema 二次校验；幂等键原样交给 Owner 台账）。 */
export async function respondHuman(
  interactionId: string,
  response: Record<string, unknown>,
  options: WebchatWriteOptions
): Promise<HumanRespondReceipt> {
  return unwrap(
    await api.post<ApiResponse<HumanRespondReceipt>>(
      `${TASK_CENTER_BASE}/human-interactions/${requireText(
        interactionId,
        'INTERACTION_ID_REQUIRED'
      )}/respond`,
      { response },
      { headers: { 'Idempotency-Key': requireKey(options) }, signal: options.signal }
    )
  );
}

/** API-19：pause/resume/cancel；动作后返回生效之后的权威详情。 */
export async function scheduleAction(
  scheduleId: string,
  input: ScheduleActionInput,
  options: WebchatWriteOptions
): Promise<ScheduleDetail> {
  if (!ACTIONS.has(input.action)) throw new TaskCenterRequestError('ACTION_INVALID');
  return unwrap(
    await api.post<ApiResponse<ScheduleDetail>>(
      `${schedulePath(scheduleId)}/actions`,
      input.scope === undefined
        ? { action: input.action }
        : { action: input.action, scope: input.scope },
      { headers: { 'Idempotency-Key': requireKey(options) }, signal: options.signal }
    )
  );
}

/** API-25：按 `artifact_id + version + checksum` 下载指定版本（字节由 Owner 解析）。 */
export async function downloadArtifact(
  artifactId: string,
  version: string,
  checksum: string,
  options: WebchatRequestOptions = {}
): Promise<Blob> {
  const response = await api.get<Blob>(
    `${TASK_CENTER_BASE}/artifacts/${requireText(artifactId, 'ARTIFACT_ID_REQUIRED')}`,
    {
      responseType: 'blob',
      params: {
        version: requireText(version, 'VERSION_REQUIRED'),
        checksum: requireText(checksum, 'CHECKSUM_REQUIRED')
      },
      signal: options.signal
    }
  );
  return response.data;
}

/** API-31 → API-27：surface 侧的用户确认；`Idempotency-Key` 同时是一次用户动作的稳定引用。 */
export async function confirmScheduleDraft(
  draftId: string,
  input: ScheduleDraftConfirmInput,
  options: WebchatWriteOptions
): Promise<ScheduleDraftConfirmReceipt> {
  const body: ScheduleDraftConfirmInput = {
    draft_hash: requireText(input.draft_hash, 'DRAFT_HASH_REQUIRED')
  };
  if (input.name !== undefined) body.name = requireText(input.name, 'DRAFT_NAME_INVALID');
  return unwrap(
    await api.post<ApiResponse<ScheduleDraftConfirmReceipt>>(
      `/webchat/schedule-drafts/${requireText(draftId, 'DRAFT_ID_REQUIRED')}/confirm`,
      body,
      { headers: { 'Idempotency-Key': requireKey(options) }, signal: options.signal }
    )
  );
}

/** 构造任务中心 typed 客户端；页面只消费这个端口，不自行拼请求。 */
export function createTaskCenterClient(): TaskCenterClient {
  return {
    listSchedules,
    getSchedule,
    listFires,
    getExecution,
    listPending,
    getBadges,
    respondHuman,
    scheduleAction,
    downloadArtifact,
    confirmDraft: confirmScheduleDraft
  };
}
