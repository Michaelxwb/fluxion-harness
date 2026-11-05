import { api, type ApiResponse, type RequestOptions } from '../../../api/client';
import type {
  ActivationState,
  ExperienceDetail,
  ExperienceItem,
  ExperienceCounts,
  FamilyDetail,
  FamilyItem,
} from '../types';

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface ExperiencePage extends Page<ExperienceItem> {
  counts: ExperienceCounts;
}

export interface FamilyPage extends Page<FamilyItem> {
  window: string;
  unclassified_runs: number;
}

export interface ExperienceQuery {
  user_id: string;
  state?: ActivationState;
  family?: string;
  q?: string;
  page: number;
  page_size: number;
}

function unwrap<T>(response: { data: ApiResponse<T> }): T {
  if (response.data.code !== '0' || response.data.data === null) {
    throw new Error(response.data.code);
  }
  return response.data.data;
}

export async function listExperiences(query: ExperienceQuery): Promise<ExperiencePage> {
  return unwrap(await api.get<ApiResponse<ExperiencePage>>('/learning/experiences', { params: query }));
}

export async function getExperience(userId: string, key: string): Promise<ExperienceDetail> {
  return unwrap(
    await api.get<ApiResponse<ExperienceDetail>>(`/learning/experiences/${userId}/${key}`)
  );
}

export async function listFamilies(params: {
  source?: string;
  q?: string;
  window_days?: number;
  page: number;
  page_size: number;
}): Promise<FamilyPage> {
  return unwrap(await api.get<ApiResponse<FamilyPage>>('/learning/families', { params }));
}

export async function getFamily(
  family: string, options: RequestOptions = {}, windowDays = 7
): Promise<FamilyDetail> {
  return unwrap(
    await api.get<ApiResponse<FamilyDetail>>(`/learning/families/${encodeURIComponent(family)}`, {
      ...options,
      params: { window_days: windowDays }
    })
  );
}

export async function replayExperience(
  userId: string,
  key: string,
  idempotencyKey: string,
  options: RequestOptions = {}
): Promise<{ id: string; status: string }> {
  return unwrap(
    await api.post<ApiResponse<{ id: string; status: string }>>(
      `/learning/experiences/${userId}/${key}/replay`,
      {},
      { headers: { 'Idempotency-Key': idempotencyKey }, ...options }
    )
  );
}

export async function experienceAction(
  userId: string,
  key: string,
  action: 'activate' | 'retire' | 'regenerate',
  reason: string | undefined,
  idempotencyKey: string,
  options: RequestOptions = {}
): Promise<{ activation_state: ActivationState; status: string }> {
  return unwrap(
    await api.post<ApiResponse<{ activation_state: ActivationState; status: string }>>(
      `/learning/experiences/${userId}/${key}/${action}`,
      { reason },
      { headers: { 'Idempotency-Key': idempotencyKey }, ...options }
    )
  );
}

export async function reviseExperience(
  userId: string,
  key: string,
  value: string,
  expectedVersion: number,
  reason: string,
  idempotencyKey: string,
  options: RequestOptions = {}
): Promise<{ activation_state: ActivationState; status: string }> {
  return unwrap(
    await api.patch<ApiResponse<{ activation_state: ActivationState; status: string }>>(
      `/learning/experiences/${userId}/${key}/content`,
      { value, expected_version: expectedVersion, reason },
      { headers: { 'Idempotency-Key': idempotencyKey }, ...options }
    )
  );
}


export async function stopRun(
  runId: string,
  reason: string,
  idempotencyKey: string,
  options: RequestOptions = {}
): Promise<{ items: Array<Record<string, unknown>> }> {
  return unwrap(
    await api.post<ApiResponse<{ items: Array<Record<string, unknown>> }>>(
      `/learning/runs/${runId}/stop`,
      { reason },
      { headers: { 'Idempotency-Key': idempotencyKey }, ...options }
    )
  );
}

export async function familyControl(
  family: string,
  paused: boolean,
  reason: string,
  idempotencyKey: string,
  options: RequestOptions = {}
): Promise<{ family: string; paused: boolean }> {
  return unwrap(
    await api.post<ApiResponse<{ family: string; paused: boolean }>>(
      `/learning/families/${encodeURIComponent(family)}/control`,
      { paused, reason },
      { headers: { 'Idempotency-Key': idempotencyKey }, ...options }
    )
  );
}

export async function feedbackRun(
  runId: string,
  reason: string,
  idempotencyKey: string,
  options: RequestOptions = {}
): Promise<Record<string, unknown>> {
  return unwrap(
    await api.post<ApiResponse<Record<string, unknown>>>(
      `/learning/runs/${runId}/feedback`,
      { reason },
      { headers: { 'Idempotency-Key': idempotencyKey }, ...options }
    )
  );
}


export interface RunLearningDiagnostics {
  run_id: string;
  task_family: string | null;
  user_id: string | null;
  learning_enabled: boolean | null;
  min_score: number | null;
  recorded: boolean;
  injected: Array<Record<string, unknown>>;
  skipped: Array<Record<string, unknown>>;
  dropped_by_budget: Array<Record<string, unknown>>;
}

export async function getRunLearningDiagnostics(
  runId: string, options: RequestOptions = {}
): Promise<RunLearningDiagnostics> {
  return unwrap(
    await api.get<ApiResponse<RunLearningDiagnostics>>(`/learning/runs/${runId}/diagnostics`, options)
  );
}
