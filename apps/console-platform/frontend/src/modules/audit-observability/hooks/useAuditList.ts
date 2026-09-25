/**
 * 审计列表数据状态机（设计 §3.5「列表数据」：hook local `{items,page,pageSize,total,loading,error}`）。
 *
 * 取数只经 TASK-010 的 service 层（组件不裸用 HTTP 客户端）；`requestSeq` 丢弃乱序响应，
 * 避免逐键筛选/快速翻页时旧响应覆盖新结果。查询失败只置 `failed`（[E-06] 保留筛选条件与已加载
 * 数据，由 `AuditTable` 的 `ErrorState` 就地重试），`reload` 是刷新与重试的唯一出口。
 *
 * 分页状态以服务端响应为准（设计 §3.4）：`page`/`pageSize`/`total` 取自响应，`total` 为服务端
 * 总数，footer 的「x-y / total」与页数都由它驱动，不使用本地数组长度推算。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { listAudits } from '../services/auditService';
import type { AuditListItem, AuditListQuery } from '../types';

export interface AuditListState {
  items: AuditListItem[];
  page: number;
  pageSize: number;
  total: number;
  loading: boolean;
  failed: boolean;
  /** 按当前 `query` 重取：刷新与失败重试共用同一出口。 */
  reload(): void;
}

export function useAuditList(query: AuditListQuery): AuditListState {
  const [items, setItems] = useState<AuditListItem[]>([]);
  const [page, setPage] = useState(query.page);
  const [pageSize, setPageSize] = useState(query.pageSize);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const result = await listAudits(query);
      if (current !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setPage(result.page);
      setPageSize(result.pageSize);
      setTotal(result.total);
      setFailed(false);
    } catch {
      // [E-06] 失败不改筛选：条件与已加载数据保留，由 ErrorState 就地重试。
      if (current === requestSeq.current) {
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [query]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { items, page, pageSize, total, loading, failed, reload };
}
