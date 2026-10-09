import { Button, Modal, Select } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';

import { ListActionButton } from '../../components/common/ListActionButton';
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

const DEFAULT_PARAMS: ScheduleListParams = { page: 1, page_size: 15 };
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

  // 深链（概览「下一批定时触发」= `/schedules?scheduleId=…`）：一次性初值，不回写 URL。
  // 目标已删/越权时由详情侧渲染 404 态，不静默落回空列表（e2e E-04）。
  const [searchParams] = useSearchParams();
  const deepLinkScheduleId = searchParams.get('scheduleId');

  useEffect(() => {
    if (deepLinkScheduleId) {
      setDetailScheduleId(deepLinkScheduleId);
    }
  }, [deepLinkScheduleId]);

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
              <ListActionButton
                data-testid="schedule-reset"
                onClick={() => setParams(DEFAULT_PARAMS)}
                action="reset"
              />
              <ListActionButton onClick={() => void reload()} action="refresh" />
            </>
          }
        />
        <RemoteTable<ScheduleListItem>
          rowKey="schedule_id"
          className="app-table-nowrap"
          scroll={{ x: 1820 }}
          loading={loading}
          columns={[
            {
              title: t('schedule.columns.name'),
              dataIndex: 'name',
              width: 160,
              render: (value: string, record: ScheduleListItem) => (
                <EntityLink
                  testId={`schedule-link-${record.schedule_id}`}
                  onClick={() => setDetailScheduleId(record.schedule_id)}
                >
                  {value}
                </EntityLink>
              )
            },
            {
              title: t('schedule.columns.agent'),
              dataIndex: 'agent_id',
              width: 190,
              render: (value: string) => <span title={value}>{value}</span>
            },
            {
              title: t('schedule.columns.actorUser'),
              dataIndex: 'actor_user_id',
              width: 190,
              render: (value: string) => <span title={value}>{value}</span>
            },
            {
              title: t('schedule.columns.skill'),
              dataIndex: 'skill_id',
              width: 190,
              render: (value: string) => <span title={value}>{value}</span>
            },
            { title: t('schedule.columns.intent'), dataIndex: 'intent_key', width: 90 },
            {
              title: t('schedule.columns.scheduleType'),
              dataIndex: 'schedule_type',
              width: 90
            },
            {
              title: t('schedule.columns.cron'),
              dataIndex: 'cron_expr',
              width: 140,
              render: (_: unknown, record: ScheduleListItem) =>
                record.cron_expr ?? (record.run_at ? <DateTimeText value={record.run_at} /> : '-')
            },
            { title: t('schedule.columns.timezone'), dataIndex: 'timezone', width: 110 },
            {
              title: t('schedule.columns.status'),
              dataIndex: 'status',
              width: 92,
              render: (value: string) => <StatusTag status={value} options={statusOptions} />
            },
            {
              title: t('schedule.columns.nextFireAt'),
              dataIndex: 'next_fire_at',
              width: 185,
              render: (value: string | null, record: ScheduleListItem) => (
                <span data-testid={`schedule-next-fire-${record.schedule_id}`}>
                  {value ? <DateTimeText value={value} /> : '-'}
                </span>
              )
            },
            {
              title: t('schedule.columns.lastFireAt'),
              dataIndex: 'last_fire_at',
              width: 185,
              render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
            },
            {
              title: t('schedule.columns.updateTime'),
              dataIndex: 'update_time',
              width: 185,
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
        centered
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
        closeOnEsc={historyTaskId === null}
      />
      <TaskDetailSideSheet
        taskId={historyTaskId}
        onCancel={() => setHistoryTaskId(null)}
        onSelectTask={(taskId) => setHistoryTaskId(taskId)}
        closeOnEsc
      />
    </>
  );
}
