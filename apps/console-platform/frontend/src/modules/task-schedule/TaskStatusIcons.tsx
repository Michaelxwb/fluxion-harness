import {
  IconAlertCircle,
  IconBolt,
  IconCalendarClock,
  IconHourglass,
  IconMinusCircle,
  IconPause,
  IconSpin,
  IconTickCircle,
  IconUser
} from '@douyinfe/semi-icons';
import { Tooltip } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { taskStatusLabel, type Translate } from './scheduleCatalog';
import type { TaskListItem, TaskStatus, TriggerType } from './services/tasks';

/** 一个单元格里并排表达「触发方式 / 任务状态」两种事实。 */
export type TaskStatusTone = 'success' | 'running' | 'warning' | 'error' | 'muted';

interface StatusIconSpec {
  icon: ReactNode;
  tone: TaskStatusTone;
}

const TRIGGER_ICONS: Record<TriggerType, ReactNode> = {
  IMMEDIATE: <IconBolt />,
  SCHEDULED: <IconCalendarClock />
};

/**
 * v2 把「等待」拆成两类不同事实：`WAITING_EXTERNAL`（异步/轮询/聚合）与 `WAITING_HUMAN`
 * （等待用户回答）。二者取消规则不同，图标也不同，不再合并成一个 `WAITING`。
 */
const STATUS_ICONS: Record<TaskStatus, StatusIconSpec> = {
  QUEUED: { icon: <IconHourglass />, tone: 'muted' },
  RUNNING: { icon: <IconSpin />, tone: 'running' },
  WAITING_EXTERNAL: { icon: <IconPause />, tone: 'warning' },
  WAITING_HUMAN: { icon: <IconUser />, tone: 'warning' },
  COMPLETED: { icon: <IconTickCircle />, tone: 'success' },
  FAILED: { icon: <IconAlertCircle />, tone: 'error' },
  CANCELLED: { icon: <IconMinusCircle />, tone: 'muted' }
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
  const translate: Translate = (key) => t(key);
  const triggerLabel = `${t('task.columns.triggerType')}: ${t(`task.trigger.${task.trigger_type}`)}`;
  const statusLabel = `${t('task.columns.status')}: ${taskStatusLabel(translate, task.status)}`;
  const status = STATUS_ICONS[task.status] ?? {
    icon: <IconAlertCircle />,
    tone: 'muted' as const
  };
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
    </div>
  );
}
