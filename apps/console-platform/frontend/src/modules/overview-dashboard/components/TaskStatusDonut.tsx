import ReactECharts from 'echarts-for-react';
import { useTranslation } from 'react-i18next';
import { EmptyState } from '../../../components/common/EmptyState';
import { prefersReducedMotion, useChartTheme, type ChartColors } from '../hooks/useChartTheme';
import type { OverviewScope } from '../types';
const STATUS_COLOR_KEY: Record<string, keyof ChartColors> = {
  CREATED: 'muted', QUEUED: 'muted', RUNNING: 'primary', WAITING_INPUT: 'warning',
  WAITING_TOOL: 'warning', WAITING: 'warning', COMPLETED: 'success', FAILED: 'danger', CANCELLED: 'muted'
};
export const RUN_STATUSES = ['CREATED', 'RUNNING', 'WAITING_TOOL', 'WAITING_INPUT', 'COMPLETED', 'FAILED', 'CANCELLED'];
export const TASK_STATUSES = ['QUEUED', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED', 'CANCELLED'];
export function TaskStatusDonut({ status, scope }: { status: Record<string, number>; scope: OverviewScope }) {
  const { t } = useTranslation();
  const { colors } = useChartTheme();
  const domain = scope === 'runs' ? RUN_STATUSES : TASK_STATUSES;
  const entries = Object.entries(status).filter(([, count]) => count > 0).map(([key, count]) => ({
    key, count, label: domain.includes(key) ? t((scope === 'runs' ? 'run.status.' : 'task.status.') + key) : t('overview.charts.other'),
    color: colors[STATUS_COLOR_KEY[key] ?? 'muted']
  }));
  if (entries.length === 0) return <EmptyState title={t('overview.charts.statusEmpty')} />;
  const option = { animation: !prefersReducedMotion(), tooltip: { trigger: 'item' as const },
    series: [{ type: 'pie' as const, radius: ['60%', '82%'], label: { show: false },
      data: entries.map((entry) => ({ name: entry.label, value: entry.count, itemStyle: { color: entry.color } })) }] };
  return <div className="overview-donut">
    <div className="overview-donut-canvas"><ReactECharts option={option} notMerge style={{ height: '100%', width: '100%' }} /></div>
    <ul className="overview-donut-legend" data-testid="overview-status-legend">
      {entries.map((entry) => <li key={entry.key} data-testid={`overview-status-${entry.key.toLowerCase()}`}>
        <span className="overview-donut-dot" style={{ background: entry.color }} />
        <span className="overview-donut-label">{entry.label}</span><strong className="overview-donut-count">{entry.count}</strong>
      </li>)}
    </ul>
  </div>;
}
