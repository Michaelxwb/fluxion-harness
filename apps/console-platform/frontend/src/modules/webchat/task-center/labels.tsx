/**
 * 任务中心的状态/模式/等待类型标签（design frontend §3.4、§3.6；TASK-027）。
 *
 * 只做「稳定枚举 → catalog 词条」的映射：未知值不隐藏、不猜语义，统一显示原始 code +
 * 通用文案（可读兜底）。颜色只用于已知状态：ACTIVE 绿、PAUSED 琥珀、NEEDS_ATTENTION 红、
 * ENDED/CANCELLED 灰；AGENTIC 不标「危险」。
 */

import { useTranslation } from 'react-i18next';

import { StatusTag, type StatusColor, type StatusTagOption } from '../../../components/common/StatusTag';
import type {
  FireStatus,
  ScheduleEndReason,
  ScheduleExecutionStatus,
  ScheduleMode,
  ScheduleStatus,
  ScheduleWaitKind
} from '../types';

type Translate = (key: string, options?: Record<string, unknown>) => string;

const MODE_KEYS: Record<ScheduleMode, string> = {
  REMINDER: 'chat.taskCenter.mode.REMINDER',
  SKILL: 'chat.taskCenter.mode.SKILL',
  AGENTIC: 'chat.taskCenter.mode.AGENTIC'
};

const MODE_COLORS: Record<ScheduleMode, StatusColor> = {
  REMINDER: 'blue',
  SKILL: 'green',
  AGENTIC: 'grey'
};

const STATUS_KEYS: Record<ScheduleStatus, string> = {
  ACTIVE: 'chat.taskCenter.status.ACTIVE',
  PAUSED: 'chat.taskCenter.status.PAUSED',
  NEEDS_ATTENTION: 'chat.taskCenter.status.NEEDS_ATTENTION',
  ENDED: 'chat.taskCenter.status.ENDED',
  CANCELLED: 'chat.taskCenter.status.CANCELLED'
};

const STATUS_COLORS: Record<ScheduleStatus, StatusColor> = {
  ACTIVE: 'green',
  PAUSED: 'amber',
  NEEDS_ATTENTION: 'red',
  ENDED: 'grey',
  CANCELLED: 'grey'
};

const END_REASON_KEYS: Record<ScheduleEndReason, string> = {
  COMPLETED: 'chat.taskCenter.endReason.COMPLETED',
  MISSED: 'chat.taskCenter.endReason.MISSED',
  EXHAUSTED: 'chat.taskCenter.endReason.EXHAUSTED',
  ADMISSION_FAILED: 'chat.taskCenter.endReason.ADMISSION_FAILED',
  USER_CANCELLED: 'chat.taskCenter.endReason.USER_CANCELLED'
};

const FIRE_STATUS_KEYS: Record<FireStatus, string> = {
  CREATED: 'chat.taskCenter.fire.status.CREATED',
  EXECUTION_CREATED: 'chat.taskCenter.fire.status.EXECUTION_CREATED',
  SKIPPED: 'chat.taskCenter.fire.status.SKIPPED',
  FAILED_ADMISSION: 'chat.taskCenter.fire.status.FAILED_ADMISSION',
  CANCELLED: 'chat.taskCenter.fire.status.CANCELLED'
};

const FIRE_STATUS_COLORS: Record<FireStatus, StatusColor> = {
  CREATED: 'blue',
  EXECUTION_CREATED: 'green',
  SKIPPED: 'grey',
  FAILED_ADMISSION: 'red',
  CANCELLED: 'grey'
};

const EXECUTION_STATUS_KEYS: Record<ScheduleExecutionStatus, string> = {
  QUEUED: 'chat.taskCenter.execution.status.QUEUED',
  RUNNING: 'chat.taskCenter.execution.status.RUNNING',
  WAITING_EXTERNAL: 'chat.taskCenter.execution.status.WAITING_EXTERNAL',
  WAITING_HUMAN: 'chat.taskCenter.execution.status.WAITING_HUMAN',
  COMPLETED: 'chat.taskCenter.execution.status.COMPLETED',
  FAILED: 'chat.taskCenter.execution.status.FAILED',
  CANCELLED: 'chat.taskCenter.execution.status.CANCELLED'
};

const EXECUTION_STATUS_COLORS: Record<ScheduleExecutionStatus, StatusColor> = {
  QUEUED: 'grey',
  RUNNING: 'blue',
  WAITING_EXTERNAL: 'amber',
  WAITING_HUMAN: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

const WAIT_KIND_KEYS: Record<ScheduleWaitKind, string> = {
  ASYNC_OPERATION: 'chat.taskCenter.wait.ASYNC_OPERATION',
  EXTERNAL_POLL: 'chat.taskCenter.wait.EXTERNAL_POLL',
  BATCH_FANIN: 'chat.taskCenter.wait.BATCH_FANIN'
};

function options<T extends string>(
  keys: Record<T, string>,
  colors: Record<T, StatusColor>,
  t: Translate
): Record<string, StatusTagOption> {
  const mapped: Record<string, StatusTagOption> = {};
  for (const key of Object.keys(keys) as T[]) {
    mapped[key] = { color: colors[key], label: t(keys[key]) };
  }
  return mapped;
}

function fallback(t: Translate, status: unknown): StatusTagOption {
  return { color: 'grey', label: t('chat.taskCenter.unknownCode', { code: String(status) }) };
}

export function ModeTag(props: { mode: ScheduleMode | string }) {
  const { t } = useTranslation();
  return (
    <StatusTag
      status={props.mode}
      options={options(MODE_KEYS, MODE_COLORS, t)}
      fallback={fallback(t, props.mode)}
    />
  );
}

export function ScheduleStatusTag(props: { status: ScheduleStatus | string }) {
  const { t } = useTranslation();
  return (
    <StatusTag
      status={props.status}
      options={options(STATUS_KEYS, STATUS_COLORS, t)}
      fallback={fallback(t, props.status)}
    />
  );
}

export function FireStatusTag(props: { status: FireStatus | string }) {
  const { t } = useTranslation();
  return (
    <StatusTag
      status={props.status}
      options={options(FIRE_STATUS_KEYS, FIRE_STATUS_COLORS, t)}
      fallback={fallback(t, props.status)}
    />
  );
}

export function ExecutionStatusTag(props: { status: ScheduleExecutionStatus | string }) {
  const { t } = useTranslation();
  return (
    <StatusTag
      status={props.status}
      options={options(EXECUTION_STATUS_KEYS, EXECUTION_STATUS_COLORS, t)}
      fallback={fallback(t, props.status)}
    />
  );
}

export function endReasonLabel(t: Translate, reason: ScheduleEndReason | string): string {
  const key = END_REASON_KEYS[reason as ScheduleEndReason];
  return key === undefined ? t('chat.taskCenter.unknownCode', { code: reason }) : t(key);
}

export function waitKindLabel(t: Translate, kind: ScheduleWaitKind | string): string {
  const key = WAIT_KIND_KEYS[kind as ScheduleWaitKind];
  return key === undefined ? t('chat.taskCenter.unknownCode', { code: kind }) : t(key);
}
