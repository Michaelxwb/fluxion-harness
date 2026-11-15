import { Button } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { Tips } from '../../components/common/Tips';
import { CancelScheduleModal } from './CancelScheduleModal';
import type {
  CancelScope,
  ScheduleActionAdmissionError,
  ScheduleListItem
} from './services/schedules';
import { useScheduleActions } from './useScheduleActions';

interface ScheduleRowActionsProps {
  schedule: ScheduleListItem;
  onMutated(): void;
  onDeleted(): void;
}

/** 恢复时 Admission 失败是 200 回执里的业务事实（不是传输错误）：显式给出修复指引。 */
function reportAdmissionFailure(
  admissionError: ScheduleActionAdmissionError | undefined,
  message: string
): void {
  if (admissionError) {
    Tips.error(`${message}: ${admissionError.repair} (${admissionError.code})`, {
      dedupeKey: `schedule-admission:${admissionError.code}`
    });
  }
}

export function ScheduleRowActions({ schedule, onMutated, onDeleted }: ScheduleRowActionsProps) {
  const { t } = useTranslation();
  const actions = useScheduleActions({ onMutated, onDeleted });
  const [cancelTarget, setCancelTarget] = useState<string | null>(null);
  const pausable = schedule.status === 'ACTIVE';
  const resumable = schedule.status === 'PAUSED' || schedule.status === 'NEEDS_ATTENTION';
  if (!pausable && !resumable) return <span>-</span>;

  const confirmCancel = async (scope: CancelScope): Promise<void> => {
    if (cancelTarget === null) return;
    const result = await actions.cancel(cancelTarget, scope);
    if (result !== null) {
      setCancelTarget(null);
    }
  };

  return (
    <div className="table-actions">
      {pausable ? (
        <Button
          theme="borderless"
          data-testid={`schedule-pause-${schedule.schedule_id}`}
          disabled={actions.pending !== null}
          loading={actions.pending === 'pause'}
          onClick={() => void actions.pause(schedule.schedule_id)}
        >
          {t('schedule.action.pause')}
        </Button>
      ) : (
        <Button
          theme="borderless"
          data-testid={`schedule-resume-${schedule.schedule_id}`}
          disabled={actions.pending !== null}
          loading={actions.pending === 'resume'}
          onClick={() =>
            void actions.resume(schedule.schedule_id).then((result) => {
              reportAdmissionFailure(
                result?.admission_error,
                t('schedule.admissionFailed')
              );
            })
          }
        >
          {t('schedule.action.resume')}
        </Button>
      )}
      <Button
        theme="borderless"
        data-testid={`schedule-cancel-${schedule.schedule_id}`}
        disabled={actions.pending !== null}
        onClick={() => setCancelTarget(schedule.schedule_id)}
      >
        {t('schedule.action.cancel')}
      </Button>
      <ConfirmAction
        type="danger"
        title={t('schedule.confirm.delete')}
        testId={`schedule-delete-${schedule.schedule_id}`}
        onConfirm={() => void actions.remove(schedule.schedule_id)}
      >
        {t('schedule.action.delete')}
      </ConfirmAction>
      <CancelScheduleModal
        scheduleId={cancelTarget}
        loading={actions.pending === 'cancel'}
        onDismiss={() => setCancelTarget(null)}
        onConfirm={(scope) => void confirmCancel(scope)}
      />
    </div>
  );
}
