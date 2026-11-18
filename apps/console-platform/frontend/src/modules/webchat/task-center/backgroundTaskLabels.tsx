/**
 * 后台任务的类型/部分失败/投递状态标签（design frontend §3.6；TASK-005）。
 *
 * 只做「稳定枚举 → catalog 词条」映射：未知值保留原始 code 并显示通用文案；颜色只表达
 * 已知语义（类型不标危险，部分失败用琥珀，交付失败用红）。
 */

import { useTranslation } from 'react-i18next';

import { StatusTag, type StatusColor } from '../../../components/common/StatusTag';
import type { TaskArtifactDelivery, TaskResultCounts, TaskType } from '../types';

type Translate = (key: string, options?: Record<string, unknown>) => string;

const TYPE_KEYS: Record<TaskType, string> = {
  SKILL: 'chat.taskCenter.background.type.SKILL',
  REMINDER: 'chat.taskCenter.background.type.REMINDER',
  AGENTIC: 'chat.taskCenter.background.type.AGENTIC',
  BATCH: 'chat.taskCenter.background.type.BATCH'
};

const TYPE_COLORS: Record<TaskType, StatusColor> = {
  SKILL: 'blue',
  REMINDER: 'grey',
  AGENTIC: 'green',
  BATCH: 'amber'
};

const DELIVERY_KEYS: Record<TaskArtifactDelivery['status'], string> = {
  DELIVERED: 'chat.taskCenter.background.delivery.DELIVERED',
  DEGRADED: 'chat.taskCenter.background.delivery.DEGRADED',
  PENDING: 'chat.taskCenter.background.delivery.PENDING',
  UNKNOWN: 'chat.taskCenter.background.delivery.UNKNOWN',
  FAILED: 'chat.taskCenter.background.delivery.FAILED'
};

const DELIVERY_COLORS: Record<TaskArtifactDelivery['status'], StatusColor> = {
  DELIVERED: 'green',
  DEGRADED: 'amber',
  PENDING: 'blue',
  UNKNOWN: 'grey',
  FAILED: 'red'
};

function fallback(t: Translate, value: unknown) {
  return { color: 'grey' as const, label: t('chat.taskCenter.unknownCode', { code: String(value) }) };
}

export function TaskTypeTag(props: { taskType: TaskType | string }) {
  const { t } = useTranslation();
  const options: Record<string, { color: StatusColor; label: string }> = {};
  for (const key of Object.keys(TYPE_KEYS) as TaskType[]) {
    options[key] = { color: TYPE_COLORS[key], label: t(TYPE_KEYS[key]) };
  }
  return (
    <StatusTag status={props.taskType} options={options} fallback={fallback(t, props.taskType)} />
  );
}

/** 部分失败是「完成态 + 失败计数」，不是布尔徽标；`failed=0` 时不渲染。 */
export function PartialFailureTag(props: { counts: TaskResultCounts }) {
  const { t } = useTranslation();
  if (props.counts.failed <= 0) return null;
  return (
    <span
      className="chat-background-partial-failure"
      data-testid="chat-background-partial-failure"
      data-failed={props.counts.failed}
    >
      {t('chat.taskCenter.background.partialFailure', {
        total: props.counts.total,
        failed: props.counts.failed
      })}
    </span>
  );
}

export function DeliveryBadge(props: { delivery: TaskArtifactDelivery }) {
  const { t } = useTranslation();
  const delivery = props.delivery;
  const key = DELIVERY_KEYS[delivery.status];
  const label =
    key === undefined ? String(delivery.status) : t(key);
  return (
    <span
      className="chat-task-artifact-delivery"
      data-testid="chat-task-artifact-delivery"
      data-delivery-status={delivery.status}
      data-delivery-color={DELIVERY_COLORS[delivery.status] ?? 'grey'}
    >
      <span className="chat-task-artifact-delivery-target">{delivery.target_key}</span>
      <span>{label}</span>
      {delivery.error_code === null ? null : (
        <span className="chat-task-artifact-delivery-error">（{delivery.error_code}）</span>
      )}
    </span>
  );
}

/** `deliveries=[]` 是明确的「未请求推送」；字段缺失时调用方不应渲染本组件。 */
export function DeliveryNone() {
  const { t } = useTranslation();
  return (
    <span className="chat-task-artifact-delivery-none" data-testid="chat-task-artifact-delivery-none">
      {t('chat.taskCenter.background.delivery.NONE')}
    </span>
  );
}
