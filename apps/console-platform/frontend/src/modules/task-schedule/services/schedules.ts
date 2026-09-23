import { api, newRequestId, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

export type ScheduleStatus = 'ACTIVE' | 'PAUSED' | 'COMPLETED' | 'MISSED';
export type ScheduleType = 'CRON' | 'ONCE';

export const SCHEDULE_PAGE_SIZE_MAX = 100;

export interface ScheduleListItem {
  schedule_id: string;
  tenant_id: string;
  name: string;
  agent_id: string;
  actor_user_id: string;
  intent_key: string;
  skill_id: string;
  input_template: Record<string, unknown>;
  schedule_type: ScheduleType;
  cron_expr: string | null;
  timezone: string;
  run_at: string | null;
  status: ScheduleStatus;
  next_fire_at: string | null;
  last_fire_at: string | null;
  revision: number;
  completed_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  last_skipped_at: string | null;
  create_time: string;
  update_time: string;
}

export type ScheduleDetail = ScheduleListItem;

export interface ScheduleListParams {
  actor_user_id?: string;
  agent_id?: string;
  status?: ScheduleStatus;
  page?: number;
  page_size?: number;
}

export interface DeleteScheduleResult {
  schedule_id: string;
  deleted: boolean;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

function normalizeParams(params: ScheduleListParams): ScheduleListParams {
  const pageSize = params.page_size ?? 20;
  return {
    ...params,
    page: params.page ?? 1,
    page_size: Math.min(pageSize, SCHEDULE_PAGE_SIZE_MAX)
  };
}

export async function listSchedules(
  params: ScheduleListParams = {}
): Promise<Page<ScheduleListItem>> {
  return unwrap(
    await api.get<ApiResponse<Page<ScheduleListItem>>>('/schedules', {
      params: normalizeParams(params)
    })
  );
}

export async function getSchedule(id: string): Promise<ScheduleDetail> {
  return unwrap(await api.get<ApiResponse<ScheduleDetail>>(`/schedules/${id}`));
}

export async function pauseSchedule(id: string): Promise<ScheduleDetail> {
  return unwrap(
    await api.put<ApiResponse<ScheduleDetail>>(`/schedules/${id}/pause`, undefined, {
      headers: { 'X-Request-Id': newRequestId() }
    })
  );
}

export async function resumeSchedule(id: string): Promise<ScheduleDetail> {
  return unwrap(
    await api.put<ApiResponse<ScheduleDetail>>(`/schedules/${id}/resume`, undefined, {
      headers: { 'X-Request-Id': newRequestId() }
    })
  );
}

export async function deleteSchedule(id: string): Promise<DeleteScheduleResult> {
  return unwrap(
    await api.delete<ApiResponse<DeleteScheduleResult>>(`/schedules/${id}`, {
      headers: { 'X-Request-Id': newRequestId() }
    })
  );
}
