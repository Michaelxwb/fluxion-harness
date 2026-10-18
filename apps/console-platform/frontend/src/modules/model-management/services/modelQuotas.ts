import { api, apiErrorBody, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

/**
 * 共享账号额度组（API-02）service：ADMIN 维护面（design frontend §3.2/§3.4）。
 *
 * - **共享预算**：一个额度组是**一份**容量声明（在飞上限 + RPM + TPM）。多把物理 Key（进而多个
 *   模型成员）引用同一组时共享这一份，不按 Key/成员数量复制或叠加；未归组的 Key 独占自身容量。
 *   本模块只表达「组内共享」的事实，引用关系在凭据归属里维护；
 * - **`rpm`/`tpm` 为 `null` = 该维度不设应用内限流**（不是零额度）：读投影原样保留 `null`，
 *   展示视图（`describeQuotaGroup`）显式给出 `UNLIMITED`，任何一侧都不折叠成 `0`；
 * - **创建支持 `Idempotency-Key`**（RULE-api-002）：同一次提交的重试必须复用同一个键，由调用方
 *   持有并透传（service 不自行生成，否则双击会变成两个额度组）；
 * - **更新是整份替换 + `expected_revision` CAS**：可编辑字段为 `key/rpm/tpm/max_inflight`；
 *   调小任一维度或从「不设限」新开限流需要先排空（`MODEL_CAPACITY_DRAIN_REQUIRED`），
 *   放宽/调大不受影响；
 * - **删除是软删且不误清引用**：仍被活跃凭据引用时后端 409 `MODEL_QUOTA_GROUP_IN_USE`，
 *   前端只提示冲突，不替后端清掉引用方的容量。
 */

/** 额度组读取投影：恰为可编辑字段与 id/revision（不含在飞状态/计量身份）。 */
export interface ModelQuotaGroupItem {
  id: string;
  key: string;
  max_inflight: number;
  /** `null` = 该维度不设应用内限流（不是零额度）。 */
  rpm: number | null;
  /** `null` = 该维度不设应用内限流（不是零额度）。 */
  tpm: number | null;
  revision: number;
}

/** 表单值：可选维度留空 = 不设限，用 `null` 表达（不是 `0`，也不是省略键）。 */
export interface ModelQuotaGroupFormValues {
  key: string;
  max_inflight: number;
  rpm: number | null;
  tpm: number | null;
}

export interface ModelQuotaGroupCreateInput {
  key: string;
  max_inflight: number;
  rpm: number | null;
  tpm: number | null;
}

/** 整份替换：可编辑字段 + CAS 版本。 */
export interface ModelQuotaGroupUpdateInput extends ModelQuotaGroupCreateInput {
  expected_revision: number;
}

/** 表单值非法：显式原因，供调用方决定本地化文案与是否提交。 */
export type ModelQuotaGroupInputReason =
  | 'KEY_REQUIRED'
  | 'MAX_INFLIGHT_INVALID'
  | 'RPM_INVALID'
  | 'TPM_INVALID';

export class ModelQuotaGroupInputError extends Error {
  readonly reason: ModelQuotaGroupInputReason;

  constructor(reason: ModelQuotaGroupInputReason) {
    super(reason);
    this.name = 'ModelQuotaGroupInputError';
    this.reason = reason;
  }
}

/** 维度限流状态：`UNLIMITED` 只由 `null` 得到（不是零额度）。 */
export type QuotaLimitState = 'LIMITED' | 'UNLIMITED';

/** 维度单位词条键（zh-CN/en-US 同步定义）。 */
export type QuotaUnitKey =
  | 'model.quota.unit.maxInflight'
  | 'model.quota.unit.rpm'
  | 'model.quota.unit.tpm';

export interface QuotaLimitDescriptor {
  /** `null` = 该维度不设应用内限流。 */
  value: number | null;
  state: QuotaLimitState;
  unitKey: QuotaUnitKey;
}

export interface QuotaGroupDescriptors {
  maxInflight: QuotaLimitDescriptor;
  rpm: QuotaLimitDescriptor;
  tpm: QuotaLimitDescriptor;
}

/** 冲突码 → 本地化文案键；未登记的失败一律回落通用保存失败。 */
const SAVE_ERROR_KEYS: Record<string, string> = {
  MODEL_QUOTA_GROUP_EXISTS: 'model.quota.error.exists',
  MODEL_CAPACITY_DRAIN_REQUIRED: 'model.quota.error.drainRequired',
  MODEL_QUOTA_GROUP_IN_USE: 'model.quota.error.inUse',
  REVISION_CONFLICT: 'model.quota.error.revisionConflict'
};

export function quotaGroupSaveErrorKey(error: unknown): string {
  const body = apiErrorBody(error);
  return (body !== null && SAVE_ERROR_KEYS[body.code]) || 'common.saveFailed';
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) {
    throw response.data;
  }
  return response.data.data;
}

/** 维度状态：只有 `null` 表示不设应用内限流；数值（含非法值）都算有限流维度。 */
export function quotaLimitState(value: number | null): QuotaLimitState {
  return value === null ? 'UNLIMITED' : 'LIMITED';
}

/** 单维度展示视图：值 + 状态 + 单位词条键；`null` 原样保留，不折叠成 0。 */
export function describeQuotaLimit(value: number | null, unitKey: QuotaUnitKey): QuotaLimitDescriptor {
  return { value, state: quotaLimitState(value), unitKey };
}

/** 额度组展示视图：三个维度各自的单位与状态（表格列/表单回显共用同一口径）。 */
export function describeQuotaGroup(group: ModelQuotaGroupItem): QuotaGroupDescriptors {
  return {
    maxInflight: describeQuotaLimit(group.max_inflight, 'model.quota.unit.maxInflight'),
    rpm: describeQuotaLimit(group.rpm, 'model.quota.unit.rpm'),
    tpm: describeQuotaLimit(group.tpm, 'model.quota.unit.tpm')
  };
}

function requirePositiveLimit(value: number | null, reason: ModelQuotaGroupInputReason): number | null {
  if (value === null) {
    return null;
  }
  if (!Number.isInteger(value) || value < 1) {
    throw new ModelQuotaGroupInputError(reason);
  }
  return value;
}

function requireMaxInflight(value: number): number {
  if (!Number.isInteger(value) || value < 1) {
    throw new ModelQuotaGroupInputError('MAX_INFLIGHT_INVALID');
  }
  return value;
}

/** 新增输入：标识必填；在飞上限必填正整数；RPM/TPM 留空 = 显式 `null`（不设限）。 */
export function buildQuotaGroupCreateInput(
  values: ModelQuotaGroupFormValues
): ModelQuotaGroupCreateInput {
  const key = values.key.trim();
  if (!key) {
    throw new ModelQuotaGroupInputError('KEY_REQUIRED');
  }
  return {
    key,
    max_inflight: requireMaxInflight(values.max_inflight),
    rpm: requirePositiveLimit(values.rpm, 'RPM_INVALID'),
    tpm: requirePositiveLimit(values.tpm, 'TPM_INVALID')
  };
}

/** 更新输入：整份替换可编辑字段 + 当前版本 CAS（与创建体同一套字段校验）。 */
export function buildQuotaGroupUpdateInput(
  group: ModelQuotaGroupItem,
  values: ModelQuotaGroupFormValues
): ModelQuotaGroupUpdateInput {
  return {
    ...buildQuotaGroupCreateInput(values),
    expected_revision: group.revision
  };
}

export async function listQuotaGroups(params: {
  page: number;
  page_size: number;
}): Promise<Page<ModelQuotaGroupItem>> {
  return unwrap(
    await api.get<ApiResponse<Page<ModelQuotaGroupItem>>>('/model-quota-groups', { params })
  );
}

export async function getQuotaGroup(id: string): Promise<ModelQuotaGroupItem> {
  return unwrap(await api.get<ApiResponse<ModelQuotaGroupItem>>(`/model-quota-groups/${id}`));
}

export async function createQuotaGroup(
  input: ModelQuotaGroupCreateInput,
  idempotencyKey: string
): Promise<ModelQuotaGroupItem> {
  return unwrap(
    await api.post<ApiResponse<ModelQuotaGroupItem>>('/model-quota-groups', input, {
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
}

export async function updateQuotaGroup(
  id: string,
  input: ModelQuotaGroupUpdateInput
): Promise<ModelQuotaGroupItem> {
  return unwrap(
    await api.put<ApiResponse<ModelQuotaGroupItem>>(`/model-quota-groups/${id}`, input)
  );
}

export async function deleteQuotaGroup(id: string): Promise<void> {
  await api.delete(`/model-quota-groups/${id}`);
}
