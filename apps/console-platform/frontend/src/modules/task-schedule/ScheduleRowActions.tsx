import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import type { ScheduleListItem } from './services/schedules';
import { useScheduleActions } from './useScheduleActions';

interface ScheduleRowActionsProps {
  schedule: ScheduleListItem;
  onMutated(): void;
  onDeleted(): void;
}

export function ScheduleRowActions({ schedule, onMutated, onDeleted }: ScheduleRowActionsProps) {
  const { t } = useTranslation();
  const actions = useScheduleActions({ onMutated, onDeleted });
  const active = schedule.status === 'ACTIVE';
  if (!active && schedule.status !== 'PAUSED') return <span>-</span>;
  return (
    <div className="table-actions">
      <Button
        theme="borderless"
        data-testid={`schedule-${active ? 'pause' : 'resume'}-${schedule.schedule_id}`}
        disabled={actions.pending !== null}
        loading={actions.pending === (active ? 'pause' : 'resume')}
        onClick={() => void (active ? actions.pause : actions.resume)(schedule.schedule_id)}
      >
        {t(active ? 'schedule.action.pause' : 'schedule.action.resume')}
      </Button>
      <ConfirmAction
        title={t('schedule.confirm.delete')}
        testId={`schedule-delete-${schedule.schedule_id}`}
        onConfirm={() => void actions.remove(schedule.schedule_id)}
      >
        {t('schedule.action.delete')}
      </ConfirmAction>
    </div>
  );
}
