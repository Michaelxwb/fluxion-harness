import { useTranslation } from 'react-i18next';
import { EmptyState } from '../../../components/common/EmptyState';
import { prefersReducedMotion, useChartTheme } from '../hooks/useChartTheme';
import type { OverviewTokenPoint } from '../types';
import { OverviewChart } from './OverviewChart';
export function TokenTrendChart({ trend }: { trend: OverviewTokenPoint[] }) {
  const { t } = useTranslation();
  const { colors } = useChartTheme();
  if (!trend.some((point) => point.modelRequestCount > 0)) return <EmptyState title={t('overview.tokens.noRequests')} />;
  if (!trend.some((point) => point.modelRequestCount > 0 && (point.inputTokens !== null || point.outputTokens !== null))) {
    return <EmptyState title={t('overview.notRecorded')} description={t('overview.tokens.missing')} />;
  }
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
      { name: t('overview.tokens.input'), type: 'line' as const, connectNulls: false,
        data: trend.map((point) => point.inputTokens), itemStyle: { color: colors.primary }, symbolSize: 5 },
      { name: t('overview.tokens.output'), type: 'line' as const, connectNulls: false,
        data: trend.map((point) => point.outputTokens), itemStyle: { color: colors.success }, symbolSize: 5 }
    ]
  };
  return <OverviewChart option={option} />;
}
