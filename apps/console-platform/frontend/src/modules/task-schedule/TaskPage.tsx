import { Button, DatePicker, Modal, Select } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { StatusTag, type StatusTagOption } from '../../components/common/StatusTag';
import { EntityLink } from '../../components/common/EntityLink';
import { TaskDetailSideSheet } from './TaskDetailSideSheet';
import { listTasks, type TaskListItem, type TaskListParams } from './services/tasks';

const DEFAULT_PARAMS: TaskListParams = { page: 1, page_size: 10 };

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  QUEUED: 'grey',
  RUNNING: 'blue',
  WAITING: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

const TRIGGER_TYPES = ['IMMEDIATE', 'SCHEDULED'] as const;
const TASK_STATUSES = ['QUEUED', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED', 'CANCELLED'] as const;

function toIsoDate(value: Date | null | undefined): string | undefined {
  return value ? value.toISOString() : undefined;
}

/** dateRange 的结束日是当天 00:00；截止时间筛选「起止含端点」须包含结束日整天。 */
function toIsoEndOfDay(value: Date | null | undefined): string | undefined {
  if (!value) {
    return undefined;
  }
  const end = new Date(value);
  end.setHours(23, 59, 59, 999);
  return end.toISOString();
}

export function TaskPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState<TaskListParams>(DEFAULT_PARAMS);
  const [items, setItems] = useState<TaskListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [helpVisible, setHelpVisible] = useState(false);
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listTasks(params);
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
    TASK_STATUSES.map((status) => [
      status,
      { color: STATUS_COLORS[status] ?? 'grey', label: t(`task.status.${status}`) }
    ])
  );

  return (
    <>
      <PageHeader title={t('task.title')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button data-testid="task-help" onClick={() => setHelpVisible(true)}>
              {t('task.help.action')}
            </Button>
          }
          search={
            <>
              <Select
                data-testid="task-filter-status"
                value={params.status ?? undefined}
                style={{ width: 150 }}
                showClear
                placeholder={t('task.filter.status')}
                optionList={TASK_STATUSES.map((status) => ({
                  value: status,
                  label: t(`task.status.${status}`)
                }))}
                onChange={(value) =>
                  setParams((prev) => ({
                    ...prev,
                    status: (value as TaskListParams['status']) ?? undefined,
                    page: 1
                  }))
                }
              />
              <Select
                data-testid="task-filter-trigger"
                value={params.trigger_type ?? undefined}
                style={{ width: 150 }}
                showClear
                placeholder={t('task.filter.triggerType')}
                optionList={TRIGGER_TYPES.map((trigger) => ({
                  value: trigger,
                  label: t(`task.trigger.${trigger}`)
                }))}
                onChange={(value) =>
                  setParams((prev) => ({
                    ...prev,
                    trigger_type: (value as TaskListParams['trigger_type']) ?? undefined,
                    page: 1
                  }))
                }
              />
              <DatePicker
                data-testid="task-filter-deadline"
                type="dateRange"
                style={{ width: 260 }}
                placeholder={[t('task.filter.deadlineFrom'), t('task.filter.deadlineTo')]}
                onChange={(value) => {
                  const range = Array.isArray(value) ? (value as Date[]) : [];
                  setParams((prev) => ({
                    ...prev,
                    deadline_from: toIsoDate(range[0]),
                    deadline_to: toIsoEndOfDay(range[1]),
                    page: 1
                  }));
                }}
              />
              <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
            </>
          }
        />
        <RemoteTable<TaskListItem>
          rowKey="task_id"
          loading={loading}
          columns={[
            {
              title: t('task.columns.taskId'),
              dataIndex: 'task_id',
              render: (value: string) => (
                <EntityLink testId={`task-link-${value}`} onClick={() => setDetailTaskId(value)}>
                  {value}
                </EntityLink>
              )
            },
            { title: t('task.columns.agent'), dataIndex: 'agent_id' },
            { title: t('task.columns.intent'), dataIndex: 'intent_key' },
            {
              title: t('task.columns.status'),
              dataIndex: 'status',
              render: (value: string) => <StatusTag status={value} options={statusOptions} />
            },
            {
              title: t('task.columns.triggerType'),
              dataIndex: 'trigger_type',
              render: (value: string) => t(`task.trigger.${value}`)
            },
            {
              title: t('task.columns.deliveryStatus'),
              dataIndex: 'delivery_status',
              render: (value: string) => t(`task.delivery.${value}`)
            },
            {
              title: t('task.columns.deadlineAt'),
              dataIndex: 'deadline_at',
              render: (value: string) => <DateTimeText value={value} />
            },
            {
              title: t('task.columns.error'),
              dataIndex: 'error_message',
              render: (_: unknown, record: TaskListItem) => record.error_message ?? record.error_code ?? '-'
            },
            {
              title: t('task.columns.createTime'),
              dataIndex: 'create_time',
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
        title={t('task.help.title')}
        footer={null}
        onCancel={() => setHelpVisible(false)}
      >
        <p data-testid="task-help-content">{t('task.help.content')}</p>
      </Modal>
      <TaskDetailSideSheet
        taskId={detailTaskId}
        onCancel={() => setDetailTaskId(null)}
        onSelectTask={(taskId) => setDetailTaskId(taskId)}
        onMutated={() => void reload()}
      />
    </>
  );
}
