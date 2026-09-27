/**
 * 下一批定时触发（设计 §3.3 CMP-05 / §3.4 / §3.6）。
 *
 * 展示组件：props-in / events-out（`onOpenSchedule`/`onViewAll` 由页面接线）。
 * 列头与状态词条复用既有 `schedule.columns.*` / `schedule.status.*`；局部清单用 Semi `Table`
 * 关闭分页（同 `RecentTaskList`：`RemoteTable` 是列表页约定，不适用于仪表盘预览块）。
 */

import { Skeleton, Table, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { EmptyState } from '../../../components/common/EmptyState';
import { StatusTag, type StatusTagOption } from '../../../components/common/StatusTag';
import type { NextScheduleItem, NextScheduleListProps } from '../types';

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  ACTIVE: 'green',
  PAUSED: 'grey',
  MISSED: 'red',
  COMPLETED: 'grey'
};

export function NextScheduleList({
  items,
  loading,
  onOpenSchedule,
  onViewAll
}: NextScheduleListProps) {
  const { t } = useTranslation();

  if (loading) {
    return <Skeleton loading placeholder={<Skeleton.Title style={{ width: '100%' }} />} />;
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title={t('overview.nextSchedules.empty')}
        action={
          <Typography.Text link onClick={onViewAll} data-testid="next-schedules-view-all">
            {t('overview.viewAll')}
          </Typography.Text>
        }
      />
    );
  }

  const statusOptions = Object.fromEntries(
    Object.entries(STATUS_COLORS).map(([key, color]) => [
      key,
      { color, label: t(`schedule.status.${key}`) }
    ])
  );

  const columns = [
    { title: t('schedule.columns.name'), dataIndex: 'name' },
    { title: t('schedule.columns.agent'), dataIndex: 'agent' },
    { title: t('schedule.columns.status'), dataIndex: 'status' },
    { title: t('schedule.columns.nextFireAt'), dataIndex: 'nextFireAt' },
    { title: t('schedule.columns.lastFireAt'), dataIndex: 'lastFireAt' },
    { title: t('schedule.columns.timezone'), dataIndex: 'timezone' }
  ];

  const dataSource = items.map((item: NextScheduleItem) => ({
    key: item.scheduleId,
    name: (
      <Typography.Text
        link
        onClick={() => onOpenSchedule(item.scheduleId)}
        data-testid={`next-schedule-${item.scheduleId}`}
      >
        {item.name}
      </Typography.Text>
    ),
    agent: item.agentName ?? t('common.empty'),
    status: <StatusTag status={item.status} options={statusOptions} />,
    nextFireAt: <DateTimeText value={item.nextFireAt} />,
    lastFireAt: item.lastFireAt ? <DateTimeText value={item.lastFireAt} /> : t('common.empty'),
    timezone: item.timezone
  }));

  return (
    <>
      <Table columns={columns} dataSource={dataSource} pagination={false} rowKey="key" />
      <Typography.Text link onClick={onViewAll} data-testid="next-schedules-view-all">
        {t('overview.viewAll')}
      </Typography.Text>
    </>
  );
}
