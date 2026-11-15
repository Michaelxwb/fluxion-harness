import { api, newRequestId, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

/**
 * Console 后台任务服务的 typed 契约（scheduled-task-v2 §3.10；TASK-030）。
 *
 * 出参来自 Console 后端对 Worker Execution 真值的投影（`application/task_view.py`）：
 * v2 没有单一的 `WAITING`——外部等待（`WAITING_EXTERNAL` + `wait_kind`）与等待用户回答
 * （`WAITING_HUMAN`）语义不同，取消规则也不同；`FAILED_CONFIG` 不再存在（准入失败记在 Fire）。
 * v1 的 `intent_key`/`delivery_*` 列已退役，服务层不再声明它们。
 */

export type TaskStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'WAITING_EXTERNAL'
  | 'WAITING_HUMAN'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';
export type TriggerType = 'IMMEDIATE' | 'SCHEDULED';
export type TaskType = 'SKILL' | 'REMINDER' | 'AGENTIC' | 'BATCH';
/** `WAITING_EXTERNAL` 的细类；`WAITING_HUMAN` 不用本枚举。 */
export type WaitKind = 'ASYNC_OPERATION' | 'EXTERNAL_POLL' | 'BATCH_FANIN';
export type FailureClass = 'BUSINESS' | 'CONFIG' | 'TIMEOUT' | 'HUMAN_TIMEOUT';

export const TASK_PAGE_SIZE_MAX = 100;

export interface TaskTimelineEvent {
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  trace_id: string | null;
  create_time: string;
}

export interface TaskChild {
  task_id: string;
  status: TaskStatus;
  item_key: string | null;
  create_time: string;
}

export interface TaskListItem {
  task_id: string;
  tenant_id: string;
  agent_id: string;
  actor_user_id: string;
  schedule_id: string | null;
  source_run_id: string | null;
  /** 名称补齐（Console control.* 权威源，列表接口注入；缺失为 null，前端回落短 id）。 */
  agent_name?: string | null;
  actor_name?: string | null;
  skill_name?: string | null;
  skill_key?: string | null;
  skill_id: string | null;
  task_type: TaskType;
  status: TaskStatus;
  trigger_type: TriggerType;
  /** 计划触发时刻：只有定时触发的任务才有。 */
  scheduled_at: string | null;
  /** 只在 `WAITING_EXTERNAL` 时对外可见；其余状态下它是内部事实。 */
  wait_kind: WaitKind | null;
  wait_payload: Record<string, unknown> | null;
  input: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  failure_class: FailureClass | null;
  attempt: number;
  max_attempts: number;
  cancel_requested: boolean;
  deadline_at: string;
  not_before: string;
  create_time: string;
  update_time: string;
  started_at: string | null;
  finished_at: string | null;
  /** 批量任务的子任务进度；无子任务为 0/0（由后端一次 GROUP BY 取回，不逐行查询）。 */
  child_total: number;
  child_finished: number;
}

/** 白名单快照摘要：原始 `execution_snapshot` 不出后端（内部存储键不外泄）。 */
export interface TaskSnapshotSummary {
  schema_version: number | null;
  hash: string | null;
  mode: string | null;
  allowlist: string[];
  memory_mode: string | null;
  budget: Record<string, unknown> | null;
}

/** 最近一次 Human 提问；`open` 以 Execution 状态为准。 */
export interface TaskHumanSummary {
  interaction_id: string | null;
  question: string | null;
  deadline_at: string | null;
  open: boolean;
}

export interface TaskDetail extends TaskListItem {
  parent_id: string | null;
  root_id: string | null;
  item_key: string | null;
  result_artifact_id: string | null;
  snapshot: TaskSnapshotSummary;
  human: TaskHumanSummary | null;
  timeline: TaskTimelineEvent[];
  children: TaskChild[];
}

export interface TaskListParams {
  schedule_id?: string;
  agent_id?: string;
  actor_user_id?: string;
  skill_id?: string;
  status?: TaskStatus;
  trigger_type?: TriggerType;
  start_time?: string;
  end_time?: string;
  deadline_from?: string;
  deadline_to?: string;
  page?: number;
  page_size?: number;
}

export interface CancelTaskResult {
  task_id: string;
  status: TaskStatus;
  cancel_requested: boolean;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

function normalizeParams(params: TaskListParams): TaskListParams {
  const pageSize = params.page_size ?? 20;
  return {
    ...params,
    page: params.page ?? 1,
    page_size: Math.min(pageSize, TASK_PAGE_SIZE_MAX)
  };
}

export async function listTasks(params: TaskListParams = {}): Promise<Page<TaskListItem>> {
  return unwrap(
    await api.get<ApiResponse<Page<TaskListItem>>>('/tasks', { params: normalizeParams(params) })
  );
}

export async function getTask(id: string): Promise<TaskDetail> {
  return unwrap(await api.get<ApiResponse<TaskDetail>>(`/tasks/${id}`));
}

export async function cancelTask(id: string): Promise<CancelTaskResult> {
  return unwrap(
    await api.post<ApiResponse<CancelTaskResult>>(`/tasks/${id}/cancel`, undefined, {
      headers: { 'X-Request-Id': newRequestId() }
    })
  );
}
