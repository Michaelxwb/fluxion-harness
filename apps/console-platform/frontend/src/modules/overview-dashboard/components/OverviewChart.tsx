import ReactECharts, { type EChartsReactProps } from 'echarts-for-react';
import { useEffect, useRef } from 'react';

/**
 * ECharts 容器：监听父容器尺寸变化并主动 `resize()`。
 *
 * `echarts-for-react` 默认只监听 window resize；概览页的图表高度由 flex/grid 分配，
 * 首屏挂载时容器可能还没拿到最终尺寸（状态分布图曾因此只渲染出 1px 宽），
 * 侧栏收起也只改变内容宽度、不触发 window resize。ResizeObserver 同时覆盖这两种情况。
 */
export function OverviewChart({ option }: { option: EChartsReactProps['option'] }) {
  const chartRef = useRef<ReactECharts>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const resize = () => {
      const instance = chartRef.current?.getEchartsInstance();
      instance?.resize();
    };
    const observer = new ResizeObserver(() => requestAnimationFrame(resize));
    observer.observe(host);
    const initial = requestAnimationFrame(resize);
    return () => {
      cancelAnimationFrame(initial);
      observer.disconnect();
    };
  }, []);
  return (
    <div ref={hostRef} className="overview-chart-host">
      <ReactECharts ref={chartRef} option={option} notMerge style={{ height: '100%', width: '100%' }} />
    </div>
  );
}
