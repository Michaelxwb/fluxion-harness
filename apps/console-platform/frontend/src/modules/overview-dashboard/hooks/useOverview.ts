import { useCallback, useEffect, useRef, useState } from 'react';
import { getOverview } from '../services/overviewService';
import type { OverviewData } from '../types';
export interface OverviewState {
  data: OverviewData | null; loading: boolean; error: boolean; reload(): void;
}
export function useOverview(days = 7): OverviewState {
  const [data, setData] = useState<OverviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState({ days, failed: false });
  const requestSeq = useRef(0);
  const selectedDays = useRef(days);
  selectedDays.current = days;
  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    setFailure({ days, failed: false });
    setData((previous) => previous?.days === days ? previous : null);
    try {
      const result = await getOverview(days);
      if (current !== requestSeq.current || selectedDays.current !== days) return;
      setData(result);
    } catch {
      if (current === requestSeq.current && selectedDays.current === days) {
        setFailure({ days, failed: true });
      }
    } finally {
      if (current === requestSeq.current && selectedDays.current === days) setLoading(false);
    }
  }, [days]);
  useEffect(() => {
    void reload();
    return () => { requestSeq.current += 1; };
  }, [reload]);
  return { data: data?.days === days ? data : null,
    loading, error: failure.days === days && failure.failed, reload };
}
