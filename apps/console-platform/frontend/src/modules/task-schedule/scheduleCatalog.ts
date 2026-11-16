import type { StatusTagOption } from '../../components/common/StatusTag';
import type {
  AgenticBudget,
  ConcurrencyPolicy,
  DeliveryIntent,
  DstEvent,
  DstEventType,
  DstResolution,
  FireHistoryPage,
  FireItem,
  FireSkipReason,
  FireStatus,
  MemoryMode,
  ScheduleEndReason,
  ScheduleMode,
  ScheduleStatus
} from './services/schedules';
import type {
  TaskHumanSummary,
  TaskSnapshotSummary,
  TaskStatus,
  TaskType,
  WaitKind
} from './services/tasks';

/**
 * Console 任务/定时任务的**纯映射层**（scheduled-task-v2 §3.10；TASK-030）。
 *
 * 状态、模式、Fire 结果、跳过原因、DST 事件与等待类型都来自后端事实，这里只把它们映射成
 * 词条文案，不做状态机推断。词条缺失（后端新增枚举而前端未跟上）时显式展示「未知 (原值)」，
 * 既不吞掉原值也不把裸 schema key 当文案。
 *
 * 本模块不 import React/Semi 运行时、不发请求，因此可以直接由 node 执行其编译产物。
 */

export type Translate = (key: string) => string;

export const SCHEDULE_STATUSES: ScheduleStatus[] = [
  'ACTIVE',
  'PAUSED',
  'NEEDS_ATTENTION',
  'ENDED',
  'CANCELLED'
];

export const SCHEDULE_MODES: ScheduleMode[] = ['REMINDER', 'SKILL', 'AGENTIC'];

export const TASK_STATUSES: TaskStatus[] = [
  'QUEUED',
  'RUNNING',
  'WAITING_EXTERNAL',
  'WAITING_HUMAN',
  'COMPLETED',
  'FAILED',
  'CANCELLED'
];

/** 可取消的执行状态：终态不再响应取消。 */
export const CANCELLABLE_TASK_STATUSES: TaskStatus[] = [
  'QUEUED',
  'RUNNING',
  'WAITING_EXTERNAL',
  'WAITING_HUMAN'
];

const UNKNOWN = 'unknown';

/** 词条缺失时展示通用文案 + 原始枚举值，未知码不会静默变成空白。 */
export function unknownLabel(t: Translate, group: string, value: string): string {
  return `${t(`${group}.${UNKNOWN}`)} (${value})`;
}

function label(t: Translate, group: string, value: string): string {
  return t(`${group}.${value}`);
}

export function scheduleStatusLabel(t: Translate, status: string): string {
  return SCHEDULE_STATUSES.includes(status as ScheduleStatus)
    ? label(t, 'schedule.status', status)
    : unknownLabel(t, 'schedule.status', status);
}

export function scheduleModeLabel(t: Translate, mode: string): string {
  return SCHEDULE_MODES.includes(mode as ScheduleMode)
    ? label(t, 'schedule.mode', mode)
    : unknownLabel(t, 'schedule.mode', mode);
}

export function endReasonLabel(t: Translate, reason: ScheduleEndReason): string {
  return label(t, 'schedule.endReason', reason);
}

export function deliveryIntentLabel(t: Translate, intent: DeliveryIntent): string {
  return label(t, 'schedule.delivery', intent);
}

export function memoryModeLabel(t: Translate, mode: MemoryMode | null): string {
  return mode === null ? '-' : label(t, 'task.memory', mode);
}

export function concurrencyPolicyLabel(t: Translate, policy: ConcurrencyPolicy): string {
  return label(t, 'schedule.concurrency', policy);
}

const FIRE_STATUSES: FireStatus[] = [
  'CREATED',
  'EXECUTION_CREATED',
  'SKIPPED',
  'FAILED_ADMISSION',
  'CANCELLED'
];

export function fireStatusLabel(t: Translate, status: string): string {
  return FIRE_STATUSES.includes(status as FireStatus)
    ? label(t, 'schedule.fire.status', status)
    : unknownLabel(t, 'schedule.fire.status', status);
}

export function skipReasonLabel(t: Translate, reason: FireSkipReason | null): string {
  return reason === null ? '-' : label(t, 'schedule.fire.skipReason', reason);
}

export function dstResolutionLabel(t: Translate, resolution: DstResolution | null): string {
  return resolution === null ? '-' : label(t, 'schedule.fire.dstResolution', resolution);
}

const DST_EVENT_TYPES: DstEventType[] = ['DST_GAP_SKIPPED', 'DST_FOLD_SECOND_SKIPPED'];

export function dstEventLabel(t: Translate, eventType: string): string {
  return DST_EVENT_TYPES.includes(eventType as DstEventType)
    ? label(t, 'schedule.fire.dstEvent', eventType)
    : unknownLabel(t, 'schedule.fire.dstEvent', eventType);
}

export function taskStatusLabel(t: Translate, status: string): string {
  return TASK_STATUSES.includes(status as TaskStatus)
    ? label(t, 'task.status', status)
    : unknownLabel(t, 'task.status', status);
}

export function taskTypeLabel(t: Translate, taskType: string): string {
  const known = ['SKILL', 'REMINDER', 'AGENTIC', 'BATCH'];
  return known.includes(taskType)
    ? label(t, 'task.taskType', taskType)
    : unknownLabel(t, 'task.taskType', taskType);
}

export function waitKindLabel(t: Translate, waitKind: WaitKind | null): string {
  return waitKind === null ? '-' : label(t, 'task.waitKind', waitKind);
}

/** 状态色沿用 design §3.6：ACTIVE 绿、PAUSED 琥珀、NEEDS_ATTENTION 红、终态灰。 */
const SCHEDULE_STATUS_COLORS: Record<ScheduleStatus, StatusTagOption['color']> = {
  ACTIVE: 'green',
  PAUSED: 'amber',
  NEEDS_ATTENTION: 'red',
  ENDED: 'grey',
  CANCELLED: 'grey'
};

export function scheduleStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return Object.fromEntries(
    SCHEDULE_STATUSES.map((status) => [
      status,
      { color: SCHEDULE_STATUS_COLORS[status], label: scheduleStatusLabel(t, status) }
    ])
  );
}

/** 未知状态也给出可读标签（灰色），不用 `StatusTag` 的裸 key 回落。 */
export function scheduleStatusFallback(t: Translate, status: string): StatusTagOption {
  return { color: 'grey', label: scheduleStatusLabel(t, status) };
}

const FIRE_STATUS_COLORS: Record<FireStatus, StatusTagOption['color']> = {
  CREATED: 'grey',
  EXECUTION_CREATED: 'green',
  SKIPPED: 'amber',
  FAILED_ADMISSION: 'red',
  CANCELLED: 'grey'
};

export function fireStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return Object.fromEntries(
    FIRE_STATUSES.map((status) => [
      status,
      { color: FIRE_STATUS_COLORS[status], label: fireStatusLabel(t, status) }
    ])
  );
}

const TASK_STATUS_COLORS: Record<TaskStatus, StatusTagOption['color']> = {
  QUEUED: 'grey',
  RUNNING: 'blue',
  WAITING_EXTERNAL: 'amber',
  WAITING_HUMAN: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

export function taskStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return Object.fromEntries(
    TASK_STATUSES.map((status) => [
      status,
      { color: TASK_STATUS_COLORS[status], label: taskStatusLabel(t, status) }
    ])
  );
}

export function taskStatusFallback(t: Translate, status: string): StatusTagOption {
  return { color: 'grey', label: taskStatusLabel(t, status) };
}

export function budgetSummary(budget: AgenticBudget | null): string {
  if (budget === null) {
    return '-';
  }
  return `steps=${budget.max_steps} · tools=${budget.max_tool_calls} · tokens=${budget.max_tokens}`;
}

/** 快照摘要：只渲染后端白名单字段，不假设原始快照存在。 */
export function snapshotSummary(t: Translate, snapshot: TaskSnapshotSummary): string {
  return [
    `${t('task.detail.snapshotSchema')}=${snapshot.schema_version ?? '-'}`,
    `${t('task.detail.snapshotMode')}=${snapshot.mode ?? '-'}`,
    `${t('task.detail.memoryMode')}=${snapshot.memory_mode ?? '-'}`,
    `${t('task.detail.allowlist')}=${snapshot.allowlist.join(', ') || '-'}`,
    `${t('task.detail.budget')}=${snapshot.budget ? JSON.stringify(snapshot.budget) : '-'}`
  ].join(' · ');
}

export function humanSummary(t: Translate, human: TaskHumanSummary | null): string {
  if (human === null) {
    return '-';
  }
  return [
    `${t('task.detail.humanQuestion')}: ${human.question ?? '-'}`,
    `${t('task.detail.humanDeadline')}: ${human.deadline_at ?? '-'}`,
    t(human.open ? 'task.detail.humanOpen' : 'task.detail.humanClosed')
  ].join(' · ');
}

/** 一行历史：真实 Fire 与 DST 事件是两类事实，合并排序但**不互相冒充**。 */
export type FireHistoryRow =
  | { kind: 'FIRE'; key: string; at: string; fire: FireItem }
  | { kind: 'DST'; key: string; at: string; event: DstEvent };

function timeOf(value: string): number {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function fireHistoryRows(page: FireHistoryPage): FireHistoryRow[] {
  const rows: FireHistoryRow[] = [
    ...page.items.map((fire) => ({
      kind: 'FIRE' as const,
      key: `fire-${fire.fire_id}`,
      at: fire.scheduled_at,
      fire
    })),
    ...page.dst_events.map((event) => ({
      kind: 'DST' as const,
      key: `dst-${event.event_id}`,
      at: event.occurred_at,
      event
    }))
  ];
  return rows.sort((left, right) => timeOf(right.at) - timeOf(left.at));
}

export function isFireRow(row: FireHistoryRow): row is Extract<FireHistoryRow, { kind: 'FIRE' }> {
  return row.kind === 'FIRE';
}

export function dstEventSummary(event: DstEvent): string {
  return `${event.local_date} ${event.local_time} · ${event.timezone}`;
}
