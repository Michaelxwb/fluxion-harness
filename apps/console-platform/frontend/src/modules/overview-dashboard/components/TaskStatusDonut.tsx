/**
 * 任务状态分布环形图（设计对齐结论：最小两块之一）。
 *
 * 已知状态用固定色环（色值经 useChartTheme 从 CSS 变量解析，随亮暗主题重渲染），未知新增
 * 状态并入「其他」。图例用 **DOM 渲染**（带计数与 testid）：canvas 不可被 e2e/辅助技术读取，
 * 图例即数据出口。全量为 0 时渲染空态，不画空环。
 */

import ReactECharts from 'echarts-for-react';
import { useTranslation } from 'react-i18next';

import { EmptyState } from '../../../components/common/EmptyState';
import { prefersReducedMotion, useChartTheme, type ChartColors } from '../hooks/useChartTheme';

export interface TaskStatusDonutProps {
  status: Record<string, number>;
}

/** 已知状态 → 主题色键（i18n 契约按此表的键序枚举 task.status.* 键族）。 */
const STATUS_COLOR_KEY: Record<string, keyof ChartColors> = {
  SUCCEEDED: 'success',
  RUNNING: 'primary',
  QUEUED: 'warning',
  FAILED: 'danger',
  CANCELLED: 'muted'
};

const STATUS_ORDER = Object.keys(STATUS_COLOR_KEY);

interface StatusSlice {
  key: string;
  label: string;
  count: number;
  color: string;
}

export function TaskStatusDonut({ status }: TaskStatusDonutProps) {
  const { t } = useTranslation();
  const { colors } = useChartTheme();

  const known: StatusSlice[] = STATUS_ORDER.map((key) => ({
    key,
    label: t(`task.status.${key}`),
    count: status[key] ?? 0,
    color: colors[STATUS_COLOR_KEY[key]]
  }));
  const otherCount = Object.entries(status)
    .filter(([key]) => !(key in STATUS_COLOR_KEY))
    .reduce((sum, [, count]) => sum + count, 0);
  const entries: StatusSlice[] = [
    ...known,
    ...(otherCount > 0
      ? [{ key: 'OTHER', label: t('overview.charts.other'), count: otherCount, color: colors.muted }]
      : [])
  ];
  const total = entries.reduce((sum, entry) => sum + entry.count, 0);

  if (total === 0) {
    return <EmptyState title={t('overview.charts.statusEmpty')} />;
  }

  const option = {
    animation: !prefersReducedMotion(),
    tooltip: { trigger: 'item' as const },
    series: [
      {
        type: 'pie' as const,
        radius: ['58%', '82%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        label: { show: false },
        data: entries.map((entry) => ({
          name: entry.label,
          value: entry.count,
          itemStyle: { color: entry.color }
        }))
      }
    ]
  };

  return (
    <div className="overview-donut">
      <div className="overview-donut-canvas">
        <ReactECharts option={option} notMerge style={{ height: '100%', width: '100%' }} />
      </div>
      <ul className="overview-donut-legend" data-testid="overview-status-legend">
        {entries.map((entry) => (
          <li key={entry.key} data-testid={`overview-status-${entry.key.toLowerCase()}`}>
            <span className="overview-donut-dot" style={{ background: entry.color }} />
            <span className="overview-donut-label">{entry.label}</span>
            <span className="overview-donut-count">{entry.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
