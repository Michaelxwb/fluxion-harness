import { api, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

export type RunStatus =
  | 'CREATED'
  | 'RUNNING'
  | 'WAITING_TOOL'
  | 'WAITING_INPUT'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

/** API-05 的等待原因：仅 WAITING_TOOL 非空（契约 `muad_contracts.tool_waiting_reason`）。 */
export type WaitReason = 'SUBMISSION' | 'TASK_RESULT' | 'RESUME_READY';

/** 与 `muad_contracts.enums.CompletionMode` 逐字对齐。 */
export type CompletionMode = 'JOIN' | 'DETACH';

/** 与 `muad_contracts.enums.OperationStatus` 逐字对齐（新增枚举值时两侧同步）。 */
export type OperationStatus =
  | 'SUBMIT_PENDING'
  | 'SUBMITTED'
  | 'TASK_ACCEPTED'
  | 'RUNNING'
  | 'RESULT_RECEIVED'
  | 'MATERIALIZED'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
  | 'LATE';

/** 与 `muad_contracts.enums.OperationErrorPhase` 逐字对齐；null 表示无错误。 */
export type OperationErrorPhase = 'SUBMIT' | 'EXECUTE';

/** TaskStatus 复用既有定义（`task.task_execution.status`）。 */
export type TaskStatus = 'QUEUED' | 'RUNNING' | 'WAITING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

/** API-06：默认 15、上限 100（与后端 `runs.py` 的 Query 约束一致）。 */
export const OPERATIONS_PAGE_SIZE_DEFAULT = 15;
export const OPERATIONS_PAGE_SIZE_MAX = 100;

/** 运行轮廓里的一条事件。**不带事件负载**——见 `RunDetailSideSheet` 的模块注释。 */
export interface RunTimelineEvent {
  seq: number;
  event_type: string;
  stream_type: string | null;
  has_artifact: boolean;
  create_time: string;
}

export interface RunDetail {
  run_id: string;
  conversation_id: string;
  status: RunStatus;
  agent_id: string;
  agent_name: string | null;
  actor_user_id: string;
  actor_name: string | null;
  trace_id: string;
  start_time: string | null;
  end_time: string | null;
  /** WAITING_TOOL/WAITING_INPUT 的等待起点；其余状态为 null。 */
  waiting_since: string | null;
  /** 等待的绝对截止时间（timestamptz）。 */
  deadline_at: string | null;
  waiting_reason: WaitReason | null;
  pending_join_count: number;
  pending_submission_count: number;
  continuation_count: number;
  error_code: string | null;
  error_message: string | null;
  create_time: string;
  update_time: string;
  timeline: RunTimelineEvent[];
  timeline_truncated: boolean;
}

/** API-06 的只读操作轮廓：无 input/result/凭据字段（后端白名单投影）。 */
export interface RunOperationOutline {
  operation_id: string;
  source_tool_call_id: string;
  completion_mode: CompletionMode;
  status: OperationStatus;
  error_phase: OperationErrorPhase | null;
  error_code: string | null;
  /** 受理前为 null——页面不得为它造链接。 */
  task_id: string | null;
  task_status: TaskStatus | null;
  submitted_at: string | null;
  completed_at: string | null;
}

export interface RunOperationListParams {
  page?: number;
  page_size?: number;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

export async function getRun(id: string): Promise<RunDetail> {
  return unwrap(await api.get<ApiResponse<RunDetail>>(`/runs/${id}`));
}

/**
 * API-06：关联操作分页。响应是封套 `Page`（不是裸数组），默认 15、page_size 上限 100。
 * 服务层夹紧分页参数，组件/hooks 不再各自复述边界。
 */
export async function listRunOperations(
  id: string,
  params: RunOperationListParams = {}
): Promise<Page<RunOperationOutline>> {
  const page = Math.max(1, Math.floor(params.page ?? 1));
  const requested = Math.floor(params.page_size ?? OPERATIONS_PAGE_SIZE_DEFAULT);
  const pageSize = Math.min(Math.max(1, requested), OPERATIONS_PAGE_SIZE_MAX);
  return unwrap(
    await api.get<ApiResponse<Page<RunOperationOutline>>>(`/runs/${id}/operations`, {
      params: { page, page_size: pageSize }
    })
  );
}
