import { api, apiErrorBody, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';
import { listQuotaGroups as listQuotaGroupPage, type ModelQuotaGroupItem } from './modelQuotas';

/**
 * 物理模型凭据（API-16）service：ADMIN 维护面 + 物理 Key 检测（API-16 检测面，design §3.4/§3.7）。
 *
 * - **读取投影只有 `configured`**：明文密钥与去重指纹都不在响应里，本模块也不持有密钥字段；
 * - **创建支持 `Idempotency-Key`**（RULE-api-002）：同一次提交的重试必须复用同一个键，由调用方
 *   持有并透传（service 不自行生成，否则双击会变成两把 Key）；
 * - **更新是整份替换 + `expected_revision` CAS**：`api_key` 留空 = 保留原值（键都不发），非空 =
 *   显式替换；标识在编辑时不变更；
 * - **检测只点名 `(model_id, credential_id)`**：占额/合并计量/结算都在服务端（API-17），前端只展示
 *   逐项结果，不遍历模型×Key，也不推断整池健康；
 * - 额度组 CRUD 与单位展示属额度组管理面（TASK-088）；本模块只提供凭据表单归属选择需要的选项投影。
 */

/** 物理 Key 读取投影：没有 `api_key`，只有 `configured`。 */
export interface ModelCredentialItem {
  id: string;
  key: string;
  provider_key: string;
  base_url: string;
  quota_group_id: string | null;
  max_inflight: number;
  enabled: boolean;
  revision: number;
  configured: boolean;
}

export interface ModelCredentialFormValues {
  key: string;
  provider_key: string;
  base_url: string;
  /** 写入面：留空 = 保留原值（更新）/ 非法（创建）；不参与任何读取渲染。 */
  api_key: string;
  quota_group_id: string | null;
  max_inflight: number;
  enabled: boolean;
}

export interface ModelCredentialCreateInput {
  key: string;
  provider_key: string;
  base_url: string;
  api_key: string;
  quota_group_id: string | null;
  max_inflight: number;
  enabled: boolean;
}

export interface ModelCredentialUpdateInput {
  key: string;
  provider_key: string;
  base_url: string;
  /** 缺省 = 保留原值；非空 = 显式替换。 */
  api_key?: string;
  quota_group_id: string | null;
  max_inflight: number;
  enabled: boolean;
  expected_revision: number;
}

/** 凭据表单可选的额度组归属（只读选项投影；完整额度组 CRUD 见 `services/modelQuotas.ts`）。 */
export type ModelQuotaGroupOption = ModelQuotaGroupItem;

export interface ModelProbeTarget {
  model_id: string;
  credential_id: string;
}

export type ModelProbeStatus = 'AVAILABLE' | 'FAILED' | 'NOT_EXECUTED';

/** 逐项检测结果：只证明被点名的那一名成员。 */
export interface ModelProbeResult {
  model_id: string;
  credential_id: string;
  status: ModelProbeStatus;
  error_code: string | null;
  latency_ms: number;
  tested_at: string;
}

/** 表单值非法：显式原因，供调用方决定本地化文案与是否提交。 */
export type ModelCredentialInputReason = 'API_KEY_REQUIRED' | 'MAX_INFLIGHT_INVALID';

export class ModelCredentialInputError extends Error {
  readonly reason: ModelCredentialInputReason;

  constructor(reason: ModelCredentialInputReason) {
    super(reason);
    this.name = 'ModelCredentialInputError';
    this.reason = reason;
  }
}

/** 冲突码 → 本地化文案键；未登记的失败一律回落通用保存失败。 */
const SAVE_ERROR_KEYS: Record<string, string> = {
  MODEL_CREDENTIAL_EXISTS: 'model.credential.error.exists',
  MODEL_CAPACITY_DRAIN_REQUIRED: 'model.credential.error.drainRequired',
  MODEL_CREDENTIAL_IN_USE: 'model.credential.error.inUse',
  REVISION_CONFLICT: 'model.credential.error.revisionConflict'
};

export function credentialSaveErrorKey(error: unknown): string {
  const body = apiErrorBody(error);
  return (body !== null && SAVE_ERROR_KEYS[body.code]) || 'common.saveFailed';
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) {
    throw response.data;
  }
  return response.data.data;
}

function requireMaxInflight(value: number): number {
  if (!Number.isInteger(value) || value < 1) {
    throw new ModelCredentialInputError('MAX_INFLIGHT_INVALID');
  }
  return value;
}

function normalizedQuotaGroupId(value: string | null | undefined): string | null {
  return value ? value : null;
}

/** 新增输入：密钥必填（空串在契约层即 422，前端不制造无效请求）。 */
export function buildCredentialCreateInput(
  values: ModelCredentialFormValues
): ModelCredentialCreateInput {
  const apiKey = values.api_key.trim();
  if (!apiKey) {
    throw new ModelCredentialInputError('API_KEY_REQUIRED');
  }
  return {
    key: values.key.trim(),
    provider_key: values.provider_key.trim(),
    base_url: values.base_url.trim(),
    api_key: apiKey,
    quota_group_id: normalizedQuotaGroupId(values.quota_group_id),
    max_inflight: requireMaxInflight(values.max_inflight),
    enabled: values.enabled
  };
}

/** 更新输入：整份替换可编辑字段；留空密钥时连 `api_key` 键都不发（保留原值）。 */
export function buildCredentialUpdateInput(
  credential: ModelCredentialItem,
  values: ModelCredentialFormValues
): ModelCredentialUpdateInput {
  const input: ModelCredentialUpdateInput = {
    key: credential.key,
    provider_key: values.provider_key.trim(),
    base_url: values.base_url.trim(),
    quota_group_id: normalizedQuotaGroupId(values.quota_group_id),
    max_inflight: requireMaxInflight(values.max_inflight),
    enabled: values.enabled,
    expected_revision: credential.revision
  };
  const apiKey = values.api_key.trim();
  if (apiKey) {
    input.api_key = apiKey;
  }
  return input;
}

export async function listCredentials(params: {
  page: number;
  page_size: number;
}): Promise<Page<ModelCredentialItem>> {
  return unwrap(await api.get<ApiResponse<Page<ModelCredentialItem>>>('/model-credentials', { params }));
}

export async function getCredential(id: string): Promise<ModelCredentialItem> {
  return unwrap(await api.get<ApiResponse<ModelCredentialItem>>(`/model-credentials/${id}`));
}

export async function createCredential(
  input: ModelCredentialCreateInput,
  idempotencyKey: string
): Promise<ModelCredentialItem> {
  return unwrap(
    await api.post<ApiResponse<ModelCredentialItem>>('/model-credentials', input, {
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
}

export async function updateCredential(
  id: string,
  input: ModelCredentialUpdateInput
): Promise<ModelCredentialItem> {
  return unwrap(await api.put<ApiResponse<ModelCredentialItem>>(`/model-credentials/${id}`, input));
}

export async function deleteCredential(id: string): Promise<void> {
  await api.delete(`/model-credentials/${id}`);
}

/** 归属选项：额度组维护面（`ModelQuotaPanel`，API-02）负责 CRUD，这里只取首屏选项（page_size≤100）。 */
export async function listQuotaGroups(): Promise<Page<ModelQuotaGroupOption>> {
  return listQuotaGroupPage({ page: 1, page_size: 100 });
}

/** 检测：只点名被选中的成员；占额、合并计量与结算都由服务端完成。 */
export async function probeCredentials(targets: ModelProbeTarget[]): Promise<ModelProbeResult[]> {
  const body = await unwrap(
    await api.post<ApiResponse<{ items: ModelProbeResult[] }>>('/model-credentials/test', { targets })
  );
  return body.items;
}
