import { api, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

export interface ModelItem {
  id: string;
  key: string;
  name: string;
  protocol: string;
  model_id: string;
  base_url: string;
  api_key_configured: boolean;
  params: Record<string, unknown>;
  revision: number;
  enabled: boolean;
  last_test_status: 'UNTESTED' | 'AVAILABLE' | 'FAILED';
  last_test_at: string | null;
  create_time: string;
  update_time: string;
}

export interface ModelSaveInput {
  key?: string;
  name: string;
  protocol?: string;
  base_url: string;
  model_id: string;
  api_key?: string;
  params?: Record<string, unknown>;
  enabled?: boolean;
}

export interface ModelTestResult {
  model_id: string;
  status: 'AVAILABLE' | 'FAILED';
  latency_ms: number;
  error_code: string | null;
  tested_at: string;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) {
    throw response.data;
  }
  return response.data.data;
}

export async function listModels(params: {
  page: number;
  page_size: number;
  keyword?: string;
  enabled?: string;
  last_test_status?: string;
}): Promise<Page<ModelItem>> {
  return unwrap(await api.get<ApiResponse<Page<ModelItem>>>('/models', { params }));
}

export async function createModel(input: ModelSaveInput): Promise<ModelItem> {
  return unwrap(await api.post<ApiResponse<ModelItem>>('/models', input));
}

export async function getModel(id: string): Promise<ModelItem> {
  return unwrap(await api.get<ApiResponse<ModelItem>>(`/models/${id}`));
}

export async function updateModel(
  id: string,
  input: ModelSaveInput & { expected_revision: number }
): Promise<ModelItem> {
  return unwrap(await api.put<ApiResponse<ModelItem>>(`/models/${id}`, input));
}

export async function deleteModel(id: string): Promise<void> {
  await api.delete(`/models/${id}`);
}

export async function batchTestModels(ids: string[]): Promise<ModelTestResult[]> {
  const body = await unwrap(
    await api.post<ApiResponse<{ items: ModelTestResult[] }>>('/models/batch-test', { model_ids: ids })
  );
  return body.items;
}
