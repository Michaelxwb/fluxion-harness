/**
 * 最近后台任务（设计 §3.3 CMP-04 / §3.4 / §3.6）。
 *
 * 展示组件：props-in / events-out（`onOpenTask`/`onViewAll` 由页面接线，本组件不导航）。
 * 局部清单（≤5 行、无分页、无工具栏）用 Semi `Table` 并关闭分页——`RemoteTable` 是**列表页**的
 * 约定（左上操作 + 右上筛选 + 右下分页），不适用于仪表盘内的预览块。
 * 列头复用既有 `task.columns.*` 词条；状态标签沿用 task-schedule 模块的 `STATUS_COLORS` 口径。
 */

import { Skeleton, Table, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { EmptyState } from '../../../components/common/EmptyState';
import { StatusTag, type StatusTagOption } from '../../../components/common/StatusTag';
import type { RecentTaskItem, RecentTaskListProps } from '../types';

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  QUEUED: 'grey',
  RUNNING: 'blue',
  WAITING: 'amber',
  COMPLETED: 'green',
  SUCCEEDED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

const DELIVERY_COLORS: Record<string, StatusTagOption['color']> = {
  PENDING: 'amber',
  SENT: 'green',
  FAILED: 'red',
  NONE: 'grey'
};

const TRIGGER_LABELS: Record<RecentTaskItem['triggerType'], string> = {
  IMMEDIATE: 'task.trigger.IMMEDIATE',
  SCHEDULED: 'task.trigger.SCHEDULED'
};

export function RecentTaskList({ items, loading, onOpenTask, onViewAll }: RecentTaskListProps) {
  const { t } = useTranslation();

  if (loading) {
    return <Skeleton loading placeholder={<Skeleton.Title style={{ width: '100%' }} />} />;
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title={t('overview.recentTasks.empty')}
        action={
          <Typography.Text link onClick={onViewAll} data-testid="recent-tasks-view-all">
            {t('overview.viewAll')}
          </Typography.Text>
        }
      />
    );
  }

  const statusOptions = Object.fromEntries(
    Object.entries(STATUS_COLORS).map(([key, color]) => [
      key,
      { color, label: t(`task.status.${key === 'SUCCEEDED' ? 'COMPLETED' : key}`) }
    ])
  );
  const deliveryOptions = Object.fromEntries(
    Object.entries(DELIVERY_COLORS).map(([key, color]) => [
      key,
      { color, label: t(`task.delivery.${key}`) }
    ])
  );

  const columns = [
    { title: t('task.columns.taskId'), dataIndex: 'taskId' },
    { title: t('task.columns.agent'), dataIndex: 'agent' },
    { title: t('task.columns.status'), dataIndex: 'status' },
    { title: t('task.columns.triggerType'), dataIndex: 'triggerType' },
    { title: t('task.columns.deliveryStatus'), dataIndex: 'deliveryStatus' },
    { title: t('task.columns.createTime'), dataIndex: 'createTime' }
  ];

  const dataSource = items.map((item) => ({
    key: item.taskId,
    taskId: (
      <Typography.Text
        link
        onClick={() => onOpenTask(item.taskId)}
        data-testid={`recent-task-${item.taskId}`}
      >
        {item.taskId}
      </Typography.Text>
    ),
    agent: item.agentName ?? t('common.empty'),
    status: <StatusTag status={item.status} options={statusOptions} />,
    triggerType: t(TRIGGER_LABELS[item.triggerType]),
    deliveryStatus: <StatusTag status={item.deliveryStatus} options={deliveryOptions} />,
    createTime: <DateTimeText value={item.createTime} />
  }));

  return (
    <>
      <Table columns={columns} dataSource={dataSource} pagination={false} rowKey="key" />
      <Typography.Text link onClick={onViewAll} data-testid="recent-tasks-view-all">
        {t('overview.viewAll')}
      </Typography.Text>
    </>
  );
}
