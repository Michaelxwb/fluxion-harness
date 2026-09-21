import { Table, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { listMcpAgents, type McpAgentItem } from './services/mcpServers';

export interface McpAgentTableProps {
  serverId: string;
}

const PAGE_SIZE = 10;

export function McpAgentTable(props: McpAgentTableProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<McpAgentItem[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listMcpAgents(props.serverId, { page, page_size: PAGE_SIZE });
      setItems(result.items);
      setTotal(result.total);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [page, props.serverId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (failed) {
    return <ErrorState onRetry={() => void reload()} />;
  }

  return (
    <div data-testid="mcp-agents">
      <Table<McpAgentItem>
        rowKey="agent_id"
        loading={loading}
        pagination={false}
        dataSource={items}
        empty={<EmptyState title={t('mcp.agents.empty')} />}
        columns={[
          { title: t('mcp.agents.agentName'), dataIndex: 'name' },
          { title: t('mcp.agents.agentKey'), dataIndex: 'key' },
          {
            title: t('mcp.columns.enabled'),
            dataIndex: 'enabled',
            render: (value: boolean) => (
              <Tag color={value ? 'green' : 'grey'}>
                {t(value ? 'common.status.enabled' : 'common.status.disabled')}
              </Tag>
            )
          },
          {
            title: t('mcp.agents.boundAt'),
            dataIndex: 'create_time',
            render: (value: string) => <DateTimeText value={value} />
          }
        ]}
      />
      <PaginationFooter page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
    </div>
  );
}
