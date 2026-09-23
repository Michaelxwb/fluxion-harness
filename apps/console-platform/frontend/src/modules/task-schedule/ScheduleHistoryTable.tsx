import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { EntityLink } from '../../components/common/EntityLink';
import { ErrorState } from '../../components/common/ErrorState';
import { RemoteTable } from '../../components/common/RemoteTable';
import { StatusTag, type StatusTagOption } from '../../components/common/StatusTag';
import { listScheduleTasks, type TaskListItem } from './services/tasks';

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  QUEUED: 'grey',
  RUNNING: 'blue',
  WAITING: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

export interface ScheduleHistoryTableProps {
  scheduleId: string;
  onOpenTask(taskId: string): void;
}

/** 历史 Task：按 schedule_id 查询（单次请求，不分页展开 N+1）。 */
export function ScheduleHistoryTable(props: ScheduleHistoryTableProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<TaskListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const result = await listScheduleTasks(props.scheduleId, { page, page_size: pageSize });
      if (current !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setTotal(result.total);
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
  }, [props.scheduleId, page, pageSize]);

  useEffect(() => {
    setPage(1);
  }, [props.scheduleId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const statusOptions = Object.fromEntries(
    Object.entries(STATUS_COLORS).map(([status, color]) => [
      status,
      { color, label: t(`task.status.${status}`) }
    ])
  );

  return (
    <div data-testid="schedule-history">
      <RemoteTable<TaskListItem>
        rowKey="task_id"
        loading={loading}
        columns={[
          {
            title: t('task.columns.taskId'),
            dataIndex: 'task_id',
            render: (value: string) => (
              <EntityLink testId={`history-task-${value}`} onClick={() => props.onOpenTask(value)}>
                {value}
              </EntityLink>
            )
          },
          {
            title: t('task.columns.status'),
            dataIndex: 'status',
            render: (value: string) => <StatusTag status={value} options={statusOptions} />
          },
          {
            title: t('task.columns.deliveryStatus'),
            dataIndex: 'delivery_status',
            render: (value: string) => t(`task.delivery.${value}`)
          },
          {
            title: t('task.columns.createTime'),
            dataIndex: 'create_time',
            render: (value: string) => <DateTimeText value={value} />
          }
        ]}
        dataSource={items}
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPage(1);
          setPageSize(size);
        }}
        empty={
          failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : (
            <EmptyState title={t('common.empty')} description={t('schedule.detail.historyEmpty')} />
          )
        }
      />
    </div>
  );
}
