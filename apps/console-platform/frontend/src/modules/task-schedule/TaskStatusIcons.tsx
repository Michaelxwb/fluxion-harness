import {
  IconAlertCircle,
  IconAlertTriangle,
  IconBolt,
  IconCalendarClock,
  IconHourglass,
  IconMail,
  IconMinus,
  IconMinusCircle,
  IconPause,
  IconSend,
  IconSpin,
  IconTickCircle
} from '@douyinfe/semi-icons';
import { Tooltip } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type {
  DeliveryStatus,
  TaskListItem,
  TaskStatus,
  TriggerType
} from './services/tasks';

/** 一个单元格里并排表达「触发方式 / 任务状态 / 投递状态」三种事实。 */
export type TaskStatusTone = 'success' | 'running' | 'warning' | 'error' | 'muted';

interface StatusIconSpec {
  icon: ReactNode;
  tone: TaskStatusTone;
}

const TRIGGER_ICONS: Record<TriggerType, ReactNode> = {
  IMMEDIATE: <IconBolt />,
  SCHEDULED: <IconCalendarClock />
};

const STATUS_ICONS: Record<TaskStatus, StatusIconSpec> = {
  QUEUED: { icon: <IconHourglass />, tone: 'muted' },
  RUNNING: { icon: <IconSpin />, tone: 'running' },
  WAITING: { icon: <IconPause />, tone: 'warning' },
  COMPLETED: { icon: <IconTickCircle />, tone: 'success' },
  FAILED: { icon: <IconAlertCircle />, tone: 'error' },
  CANCELLED: { icon: <IconMinusCircle />, tone: 'muted' }
};

const DELIVERY_ICONS: Record<DeliveryStatus, StatusIconSpec> = {
  PENDING: { icon: <IconSend />, tone: 'warning' },
  SENT: { icon: <IconMail />, tone: 'success' },
  FAILED: { icon: <IconAlertTriangle />, tone: 'error' },
  NONE: { icon: <IconMinus />, tone: 'muted' }
};

interface StatusIconProps {
  label: string;
  tone: TaskStatusTone;
  testId: string;
  children: ReactNode;
}

function StatusIcon(props: StatusIconProps) {
  return (
    <Tooltip content={props.label}>
      <span
        role="img"
        aria-label={props.label}
        tabIndex={0}
        data-testid={props.testId}
        className={`task-status-icon task-status-${props.tone}`}
      >
        {props.children}
      </span>
    </Tooltip>
  );
}

export function TaskStatusIcons({ task }: { task: TaskListItem }) {
  const { t } = useTranslation();
  const triggerLabel = `${t('task.columns.triggerType')}: ${t(`task.trigger.${task.trigger_type}`)}`;
  const statusLabel = `${t('task.columns.status')}: ${t(`task.status.${task.status}`)}`;
  const deliveryLabel = `${t('task.columns.deliveryStatus')}: ${t(
    `task.delivery.${task.delivery_status}`
  )}`;
  const status = STATUS_ICONS[task.status];
  const delivery = DELIVERY_ICONS[task.delivery_status];
  return (
    <div className="task-status-icons" data-testid={`task-status-icons-${task.task_id}`}>
      <StatusIcon
        label={triggerLabel}
        tone="muted"
        testId={`task-trigger-${task.task_id}`}
      >
        {TRIGGER_ICONS[task.trigger_type]}
      </StatusIcon>
      <StatusIcon
        label={statusLabel}
        tone={status.tone}
        testId={`task-status-${task.task_id}`}
      >
        {status.icon}
      </StatusIcon>
      <StatusIcon
        label={deliveryLabel}
        tone={delivery.tone}
        testId={`task-delivery-${task.task_id}`}
      >
        {delivery.icon}
      </StatusIcon>
    </div>
  );
}
