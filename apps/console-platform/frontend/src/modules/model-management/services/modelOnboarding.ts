import { apiErrorBody } from '../../../api/client';
import {
  createDeployment,
  type ModelDeploymentCreateInput,
  type ModelDeploymentItem
} from './modelDeployments';
import {
  buildModelCreateInput,
  createModel,
  type ModelCreateInput,
  type ModelFormValues,
  type ModelItem
} from './models';
import type { ModelCredentialItem } from './modelCredentials';

/**
 * “新增模型”两步向导的写入编排（design frontend §3.2/§3.4；backend API-01/API-13/API-16）。
 *
 * - **供方由物理 Key 决定**：逻辑模型的 `provider_key` 直接取所选 Key 的供方，避免用户在
 *   模型表单里填出必然 `MODEL_PROVIDER_MISMATCH` 的组合；
 * - **创建后自动绑定第一条成员**：成员名取所选物理 Key 的标识（同模型内重名时由调用方
 *   追加后缀）、权重 1、启用；后续要加更多 Key 仍走模型详情 → 成员的单关系写入口
 *   （本模块不提供集合整体替换）；
 * - **绑定失败不吞掉已创建的模型**：抛 `MemberBindingFailedError`，调用方保留模型并给出
 *   “重试绑定 / 去成员页”两条明确路径；响应丢失后的重试若命中 `MODEL_DEPLOYMENT_EXISTS`，
 *   由调用方按“已绑定”处理（`isMemberAlreadyBound`）。
 */

export const FIRST_MEMBER_WEIGHT = 1;

/** 模型创建输入：供方强制取所选物理 Key，表单里的同名字段不参与。 */
export function buildOnboardingModelInput(
  values: ModelFormValues,
  credential: ModelCredentialItem
): ModelCreateInput {
  return buildModelCreateInput({
    ...values,
    provider_key: credential.provider_key
  });
}

/** 第一条成员绑定输入：成员名取物理 Key 标识（新模型无其它成员，不会重名）。 */
export function buildFirstMemberInput(
  credential: ModelCredentialItem
): ModelDeploymentCreateInput {
  return {
    key: credential.key.trim(),
    credential_id: credential.id,
    weight: FIRST_MEMBER_WEIGHT,
    enabled: true
  };
}

export class MemberBindingFailedError extends Error {
  readonly model: ModelItem;
  readonly reason: unknown;

  constructor(model: ModelItem, reason: unknown) {
    super('MEMBER_BINDING_FAILED');
    this.name = 'MemberBindingFailedError';
    this.model = model;
    this.reason = reason;
  }
}

/** 绑定响应丢失后的重试：`MODEL_DEPLOYMENT_EXISTS` 表示这条成员已经在服务端落库。 */
export function isMemberAlreadyBound(error: unknown): boolean {
  return apiErrorBody(error)?.code === 'MODEL_DEPLOYMENT_EXISTS';
}

export interface ModelWithMember {
  model: ModelItem;
  member: ModelDeploymentItem;
}

/** 创建逻辑模型并自动绑定第一条成员；绑定失败时保留已创建的模型供调用方重试。 */
export async function createModelWithMember(
  values: ModelFormValues,
  credential: ModelCredentialItem
): Promise<ModelWithMember> {
  const model = await createModel(buildOnboardingModelInput(values, credential));
  try {
    const member = await createDeployment(model.id, buildFirstMemberInput(credential));
    return { model, member };
  } catch (error) {
    throw new MemberBindingFailedError(model, error);
  }
}

/** 重试绑定第一条成员（不重复创建逻辑模型）。 */
export async function bindFirstMember(
  model: ModelItem,
  credential: ModelCredentialItem
): Promise<ModelDeploymentItem> {
  return createDeployment(model.id, buildFirstMemberInput(credential));
}
