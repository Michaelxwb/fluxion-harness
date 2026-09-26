/**
 * 概览数据状态机（设计 §3.5「概览数据：hook local `{data, loading, error}`」）。
 *
 * 取数只经 service 层（组件不裸用 HTTP 客户端）；`requestSeq` 丢弃乱序响应，避免手动刷新与首载
 * 竞态覆盖。失败只置 `error` 且**不伪造 0**（[E-03] 由页面渲染整页 `ErrorState` + 重试），
 * `reload` 是刷新与重试的唯一出口。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { getOverview } from '../services/overviewService';
import type { OverviewData } from '../types';

export interface OverviewState {
  data: OverviewData | null;
  loading: boolean;
  error: boolean;
  /** 重新聚合取数：刷新与失败重试共用同一出口。 */
  reload(): void;
}

export function useOverview(): OverviewState {
  const [data, setData] = useState<OverviewData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const result = await getOverview();
      if (current !== requestSeq.current) {
        return;
      }
      setData(result);
      setError(false);
    } catch {
      // [E-03] 失败不伪造 0：保留既有数据形态交由页面分流（无数据即整页 ErrorState）
      if (current === requestSeq.current) {
        setError(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, loading, error, reload };
}
