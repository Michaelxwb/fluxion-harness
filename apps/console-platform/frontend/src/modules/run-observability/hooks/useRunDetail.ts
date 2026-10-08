import { useCallback, useEffect, useRef, useState } from 'react';

import { apiErrorBody } from '../../../api/client';
import { getRun, type RunDetail } from '../services/runs';

export interface RunDetailState {
  detail: RunDetail | null;
  loading: boolean;
  failed: boolean;
  /** 404（不存在或跨租户同码）：页面显示「不可用」，不落任何旧对象内容。 */
  notFound: boolean;
  /** 按当前 runId 重取：详情失败重试与页头刷新的唯一出口。 */
  reload(): void;
}

/** 详情与其所属 run 一起存：暴露值只在 run 匹配时可见，结构上杜绝旧对象闪给新对象。 */
interface LoadedDetail {
  runId: string;
  value: RunDetail;
}

/**
 * Run 详情数据状态机（设计 §3.5 FRONT-01）。只经 `services/runs.ts` 取数。
 *
 * - 每条请求发出递增 `requestSeq`，响应/异常/finally 都核验；关闭、切换对象与卸载都递增，
 *   过期响应不得写当前 state（E-22）。
 * - 切换对象立即不可见旧详情（不把 A 的内容闪给 B，E-20）；**同对象刷新**保留旧内容直到新值到达。
 * - 404 与普通失败分开（apiErrorBody 分类）：前者是「不可用」，后者是可重试错误态。
 */
export function useRunDetail(runId: string | null): RunDetailState {
  const [loaded, setLoaded] = useState<LoadedDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    if (runId === null) {
      return;
    }
    const current = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    setNotFound(false);
    try {
      const value = await getRun(runId);
      if (current === requestSeq.current) {
        setLoaded({ runId, value });
      }
    } catch (error) {
      if (current === requestSeq.current) {
        setLoaded(null);
        setFailed(true);
        setNotFound(apiErrorBody(error)?.code === 'COMMON_NOT_FOUND');
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [runId]);

  const reload = useCallback(() => {
    void load();
  }, [load]);

  useEffect(() => {
    // 切换对象/关闭：先让在途响应失效，再清空旧对象（不闪旧内容）。
    requestSeq.current += 1;
    setLoaded(null);
    setFailed(false);
    setNotFound(false);
    setLoading(false);
    if (runId !== null) {
      void load();
    }
    return () => {
      // 卸载：同样让在途响应失效，不得在已卸载的详情上写 state。
      requestSeq.current += 1;
    };
  }, [runId, load]);

  const detail = loaded !== null && loaded.runId === runId ? loaded.value : null;
  return { detail, loading, failed, notFound, reload };
}
