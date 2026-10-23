import { api, apiErrorBody, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

/**
 * 模型成员关系（API-01）service：ADMIN 维护面（design frontend §3.2/§3.4）。
 *
 * - 成员是**关系行**：只引用 `credential_id`，不复制也不再次填写密钥/端点；
 * - 关系修改是**单关系**操作：POST 绑定一条、PUT 改一条（`expected_revision` CAS）、DELETE 解绑一条；
 *   没有覆盖整个关系集合的写入口（RULE-rel-001；集合整体 PUT 由后端 405 拒绝）；
 * - PUT 不携带 `credential_id`：更换凭据是 DELETE 旧关系 + POST 新关系，不能用整份替换盖掉集合；
 * - 读取投影恰为 `{id,key,model_id,credential_id,weight,enabled,revision}`；
 * - 同文件还提供成员面共用的两个**逻辑模型事实读取**（输出预算与供方）：模型详情的成员 Tab 与
 *   基本信息同屏展示它们，且必须与 TASK-019 契约同口径（见两个函数的说明）。
 */

export interface ModelDeploymentItem {
  id: string;
  key: string;
  model_id: string;
  credential_id: string;
  weight: number;
  enabled: boolean;
  revision: number;
}

/** 成员权重契约（`MemberWeight`）：正整数 1..100。 */
export const MEMBER_WEIGHT_MIN = 1;
export const MEMBER_WEIGHT_MAX = 100;

/** 成员标识契约（`ResourceKey`）：1..128。 */
export const MEMBER_KEY_MAX_LENGTH = 128;

/**
 * 成员标识默认取所选物理 Key 的标识；同模型内重名时追加 `-2`/`-3`…
 *
 * 成员标识是**关系名**（同模型内唯一），物理 Key 标识是**凭据名**（同租户内唯一）；
 * 常见场景直接用 Key 标识最直观，但若用户曾手动命名过同名成员，仍要给出可用的唯一值。
 * 上限与后端 `ResourceKey` 一致，截断发生在追加后缀之前。
 */
export function suggestMemberKey(
  credentialKey: string,
  existingKeys: readonly string[]
): string {
  const base = credentialKey.trim();
  const used = new Set(existingKeys);
  if (!used.has(base)) {
    return base;
  }
  for (let index = 2; index <= existingKeys.length + 2; index += 1) {
    const suffix = `-${index}`;
    const candidate = `${base.slice(0, MEMBER_KEY_MAX_LENGTH - suffix.length)}${suffix}`;
    if (!used.has(candidate)) {
      return candidate;
    }
  }
  const fallback = `-${Date.now().toString(36)}`;
  return `${base.slice(0, MEMBER_KEY_MAX_LENGTH - fallback.length)}${fallback}`;
}

export interface ModelDeploymentFormValues {
  key: string;
  credential_id: string;
  weight: number;
  enabled: boolean;
}

export interface ModelDeploymentCreateInput {
  key: string;
  credential_id: string;
  weight: number;
  enabled: boolean;
}

/** 单关系 PUT：只改成员名/权重/启用 + CAS 版本，不含 `credential_id`。 */
export interface ModelDeploymentUpdateInput {
  key: string;
  weight: number;
  enabled: boolean;
  expected_revision: number;
}

/** 表单值非法：显式原因，供调用方决定本地化文案与是否提交。 */
export type ModelDeploymentInputReason = 'WEIGHT_INVALID' | 'CREDENTIAL_REQUIRED';

export class ModelDeploymentInputError extends Error {
  readonly reason: ModelDeploymentInputReason;

  constructor(reason: ModelDeploymentInputReason) {
    super(reason);
    this.name = 'ModelDeploymentInputError';
    this.reason = reason;
  }
}

/** 冲突码 → 本地化文案键；未登记的失败一律回落通用保存失败。 */
const SAVE_ERROR_KEYS: Record<string, string> = {
  MODEL_DEPLOYMENT_EXISTS: 'model.member.error.exists',
  MODEL_PROVIDER_MISMATCH: 'model.member.error.providerMismatch',
  REVISION_CONFLICT: 'model.member.error.revisionConflict'
};

export function deploymentSaveErrorKey(error: unknown): string {
  const body = apiErrorBody(error);
  return (body !== null && SAVE_ERROR_KEYS[body.code]) || 'common.saveFailed';
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) {
    throw response.data;
  }
  return response.data.data;
}

function requireWeight(value: number): number {
  if (!Number.isInteger(value) || value < MEMBER_WEIGHT_MIN || value > MEMBER_WEIGHT_MAX) {
    throw new ModelDeploymentInputError('WEIGHT_INVALID');
  }
  return value;
}

/** 绑定输入：从已配置凭据中选一条 + 权重；本面不出现密钥/端点字段。 */
export function buildDeploymentCreateInput(
  values: ModelDeploymentFormValues
): ModelDeploymentCreateInput {
  const credentialId = values.credential_id.trim();
  if (!credentialId) {
    throw new ModelDeploymentInputError('CREDENTIAL_REQUIRED');
  }
  return {
    key: values.key.trim(),
    credential_id: credentialId,
    weight: requireWeight(values.weight),
    enabled: values.enabled
  };
}

/** 权重版本修改输入：单关系 PUT 携带 CAS 版本；`values.credential_id` 只用于表单回显，不进请求体。 */
export function buildDeploymentUpdateInput(
  member: ModelDeploymentItem,
  values: ModelDeploymentFormValues
): ModelDeploymentUpdateInput {
  return {
    key: values.key.trim(),
    weight: requireWeight(values.weight),
    enabled: values.enabled,
    expected_revision: member.revision
  };
}

/**
 * 逻辑模型输出预算读取：`params.max_tokens` 必须是正整数（TASK-019 契约，也是运行时真正发送的键；
 * 同义但从不发送的 `max_output_tokens` 不算）。缺失/非法显式返回 `null`，由展示面给出「未声明」，
 * 不伪造默认预算。
 */
export function readOutputBudget(params: Record<string, unknown>): number | null {
  const budget = params.max_tokens;
  if (typeof budget !== 'number' || !Number.isInteger(budget) || budget <= 0) {
    return null;
  }
  return budget;
}

/**
 * 逻辑模型供方读取（TASK-019 契约字段 `provider_key`）。
 *
 * `services/models.ts` 的 `ModelItem` 仍带旧字段（`base_url`/`api_key_configured`），其 DTO 同步
 * 属 TASK-092；这里对后端真实投影做一次显式读取：缺失/非字符串按「未声明」（空串）处理，
 * 不猜供方、也不放行同供方核对。
 */
export function readModelProviderKey(model: object): string {
  if (!('provider_key' in model)) {
    return '';
  }
  const value = model.provider_key;
  return typeof value === 'string' ? value.trim() : '';
}

export async function listDeployments(
  modelId: string,
  params: { page: number; page_size: number }
): Promise<Page<ModelDeploymentItem>> {
  return unwrap(
    await api.get<ApiResponse<Page<ModelDeploymentItem>>>(`/models/${modelId}/deployments`, {
      params
    })
  );
}

export async function createDeployment(
  modelId: string,
  input: ModelDeploymentCreateInput
): Promise<ModelDeploymentItem> {
  return unwrap(
    await api.post<ApiResponse<ModelDeploymentItem>>(`/models/${modelId}/deployments`, input)
  );
}

export async function updateDeployment(
  modelId: string,
  deploymentId: string,
  input: ModelDeploymentUpdateInput
): Promise<ModelDeploymentItem> {
  return unwrap(
    await api.put<ApiResponse<ModelDeploymentItem>>(
      `/models/${modelId}/deployments/${deploymentId}`,
      input
    )
  );
}

export async function deleteDeployment(modelId: string, deploymentId: string): Promise<void> {
  await api.delete(`/models/${modelId}/deployments/${deploymentId}`);
}
