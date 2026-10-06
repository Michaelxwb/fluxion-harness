/**
 * 概览页容器（v2 指标化改造）：纯指标页——KPI 卡 + 两块图表（任务趋势 / 状态分布）。
 *
 * 取数走两个 hook（`useOverview` → KPI、`useOverviewMetrics` → 图表）；指标**只在页面取一次**，
 * 两块卡片经 props 共享同一份状态——图表卡各自调 hook 就会发出两次相同请求（N+1 的变体）。
 * 各自三态分流：loading 用 Skeleton；首载失败渲染 ErrorState + 重试（不伪造 0）。
 * 历史明细不再上概览，排障入口是 KPI 卡的跳转（/tasks、/schedules）与运行审计页。
 */

import { Skeleton } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../../components/common/ConsolePage';
import { ErrorState } from '../../../components/common/ErrorState';
import { KpiCards } from '../components/KpiCards';
import { TaskStatusDonut } from '../components/TaskStatusDonut';
import { TaskTrendChart } from '../components/TaskTrendChart';
import { useOverview } from '../hooks/useOverview';
import { useOverviewMetrics, type OverviewMetricsState } from '../hooks/useOverviewMetrics';
import type { OverviewKpis } from '../types';

const EMPTY_KPIS: OverviewKpis = {
  enabledAgents: 0,
  enabledSkills: 0,
  activeTasks: 0,
  activeSchedules: 0
};

function ChartSkeleton() {
  return (
    <div className="overview-chart-body" data-testid="overview-chart-loading">
      <Skeleton placeholder={<Skeleton.Title style={{ width: 160 }} />} loading />
      <Skeleton placeholder={<Skeleton.Paragraph rows={4} />} loading />
    </div>
  );
}

function TaskTrendCard({ metrics }: { metrics: OverviewMetricsState }) {
  const { t } = useTranslation();
  const firstLoadFailed = metrics.error && metrics.data === null;
  const trend = metrics.data?.taskTrend ?? [];
  const total = trend.reduce((sum, point) => sum + point.total, 0);
  const failed = trend.reduce((sum, point) => sum + point.failed, 0);

  return (
    <PageSection title={t('overview.charts.taskTrend')}>
      {firstLoadFailed ? (
        <ErrorState description={t('overview.charts.loadFailed')} onRetry={metrics.reload} />
      ) : (
        <div className="overview-chart-body">
          <p className="overview-chart-summary" data-testid="overview-trend-summary">
            {t('overview.charts.trendSummary', { days: metrics.data?.days ?? 7, total, failed })}
          </p>
          <div className="overview-chart-canvas">
            {metrics.loading && metrics.data === null ? (
              <ChartSkeleton />
            ) : (
              <TaskTrendChart trend={trend} />
            )}
          </div>
        </div>
      )}
    </PageSection>
  );
}

function TaskStatusCard({ metrics }: { metrics: OverviewMetricsState }) {
  const { t } = useTranslation();
  const firstLoadFailed = metrics.error && metrics.data === null;

  return (
    <PageSection title={t('overview.charts.taskStatus')}>
      {firstLoadFailed ? (
        <ErrorState description={t('overview.charts.loadFailed')} onRetry={metrics.reload} />
      ) : (
        <div className="overview-chart-body">
          {metrics.loading && metrics.data === null ? (
            <ChartSkeleton />
          ) : (
            <TaskStatusDonut status={metrics.data?.taskStatus ?? {}} />
          )}
        </div>
      )}
    </PageSection>
  );
}

export function OverviewPage() {
  const { t } = useTranslation();
  const overview = useOverview();
  const metrics = useOverviewMetrics();
  const firstLoadFailed = overview.error && overview.data === null;
  const showSkeleton = overview.loading && overview.data === null;

  return (
    <>
      <PageHeader title={t('overview.title')} description={t('overview.subtitle')} />
      <PageSection>
        {firstLoadFailed ? (
          <ErrorState description={t('overview.loadFailed')} onRetry={overview.reload} />
        ) : (
          <KpiCards kpis={overview.data?.kpis ?? EMPTY_KPIS} loading={showSkeleton} />
        )}
      </PageSection>
      <div className="overview-charts">
        <TaskTrendCard metrics={metrics} />
        <TaskStatusCard metrics={metrics} />
      </div>
    </>
  );
}
