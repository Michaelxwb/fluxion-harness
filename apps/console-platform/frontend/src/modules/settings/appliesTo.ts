/**
 * 生效方式标签映射（前端设计 §3.3 CMP-05、§2.4 B-06）。
 *
 * 纯函数：把 API 返回的 `applies_to` 枚举映射为 i18n 词条键。这是前端唯一一处「五个取值 →
 * 标签」的映射；未知取值**不做兜底文案**（B-06 明示），而是原样返回枚举值本身，让契约测试
 * 能在未知取值出现时直接失败（后端新增取值必须先补词条与映射）。
 */

import type { AppliesTo } from './types';

/** `applies_to` 的五个已知取值（与后端 `platform_settings_catalog.py` 一致）。 */
export const APPLIES_TO_VALUES: readonly AppliesTo[] = [
  'new_run',
  'new_task',
  'next_operation',
  'restart_required',
  'code'
] as const;

/** 取值 → i18n 词条键；未知取值返回 null（不兜底）。 */
export function appliesToLabelKey(value: AppliesTo): string | null {
  if (!APPLIES_TO_VALUES.includes(value)) {
    return null;
  }
  return `settings.appliesTo.${value}`;
}
