/**
 * 用户记忆枚举/错误码 → 文案与选项的**唯一**构造口（design §3.7 API 与 Console）。
 *
 * 契约：
 * - `t` 一律由调用方按**当前渲染**注入（`useTranslation()` 的返回值）；本模块不在模块作用域或
 *   组件状态里保存译文，调用方重渲染即得到新语言的文案。
 * - 选项同时携带**原始码** `value` 与译文 `label`：调用方以 `title={option.value}` 渲染时，
 *   悬浮提示里保留后端原始枚举/错误码；枚举域外的值原样显示（不吞掉原始码）。
 * - 枚举口径取后端事实：`muad_contracts.memory`（author/category/retirement reason）、
 *   `muad_contracts.memory_admin`（status/action）、`memory_commands.MemoryActor` 与 Runtime
 *   `memory_mutations.py` 实际写入的 `change_reason`。
 */

import type { TFunction } from 'i18next';

import {
  isMemoryErrorCode,
  type MemoryErrorCode,
  type MemoryItem,
  type MemoryWritableCategory
} from './memoryTypes';

/** 记忆作者（`MemoryAuthor`）：USER=真实确认、ADMIN=授权人工录入、AGENT=未经确认的推断。 */
export const MEMORY_AUTHORS = ['USER', 'ADMIN', 'AGENT'] as const;

/** 变更操作者（`MemoryActor`）：author 描述来源，actor 描述这次修改由谁执行。 */
export const MEMORY_ACTORS = ['USER', 'ADMIN', 'AGENT', 'SYSTEM'] as const;

/** 分类（`MemoryCategory`）：TASK_STATE 是任务线索保留命名空间。 */
export const MEMORY_CATEGORIES = ['PREFERENCE', 'WORK_STYLE', 'EXPLICIT', 'TASK_STATE'] as const;

/** 条目状态（`MemoryStatus`）：由 enabled + expires_at 派生。 */
export const MEMORY_STATUSES = ['ACTIVE', 'DISABLED', 'EXPIRED'] as const;

/** 确认状态：`explicit_request` 派生，真实确认 vs 未确认推断。 */
export const MEMORY_CONFIRMATION_STATES = ['EXPLICIT', 'INFERRED'] as const;

/** 退休原因（`MemoryRetirementReason`）：删除之外的自动退休取值。 */
export const MEMORY_RETIREMENT_REASONS = [
  'USER_DELETE',
  'ADMIN_DELETE',
  'EXPIRE',
  'QUOTA_EVICT',
  'TASK_COMPLETE'
] as const;

/** 历史 `change_reason` 的全部取值：upsert 四态 + 控制动作 + 退休原因 + 迁移基线。 */
export const MEMORY_CHANGE_REASONS = [
  'CREATE',
  'OVERWRITE',
  'CONFIRM',
  'RESTORE',
  'ENABLE',
  'DISABLE',
  'MIGRATION_BASELINE',
  ...MEMORY_RETIREMENT_REASONS
] as const;

export type MemoryAuthorValue = (typeof MEMORY_AUTHORS)[number];
export type MemoryActorValue = (typeof MEMORY_ACTORS)[number];
export type MemoryCategoryValue = (typeof MEMORY_CATEGORIES)[number];
export type MemoryStatusValue = (typeof MEMORY_STATUSES)[number];
export type MemoryConfirmationState = (typeof MEMORY_CONFIRMATION_STATES)[number];
export type MemoryChangeReason = (typeof MEMORY_CHANGE_REASONS)[number];

/** 下拉/筛选选项：`value` 是后端原始码，`label` 是本次渲染的译文。 */
export interface MemoryOption {
  value: string;
  label: string;
}

/** 动态键求值：值在枚举域内才查词条，域外原样返回原始码。 */
function memoryEnumLabel(
  t: TFunction,
  prefix: string,
  value: string,
  allowed: readonly string[]
): string {
  return allowed.includes(value) ? t(`${prefix}.${value}`) : value;
}

export function memoryAuthorLabel(t: TFunction, value: string): string {
  return memoryEnumLabel(t, 'user.memory.author', value, MEMORY_AUTHORS);
}

export function memoryAuthorOptions(t: TFunction): MemoryOption[] {
  return MEMORY_AUTHORS.map((value) => ({ value, label: memoryAuthorLabel(t, value) }));
}

export function memoryActorLabel(t: TFunction, value: string): string {
  return memoryEnumLabel(t, 'user.memory.actor', value, MEMORY_ACTORS);
}

export function memoryCategoryLabel(t: TFunction, value: string): string {
  return memoryEnumLabel(t, 'user.memory.category', value, MEMORY_CATEGORIES);
}

export function memoryCategoryOptions(t: TFunction): MemoryOption[] {
  return MEMORY_CATEGORIES.map((value) => ({ value, label: memoryCategoryLabel(t, value) }));
}

/**
 * 管理员可写分类选项（`MemoryCreateInput.category`）：`TASK_STATE` 是 Agent 任务线索的保留
 * 命名空间，Console 人工创建不可选（后端 `MemoryCreateRequest` 同样拒绝）。
 */
export function memoryWritableCategoryOptions(t: TFunction): MemoryOption[] {
  return MEMORY_CATEGORIES.filter(
    (value): value is MemoryWritableCategory => value !== 'TASK_STATE'
  ).map((value) => ({ value, label: memoryCategoryLabel(t, value) }));
}

export function memoryStatusLabel(t: TFunction, value: string): string {
  return memoryEnumLabel(t, 'user.memory.status', value, MEMORY_STATUSES);
}

export function memoryStatusOptions(t: TFunction): MemoryOption[] {
  return MEMORY_STATUSES.map((value) => ({ value, label: memoryStatusLabel(t, value) }));
}

/** 确认状态派生：只有真实确认（`explicit_request`）才是 EXPLICIT。 */
export function memoryConfirmationState(
  item: Pick<MemoryItem, 'explicit_request'>
): MemoryConfirmationState {
  return item.explicit_request ? 'EXPLICIT' : 'INFERRED';
}

export function memoryConfirmationLabel(
  t: TFunction,
  item: Pick<MemoryItem, 'explicit_request'>
): string {
  return memoryEnumLabel(
    t,
    'user.memory.confirmation',
    memoryConfirmationState(item),
    MEMORY_CONFIRMATION_STATES
  );
}

/** 历史原因：已知取值查词条，迁移/未知取值原样显示。 */
export function memoryChangeReasonLabel(t: TFunction, reason: string): string {
  return memoryEnumLabel(t, 'user.memory.changeReason', reason, MEMORY_CHANGE_REASONS);
}

/** 错误码 → 词条键：键名与 `config/api-messages.yaml` 的 catalog 码同名。 */
export const MEMORY_ERROR_KEYS: Record<MemoryErrorCode, string> = {
  MEMORY_VALUE_INVALID: 'user.memory.error.MEMORY_VALUE_INVALID',
  MEMORY_NOT_FOUND: 'user.memory.error.MEMORY_NOT_FOUND',
  COMMON_NOT_FOUND: 'user.memory.error.COMMON_NOT_FOUND',
  MEMORY_VERSION_CONFLICT: 'user.memory.error.MEMORY_VERSION_CONFLICT',
  IDEMPOTENCY_MISMATCH: 'user.memory.error.IDEMPOTENCY_MISMATCH',
  FORBIDDEN: 'user.memory.error.FORBIDDEN',
  COMMON_VALIDATION_ERROR: 'user.memory.error.COMMON_VALIDATION_ERROR',
  COMMON_INTERNAL_ERROR: 'user.memory.error.COMMON_INTERNAL_ERROR'
};

/** 未登记的错误码落这里：不回显后端 msg，也不让界面空着。 */
export const MEMORY_ERROR_FALLBACK_KEY = 'user.memory.errorFallback';

/** 错误码 → 文案：只认 `MEMORY_ERROR_CODES` 里的码，其余（含 null）落 fallback。 */
export function memoryErrorLabel(t: TFunction, code: string | null): string {
  if (code !== null && isMemoryErrorCode(code)) {
    return t(MEMORY_ERROR_KEYS[code]);
  }
  return t(MEMORY_ERROR_FALLBACK_KEY);
}
