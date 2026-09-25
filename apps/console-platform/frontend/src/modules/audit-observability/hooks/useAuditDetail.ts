/**
 * 审计详情数据状态机（设计 §3.5「详情选择」：hook local `{detail,loading,failed}`）。
 *
 * 取数只经 TASK-010 的 service 层（组件不裸用 HTTP 客户端）；`auditId` 可空（关闭即清空并让在途
 * 响应失效），`requestSeq` 丢弃乱序响应，查询失败只置 `failed`——[E-07] 侧栏就地重试，不回退、
 * 不伪造详情数据；`reload` 是重试的唯一出口。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { getAudit } from '../services/auditService';
import type { AuditDetail, AuditListItem } from '../types';

export interface AuditDetailState {
  detail: AuditDetail | null;
  loading: boolean;
  failed: boolean;
  /** 按当前 `(auditType, auditId)` 重取：详情失败重试的唯一出口。 */
  reload(): void;
}

export function useAuditDetail(
  auditType: AuditListItem['auditType'],
  auditId: string | null
): AuditDetailState {
  const [detail, setDetail] = useState<AuditDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    if (auditId === null) {
      return;
    }
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const value = await getAudit(auditType, auditId);
      if (current !== requestSeq.current) {
        return;
      }
      setDetail(value);
      setFailed(false);
    } catch {
      // [E-07] 失败只置 failed：详情保持为空由 ErrorState 就地重试，不编造兜底数据。
      if (current === requestSeq.current) {
        setDetail(null);
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [auditType, auditId]);

  useEffect(() => {
    // 选择变更/关闭即让在途响应失效：过期响应不得回写到已切换的详情。
    requestSeq.current += 1;
    if (auditId === null) {
      setDetail(null);
      setFailed(false);
      setLoading(false);
      return;
    }
    void reload();
  }, [auditId, reload]);

  return { detail, loading, failed, reload };
}
