import { Button, Modal, Select } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { EntityLink } from '../../components/common/EntityLink';
import { StatusTag, type StatusTagOption } from '../../components/common/StatusTag';
import { ScheduleDetailSideSheet } from './ScheduleDetailSideSheet';
import { TaskDetailSideSheet } from './TaskDetailSideSheet';
import { listSchedules, type ScheduleListItem, type ScheduleListParams } from './services/schedules';

const DEFAULT_PARAMS: ScheduleListParams = { page: 1, page_size: 10 };
const SCHEDULE_STATUSES = ['ACTIVE', 'PAUSED', 'COMPLETED', 'MISSED'] as const;

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  ACTIVE: 'green',
  PAUSED: 'amber',
  COMPLETED: 'grey',
  MISSED: 'red'
};

export function SchedulePage() {
  const { t } = useTranslation();
  const [params, setParams] = useState<ScheduleListParams>(DEFAULT_PARAMS);
  const [items, setItems] = useState<ScheduleListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [helpVisible, setHelpVisible] = useState(false);
  const [detailScheduleId, setDetailScheduleId] = useState<string | null>(null);
  const [historyTaskId, setHistoryTaskId] = useState<string | null>(null);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listSchedules(params);
      if (current !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
      setFailed(false);
    } catch {
      if (current === requestSeq.current) {
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const statusOptions = Object.fromEntries(
    SCHEDULE_STATUSES.map((status) => [
      status,
      { color: STATUS_COLORS[status] ?? 'grey', label: t(`schedule.status.${status}`) }
    ])
  );

  return (
    <>
      <PageHeader title={t('schedule.title')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button data-testid="schedule-help" onClick={() => setHelpVisible(true)}>
              {t('schedule.help.action')}
            </Button>
          }
          search={
            <>
              <Select
                data-testid="schedule-filter-status"
                value={params.status ?? undefined}
                style={{ width: 150 }}
                showClear
                placeholder={t('schedule.filter.status')}
                optionList={SCHEDULE_STATUSES.map((status) => ({
                  value: status,
                  label: t(`schedule.status.${status}`)
                }))}
                onChange={(value) =>
                  setParams((prev) => ({
                    ...prev,
                    status: (value as ScheduleListParams['status']) ?? undefined,
                    page: 1
                  }))
                }
              />
              <Button
                data-testid="schedule-reset"
                onClick={() => setParams(DEFAULT_PARAMS)}
              >
                {t('common.reset')}
              </Button>
              <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
            </>
          }
        />
        <RemoteTable<ScheduleListItem>
          rowKey="schedule_id"
          loading={loading}
          columns={[
            {
              title: t('schedule.columns.name'),
              dataIndex: 'name',
              render: (value: string, record: ScheduleListItem) => (
                <EntityLink
                  testId={`schedule-link-${record.schedule_id}`}
                  onClick={() => setDetailScheduleId(record.schedule_id)}
                >
                  {value}
                </EntityLink>
              )
            },
            { title: t('schedule.columns.agent'), dataIndex: 'agent_id' },
            { title: t('schedule.columns.intent'), dataIndex: 'intent_key' },
            {
              title: t('schedule.columns.scheduleType'),
              dataIndex: 'schedule_type'
            },
            {
              title: t('schedule.columns.cron'),
              dataIndex: 'cron_expr',
              render: (_: unknown, record: ScheduleListItem) =>
                record.cron_expr ?? (record.run_at ? <DateTimeText value={record.run_at} /> : '-')
            },
            { title: t('schedule.columns.timezone'), dataIndex: 'timezone' },
            {
              title: t('schedule.columns.status'),
              dataIndex: 'status',
              render: (value: string) => <StatusTag status={value} options={statusOptions} />
            },
            {
              title: t('schedule.columns.nextFireAt'),
              dataIndex: 'next_fire_at',
              render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
            },
            {
              title: t('schedule.columns.lastFireAt'),
              dataIndex: 'last_fire_at',
              render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
            },
            {
              title: t('schedule.columns.updateTime'),
              dataIndex: 'update_time',
              render: (value: string) => <DateTimeText value={value} />
            }
          ]}
          dataSource={items}
          page={params.page ?? 1}
          pageSize={params.page_size ?? 10}
          total={total}
          onPageChange={(page) => setParams((prev) => ({ ...prev, page }))}
          onPageSizeChange={(page_size) => setParams((prev) => ({ ...prev, page: 1, page_size }))}
          empty={
            failed ? (
              <ErrorState onRetry={() => void reload()} />
            ) : (
              <EmptyState title={t('common.empty')} description={t('common.emptyHint')} />
            )
          }
        />
      </PageSection>
      <Modal
        visible={helpVisible}
        title={t('schedule.help.title')}
        footer={null}
        onCancel={() => setHelpVisible(false)}
      >
        <p data-testid="schedule-help-content">{t('schedule.help.content')}</p>
      </Modal>
      <ScheduleDetailSideSheet
        scheduleId={detailScheduleId}
        onCancel={() => setDetailScheduleId(null)}
        onMutated={() => void reload()}
        onOpenTask={(taskId) => setHistoryTaskId(taskId)}
      />
      <TaskDetailSideSheet
        taskId={historyTaskId}
        onCancel={() => setHistoryTaskId(null)}
        onSelectTask={(taskId) => setHistoryTaskId(taskId)}
      />
    </>
  );
}
