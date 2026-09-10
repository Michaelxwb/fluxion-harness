import { useCallback, useEffect, useState } from 'react';

import { listAgents, type AgentRow } from '../services/agentService';

export function useAgentList() {
  const [items, setItems] = useState<AgentRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPageState] = useState(1);
  const [pageSize, setPageSizeState] = useState(20);
  const [keyword, setKeywordState] = useState('');
  const [enabledFilter, setEnabledFilterState] = useState<boolean>();
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listAgents({ page, pageSize, keyword, enabled: enabledFilter });
      setItems(result.items);
      setTotal(result.total);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, keyword, enabledFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  return {
    items,
    total,
    page,
    pageSize,
    keyword,
    enabledFilter,
    loading,
    reload: load,
    setPage: setPageState,
    setPageSize: (value: number) => {
      setPageSizeState(value);
      setPageState(1);
    },
    setKeyword: (value: string) => {
      setKeywordState(value);
      setPageState(1);
    },
    setEnabledFilter: (value?: boolean) => {
      setEnabledFilterState(value);
      setPageState(1);
    },
  };
}
