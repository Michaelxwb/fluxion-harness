import { useTranslation } from 'react-i18next';
import { prefersReducedMotion, useChartTheme } from '../hooks/useChartTheme';
import type { OverviewTrendPoint } from '../types';
import { OverviewChart } from './OverviewChart';
export function TaskTrendChart({ trend }: { trend: OverviewTrendPoint[] }) {
  const { t } = useTranslation();
  const { colors } = useChartTheme();
  const option = {
    animation: !prefersReducedMotion(), tooltip: { trigger: 'axis' as const },
    legend: { top: 0, left: 0, textStyle: { color: colors.text2, fontSize: 12 } },
    grid: { left: 8, right: 8, top: 36, bottom: 18, containLabel: true },
    xAxis: { type: 'category' as const, data: trend.map((point) => point.date),
      axisLabel: { color: colors.text2, formatter: (date: string) => date.slice(5) },
      axisLine: { lineStyle: { color: colors.border } }, axisTick: { show: false } },
    yAxis: { type: 'value' as const, minInterval: 1, axisLabel: { color: colors.text2 },
      splitLine: { lineStyle: { color: colors.fill } } },
    series: [
      { name: t('overview.charts.seriesTotal'), type: 'bar' as const,
        data: trend.map((point) => point.total), itemStyle: { color: colors.primary, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 24 },
      { name: t('overview.charts.seriesFailed'), type: 'line' as const,
        data: trend.map((point) => point.failed), itemStyle: { color: colors.danger }, symbolSize: 5 }
    ]
  };
  return <OverviewChart option={option} />;
}
