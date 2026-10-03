import { api, type ApiResponse } from '../../../api/client';

export type RunStatus =
  | 'CREATED'
  | 'RUNNING'
  | 'WAITING_INPUT'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

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
  error_code: string | null;
  error_message: string | null;
  create_time: string;
  update_time: string;
  timeline: RunTimelineEvent[];
  timeline_truncated: boolean;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

export async function getRun(id: string): Promise<RunDetail> {
  return unwrap(await api.get<ApiResponse<RunDetail>>(`/runs/${id}`));
}
