import { useCallback, useEffect, useRef, useState } from 'react';

import type { Page } from '../../user-identity/services/users';
import {
  listRunOperations,
  OPERATIONS_PAGE_SIZE_DEFAULT,
  OPERATIONS_PAGE_SIZE_MAX,
  type RunOperationOutline
} from '../services/runs';

export interface RunOperationsState {
  page: Page<RunOperationOutline> | null;
  pageIndex: number;
  pageSize: number;
  loading: boolean;
  failed: boolean;
  /** 曾经发起过取数（首次激活页签）：刷新只对已加载的数据重取，不凭空多发请求。 */
  loaded: boolean;
  reload(): void;
  changePage(page: number): void;
  changePageSize(pageSize: number): void;
}

/** 一页与其所属 run 一起存：暴露值只在 run 匹配时可见，不把另一个 Run 的清单拼到当前详情。 */
interface LoadedPage {
  runId: string;
  value: Page<RunOperationOutline>;
}

/**
 * 关联操作分页状态机（设计 §3.5 FRONT-01、§3.6）。
 *
 * - **首次激活页签才取数**（`active` 首次为真）：基本页签不发分页请求；
 * - 对象切换清空并使过期响应失效（`requestSeq`）；
 * - 空页回退：状态变化（删除/取消）后当前页可能超界，自动回落到最后一个合法页再取；
 * - 分页参数在服务层夹紧（默认 15、上限 100）。
 */
export function useRunOperations(runId: string | null, active: boolean): RunOperationsState {
  const [loadedPage, setLoadedPage] = useState<LoadedPage | null>(null);
  const [pageIndex, setPageIndex] = useState(1);
  const [pageSize, setPageSize] = useState(OPERATIONS_PAGE_SIZE_DEFAULT);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const requestSeq = useRef(0);
  const runIdRef = useRef(runId);
  runIdRef.current = runId;

  const fetchPage = useCallback(async (targetPage: number, targetSize: number) => {
    const id = runIdRef.current;
    if (id === null) {
      return;
    }
    const current = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      let value = await listRunOperations(id, { page: targetPage, page_size: targetSize });
      if (current !== requestSeq.current) {
        return;
      }
      const lastPage = Math.max(1, Math.ceil(value.total / targetSize));
      if (targetPage > lastPage) {
        // 状态变化后当前页超界：回落到最后一个合法页重取（不展示空页，也不越界请求）。
        value = await listRunOperations(id, { page: lastPage, page_size: targetSize });
        if (current !== requestSeq.current) {
          return;
        }
        setPageIndex(lastPage);
      }
      setLoadedPage({ runId: id, value });
    } catch {
      if (current === requestSeq.current) {
        setLoadedPage(null);
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, []);

  // 对象切换/关闭：清空并使在途响应失效。
  useEffect(() => {
    requestSeq.current += 1;
    setLoadedPage(null);
    setFailed(false);
    setLoading(false);
    setLoaded(false);
    setPageIndex(1);
    return () => {
      requestSeq.current += 1;
    };
  }, [runId]);

  // 首次激活页签时才取数；已加载后切换页签不重复取数。
  useEffect(() => {
    if (active && runId !== null && !loaded) {
      setLoaded(true);
      void fetchPage(pageIndex, pageSize);
    }
  }, [active, runId, loaded, pageIndex, pageSize, fetchPage]);

  const reload = useCallback(() => {
    if (runIdRef.current !== null && loaded) {
      void fetchPage(pageIndex, pageSize);
    }
  }, [loaded, pageIndex, pageSize, fetchPage]);

  const changePage = useCallback(
    (next: number) => {
      const target = Math.max(1, Math.floor(next));
      setPageIndex(target);
      if (loaded) {
        void fetchPage(target, pageSize);
      }
    },
    [loaded, pageSize, fetchPage]
  );

  const changePageSize = useCallback(
    (next: number) => {
      const size = Math.min(Math.max(1, Math.floor(next)), OPERATIONS_PAGE_SIZE_MAX);
      setPageSize(size);
      setPageIndex(1);
      if (loaded) {
        void fetchPage(1, size);
      }
    },
    [loaded, fetchPage]
  );

  const page = loadedPage !== null && loadedPage.runId === runId ? loadedPage.value : null;
  return { page, pageIndex, pageSize, loading, failed, loaded, reload, changePage, changePageSize };
}
