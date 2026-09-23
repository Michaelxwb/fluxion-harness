import { Button, Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { ErrorState } from '../../components/common/ErrorState';
import { StatusTag, type StatusTagOption } from '../../components/common/StatusTag';
import { ScheduleHistoryTable } from './ScheduleHistoryTable';
import { getSchedule, type ScheduleDetail } from './services/schedules';
import { useScheduleActions } from './useScheduleActions';

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  ACTIVE: 'green',
  PAUSED: 'amber',
  COMPLETED: 'grey',
  MISSED: 'red'
};

export interface ScheduleDetailSideSheetProps {
  scheduleId: string | null;
  onCancel(): void;
  onMutated?(scheduleId: string): void;
  onOpenTask?(taskId: string): void;
}

export function ScheduleDetailSideSheet(props: ScheduleDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<ScheduleDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    if (!props.scheduleId) {
      return;
    }
    const current = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const value = await getSchedule(props.scheduleId);
      if (current === requestSeq.current) {
        setDetail(value);
      }
    } catch {
      if (current === requestSeq.current) {
        setDetail(null);
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [props.scheduleId]);

  useEffect(() => {
    setActiveTab('basic');
    if (props.scheduleId) {
      void load();
    } else {
      setDetail(null);
      setFailed(false);
    }
  }, [props.scheduleId, load]);

  const statusOptions = Object.fromEntries(
    Object.entries(STATUS_COLORS).map(([status, color]) => [
      status,
      { color, label: t(`schedule.status.${status}`) }
    ])
  );

  const actionsState = useScheduleActions({
    onMutated: (scheduleId) => {
      void load();
      props.onMutated?.(scheduleId);
    },
    onDeleted: (scheduleId) => {
      props.onMutated?.(scheduleId);
      props.onCancel();
    }
  });

  const actions = detail ? (
    <div data-testid="schedule-detail-actions" style={{ display: 'flex', gap: 8 }}>
      {detail.status === 'ACTIVE' ? (
        <Button
          data-testid="schedule-pause"
          loading={actionsState.pending === 'pause'}
          onClick={() => void actionsState.pause(detail.schedule_id)}
        >
          {t('schedule.action.pause')}
        </Button>
      ) : null}
      {detail.status === 'PAUSED' ? (
        <Button
          data-testid="schedule-resume"
          loading={actionsState.pending === 'resume'}
          onClick={() => void actionsState.resume(detail.schedule_id)}
        >
          {t('schedule.action.resume')}
        </Button>
      ) : null}
      {detail.status === 'ACTIVE' || detail.status === 'PAUSED' ? (
        <ConfirmAction
          danger
          title={t('schedule.confirm.delete')}
          testId="schedule-delete"
          onConfirm={() => void actionsState.remove(detail.schedule_id)}
        >
          {t('schedule.action.delete')}
        </ConfirmAction>
      ) : null}
    </div>
  ) : undefined;

  return (
    <DetailSideSheet
      visible={props.scheduleId !== null}
      title={detail?.name ?? props.scheduleId ?? ''}
      subtitle={detail ? `${t(`schedule.status.${detail.status}`)} · ${detail.intent_key}` : t('schedule.detail.basic')}
      actions={actions}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onCancel}
      notice={failed ? <ErrorState onRetry={() => void load()} /> : undefined}
    >
      <Tabs.TabPane itemKey="basic" tab={t('schedule.detail.basic')}>
        {loading ? <Spin /> : null}
        {detail ? (
          <DetailGrid
            items={[
              { label: t('schedule.columns.name'), value: detail.name },
              {
                label: t('schedule.columns.status'),
                value: <StatusTag status={detail.status} options={statusOptions} />
              },
              { label: t('schedule.columns.intent'), value: detail.intent_key },
              { label: t('schedule.columns.scheduleType'), value: detail.schedule_type },
              {
                label: t('schedule.columns.cron'),
                value:
                  detail.schedule_type === 'CRON'
                    ? detail.cron_expr ?? '-'
                    : detail.run_at
                      ? <DateTimeText value={detail.run_at} />
                      : '-'
              },
              { label: t('schedule.columns.timezone'), value: detail.timezone },
              { label: 'revision', value: detail.revision },
              {
                label: t('schedule.columns.nextFireAt'),
                value: (
                  <span data-testid="schedule-detail-next-fire">
                    {detail.next_fire_at ? <DateTimeText value={detail.next_fire_at} /> : '-'}
                  </span>
                )
              },
              {
                label: t('schedule.columns.lastFireAt'),
                value: detail.last_fire_at ? <DateTimeText value={detail.last_fire_at} /> : '-'
              },
              {
                label: 'completed_at',
                value: detail.completed_at ? <DateTimeText value={detail.completed_at} /> : '-'
              },
              {
                label: t('task.detail.error'),
                value: (
                  <span data-testid="schedule-detail-skip">
                    {detail.last_error_code ? `${detail.last_error_code}: ` : ''}
                    {detail.last_error_message ?? '-'}
                    {detail.last_skipped_at ? (
                      <>
                        {' · '}
                        <DateTimeText value={detail.last_skipped_at} />
                      </>
                    ) : null}
                  </span>
                )
              }
            ]}
          />
        ) : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="history" tab={t('schedule.detail.history')}>
        {detail && activeTab === 'history' ? (
          <ScheduleHistoryTable
            scheduleId={detail.schedule_id}
            onOpenTask={(taskId) => props.onOpenTask?.(taskId)}
          />
        ) : null}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
