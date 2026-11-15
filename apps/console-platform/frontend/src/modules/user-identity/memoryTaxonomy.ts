/**
 * 受控 taxonomy 的前端投影（创建表单的谓词/条件选项）。
 *
 * 事实源在 `packages/contracts/src/muad_contracts/memory_taxonomy.py`；这里只做展示层的
 * 只读镜像，契约测试（`tests/frontend/test_memory_taxonomy_parity.py`）逐项核对 key/kind/
 * cardinality/scope 维度，漂移会直接红。管理员能选什么、能填什么不允许在这里另立规则。
 */

import type { MemoryCardinality, MemoryKind } from './memoryTypes';

export type ScopeDimension = 'audience' | 'region';

export interface MemoryPredicateOption {
  key: string;
  kind: MemoryKind;
  cardinality: MemoryCardinality;
  scopeDimensions: readonly ScopeDimension[];
  labelZh: string;
  labelEn: string;
}

export const MEMORY_PREDICATES: readonly MemoryPredicateOption[] = [
  { key: 'report_format', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: ['audience'], labelZh: '报告格式', labelEn: 'Report format' },
  { key: 'language', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: [], labelZh: '语言', labelEn: 'Language' },
  { key: 'timezone', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: [], labelZh: '时区', labelEn: 'Time zone' },
  { key: 'output_style', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: ['audience'], labelZh: '风格', labelEn: 'Output style' },
  { key: 'response_length', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: [], labelZh: '长度', labelEn: 'Response length' },
  { key: 'data_format', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: ['audience'], labelZh: '数据格式', labelEn: 'Data format' },
  { key: 'notification_channel', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: [], labelZh: '通知方式', labelEn: 'Notification channel' },
  { key: 'working_hours', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: [], labelZh: '工作时间', labelEn: 'Working hours' },
  { key: 'default_priority', kind: 'PREFERENCE', cardinality: 'SINGLE', scopeDimensions: [], labelZh: '默认优先级', labelEn: 'Default priority' },
  { key: 'role', kind: 'FACT', cardinality: 'SINGLE', scopeDimensions: [], labelZh: '职位', labelEn: 'Role' },
  { key: 'team', kind: 'FACT', cardinality: 'SINGLE', scopeDimensions: [], labelZh: '团队', labelEn: 'Team' },
  { key: 'owned_system', kind: 'FACT', cardinality: 'SET', scopeDimensions: [], labelZh: '负责系统', labelEn: 'Owned system' },
  { key: 'general', kind: 'NOTE', cardinality: 'SET', scopeDimensions: [], labelZh: '通用笔记', labelEn: 'General note' },
] as const;

export const MEMORY_PREDICATE_MAP: ReadonlyMap<string, MemoryPredicateOption> = new Map(
  MEMORY_PREDICATES.map((item) => [item.key, item])
);

export const SCOPE_VALUES: Readonly<Record<ScopeDimension, readonly string[]>> = {
  audience: ['internal', 'external'],
  region: ['CN', 'US', 'EU']
} as const;

export function predicateLabel(item: MemoryPredicateOption, locale: string): string {
  const label = locale === 'en-US' ? item.labelEn : item.labelZh;
  return locale === 'en-US' ? `${label} (${item.key})` : `${label}（${item.key}）`;
}
