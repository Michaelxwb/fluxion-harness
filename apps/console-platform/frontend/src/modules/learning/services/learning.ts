import { api, type ApiResponse } from '../../../api/client';
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

export async function getFamily(family: string, windowDays = 7): Promise<FamilyDetail> {
  return unwrap(
    await api.get<ApiResponse<FamilyDetail>>(`/learning/families/${encodeURIComponent(family)}`, {
      params: { window_days: windowDays }
    })
  );
}

export async function replayExperience(
  userId: string,
  key: string,
  idempotencyKey: string
): Promise<{ id: string; status: string }> {
  return unwrap(
    await api.post<ApiResponse<{ id: string; status: string }>>(
      `/learning/experiences/${userId}/${key}/replay`,
      {},
      { headers: { 'Idempotency-Key': idempotencyKey } }
    )
  );
}

export async function experienceAction(
  userId: string,
  key: string,
  action: 'activate' | 'retire' | 'regenerate',
  reason: string | undefined,
  idempotencyKey: string
): Promise<{ activation_state: ActivationState; status: string }> {
  return unwrap(
    await api.post<ApiResponse<{ activation_state: ActivationState; status: string }>>(
      `/learning/experiences/${userId}/${key}/${action}`,
      { reason },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    )
  );
}

export async function reviseExperience(
  userId: string,
  key: string,
  value: string,
  expectedVersion: number,
  reason: string,
  idempotencyKey: string
): Promise<{ activation_state: ActivationState; status: string }> {
  return unwrap(
    await api.patch<ApiResponse<{ activation_state: ActivationState; status: string }>>(
      `/learning/experiences/${userId}/${key}/content`,
      { value, expected_version: expectedVersion, reason },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    )
  );
}
