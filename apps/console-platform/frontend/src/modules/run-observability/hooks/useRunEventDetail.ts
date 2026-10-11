import { useCallback, useEffect, useRef, useState } from 'react';

import { apiErrorBody } from '../../../api/client';
import { getRunEventDetail, type RunEventDetail } from '../services/runs';

/** Mounted per node/run/refresh generation. Collapse preserves the cached result. */
export function useRunEventDetail(runId: string, seq: number, expanded: boolean) {
  const [detail, setDetail] = useState<RunEventDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const requestSeq = useRef(0);
  const load = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const value = await getRunEventDetail(runId, seq);
      if (current === requestSeq.current) setDetail(value);
    } catch (error) {
      if (current === requestSeq.current) {
        setFailed(true);
        setNotFound(apiErrorBody(error)?.code === 'COMMON_NOT_FOUND');
      }
    } finally {
      if (current === requestSeq.current) setLoading(false);
    }
  }, [runId, seq]);
  useEffect(() => () => { requestSeq.current += 1; }, [runId, seq]);
  useEffect(() => {
    if (expanded && !detail && !loading && !failed) void load();
  }, [expanded, detail, loading, failed, load]);
  return { detail, loading, failed, notFound, reload: () => { void load(); } };
}
