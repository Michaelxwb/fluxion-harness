/**
 * 近 7 天任务趋势柱状图（设计对齐结论：最小两块之一）。
 *
 * 双系列：任务量（primary）+ 失败（danger）。颜色经 `useChartTheme` 从 CSS 变量解析
 * （canvas 吃不到 var()）；数据由后端 `/overview/metrics` 按平台默认时区分桶并补零，
 * 组件不负责任何聚合（RISK-FE-01 同口径：前端不做第二套统计）。
 */

import ReactECharts from 'echarts-for-react';
import { useTranslation } from 'react-i18next';

import { prefersReducedMotion, useChartTheme } from '../hooks/useChartTheme';
import type { TaskTrendPoint } from '../types';

export interface TaskTrendChartProps {
  trend: TaskTrendPoint[];
}

export function TaskTrendChart({ trend }: TaskTrendChartProps) {
  const { t } = useTranslation();
  const { colors } = useChartTheme();

  const option = {
    animation: !prefersReducedMotion(),
    tooltip: { trigger: 'axis' as const },
    legend: { top: 0, left: 0, textStyle: { color: colors.text2 } },
    grid: { left: 8, right: 8, top: 36, bottom: 0, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: trend.map((point) => point.date),
      axisLabel: { color: colors.text2 },
      axisLine: { lineStyle: { color: colors.border } },
      axisTick: { show: false }
    },
    yAxis: {
      type: 'value' as const,
      minInterval: 1,
      axisLabel: { color: colors.text2 },
      splitLine: { lineStyle: { color: colors.fill } }
    },
    series: [
      {
        name: t('overview.charts.seriesTotal'),
        type: 'bar' as const,
        data: trend.map((point) => point.total),
        itemStyle: { color: colors.primary, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 26
      },
      {
        name: t('overview.charts.seriesFailed'),
        type: 'bar' as const,
        data: trend.map((point) => point.failed),
        itemStyle: { color: colors.danger, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 26
      }
    ]
  };

  return <ReactECharts option={option} notMerge style={{ height: '100%', width: '100%' }} />;
}
