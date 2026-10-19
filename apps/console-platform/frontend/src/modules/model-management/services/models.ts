import { api, apiErrorBody, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

/**
 * 逻辑模型（API-01/API-13）service：模型池的管理面读取与版本维护。
 *
 * - **逻辑模型只声明协议/型号/供方/参数**：执行端点与密钥的唯一权威是物理凭据
 *   （`model_credential`，API-16），经成员关系（API-01）挂载。本面既不接收也不回显
 *   `base_url`/`api_key`/`api_key_configured`（后端 `extra="forbid"` 会直接拒绝这类字段）；
 * - **`params.max_tokens` 是真正发送的输出预算**（TASK-019 契约）：创建/更新都必须是正整数；
 *   同义但从不发送的 `max_output_tokens` 不算。表单只改这一个键，其余 params 原样保留，
 *   不静默丢用户看不到的参数；
 * - **更新是 CAS 版本操作**：显式带 `expected_revision`；启用切换只提交该字段，不重发连接/密钥。
 */

export interface ModelItem {
  id: string;
  key: string;
  name: string;
  protocol: string;
  model_id: string;
  provider_key: string;
  params: Record<string, unknown>;
  revision: number;
  enabled: boolean;
  last_test_status: 'UNTESTED' | 'AVAILABLE' | 'FAILED';
  last_test_at: string | null;
  create_time: string;
  update_time: string;
}

/** 表单值：字段面与后端契约一一对应（`provider_key` 可留空 = 尚未声明）。 */
export interface ModelFormValues {
  key: string;
  name: string;
  protocol: string;
  model_id: string;
  provider_key: string;
  max_tokens: number;
  enabled: boolean;
}

export interface ModelCreateInput {
  key: string;
  name: string;
  protocol: string;
  model_id: string;
  provider_key: string;
  params: Record<string, unknown>;
  enabled: boolean;
}

/** 部分更新：未提供的字段后端保持原值（`key` 不可变更，故不在更新体里）。 */
export interface ModelUpdateInput {
  expected_revision: number;
  name?: string;
  model_id?: string;
  provider_key?: string;
  params?: Record<string, unknown>;
  enabled?: boolean;
}

/** 表单值非法：显式原因，供调用方决定本地化文案与是否提交。 */
export type ModelInputReason =
  | 'KEY_REQUIRED'
  | 'NAME_REQUIRED'
  | 'MODEL_ID_REQUIRED'
  | 'MAX_TOKENS_INVALID';

export class ModelInputError extends Error {
  readonly reason: ModelInputReason;

  constructor(reason: ModelInputReason) {
    super(reason);
    this.name = 'ModelInputError';
    this.reason = reason;
  }
}

/** 冲突码 → 本地化文案键；未登记的失败一律回落通用保存失败。 */
const SAVE_ERROR_KEYS: Record<string, string> = {
  MODEL_KEY_EXISTS: 'model.error.exists',
  REVISION_CONFLICT: 'model.error.revisionConflict'
};

export function modelSaveErrorKey(error: unknown): string {
  const body = apiErrorBody(error);
  return (body !== null && SAVE_ERROR_KEYS[body.code]) || 'common.saveFailed';
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) {
    throw response.data;
  }
  return response.data.data;
}

function requireText(value: string, reason: ModelInputReason): string {
  const trimmed = value.trim();
  if (!trimmed) {
    throw new ModelInputError(reason);
  }
  return trimmed;
}

/**
 * 输出预算读取即校验：缺失、布尔、浮点、字符串、非正数一律显式拒绝。
 *
 * 这里**不做数值强转**（`Number('4096')` 会把字符串悄悄放行）：预算按实际发送上限算，
 * 类型错了必须让调用方看见，而不是替它猜。
 */
function requireOutputBudget(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value <= 0) {
    throw new ModelInputError('MAX_TOKENS_INVALID');
  }
  return value;
}

/** 创建输入：标识/名称/型号必填，`params` 只有真正发送的 `max_tokens`。 */
export function buildModelCreateInput(values: ModelFormValues): ModelCreateInput {
  return {
    key: requireText(values.key, 'KEY_REQUIRED'),
    name: requireText(values.name, 'NAME_REQUIRED'),
    protocol: 'OPENAI',
    model_id: requireText(values.model_id, 'MODEL_ID_REQUIRED'),
    provider_key: values.provider_key.trim(),
    params: { max_tokens: requireOutputBudget(values.max_tokens) },
    enabled: values.enabled
  };
}

/** 更新输入：整份可编辑字段 + CAS 版本；其余 params 原样保留，只覆盖 `max_tokens`。 */
export function buildModelUpdateInput(model: ModelItem, values: ModelFormValues): ModelUpdateInput {
  return {
    expected_revision: model.revision,
    name: requireText(values.name, 'NAME_REQUIRED'),
    model_id: requireText(values.model_id, 'MODEL_ID_REQUIRED'),
    provider_key: values.provider_key.trim(),
    params: { ...model.params, max_tokens: requireOutputBudget(values.max_tokens) },
    enabled: values.enabled
  };
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

export async function createModel(input: ModelCreateInput): Promise<ModelItem> {
  return unwrap(await api.post<ApiResponse<ModelItem>>('/models', input));
}

export async function getModel(id: string): Promise<ModelItem> {
  return unwrap(await api.get<ApiResponse<ModelItem>>(`/models/${id}`));
}

export async function updateModel(id: string, input: ModelUpdateInput): Promise<ModelItem> {
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

export interface ModelTestResult {
  model_id: string;
  status: 'AVAILABLE' | 'FAILED';
  latency_ms: number;
  error_code: string | null;
  tested_at: string;
}
