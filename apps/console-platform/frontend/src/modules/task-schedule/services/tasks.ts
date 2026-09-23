import { api, newRequestId, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

export type TaskStatus = 'QUEUED' | 'RUNNING' | 'WAITING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
export type TriggerType = 'IMMEDIATE' | 'SCHEDULED';
export type TaskType = 'SKILL' | 'BATCH';
export type DeliveryStatus = 'PENDING' | 'SENT' | 'FAILED' | 'NONE';
export type DeliveryMode = 'FINAL_ONLY' | 'NONE';

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
  intent_key: string;
  skill_id: string;
  status: TaskStatus;
  trigger_type: TriggerType;
  task_type: TaskType;
  input: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  attempt: number;
  max_attempts: number;
  cancel_requested: boolean;
  delivery_mode: DeliveryMode;
  delivery_status: DeliveryStatus;
  delivery_attempts: number;
  delivered_at: string | null;
  deadline_at: string;
  not_before: string;
  create_time: string;
  update_time: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TaskDetail extends TaskListItem {
  parent_id: string | null;
  root_id: string | null;
  item_key: string | null;
  result_artifact_id: string | null;
  lease_owner: string | null;
  lease_until: string | null;
  heartbeat_at: string | null;
  execution_snapshot: Record<string, unknown>;
  execution_snapshot_schema_version: number;
  snapshot_hash: string;
  timeline: TaskTimelineEvent[];
  children: TaskChild[];
}

export interface TaskListParams {
  schedule_id?: string;
  agent_id?: string;
  actor_user_id?: string;
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

export async function listScheduleTasks(
  scheduleId: string,
  params: TaskListParams = {}
): Promise<Page<TaskListItem>> {
  return listTasks({ ...params, schedule_id: scheduleId });
}
