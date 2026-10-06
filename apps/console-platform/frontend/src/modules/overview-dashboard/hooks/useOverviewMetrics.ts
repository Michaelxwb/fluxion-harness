/**
 * 指标图数据状态机：与 `useOverview` 同构（hook local `{data, loading, error}`）。
 *
 * 取数只经 service 层；`requestSeq` 丢弃乱序响应，避免刷新与首载竞态。失败只置 `error`
 * 且**不伪造 0**：图表区由页面渲染 ErrorState + 重试，绝不把"没读到"画成全零图。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { getOverviewMetrics } from '../services/overviewService';
import type { OverviewMetrics, OverviewMetricsQuery } from '../types';

export interface OverviewMetricsState {
  data: OverviewMetrics | null;
  loading: boolean;
  error: boolean;
  /** 重新聚合取数：刷新与失败重试共用同一出口。 */
  reload(): void;
}

export function useOverviewMetrics(query: OverviewMetricsQuery = {}): OverviewMetricsState {
  const days = query.days ?? 7;
  const [data, setData] = useState<OverviewMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const result = await getOverviewMetrics(days);
      if (current !== requestSeq.current) {
        return;
      }
      setData(result);
      setError(false);
    } catch {
      // 失败不伪造 0：保留既有数据形态交由页面分流（无数据即图表区 ErrorState）
      if (current === requestSeq.current) {
        setError(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [days]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, loading, error, reload };
}
