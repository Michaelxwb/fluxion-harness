import { Button, Modal, Table } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { EmptyState } from '../../components/common/EmptyState';
import {
  getTool,
  listTools,
  type McpToolDetail,
  type McpToolEntry
} from './services/mcpServers';

export interface McpToolTableProps {
  serverId: string;
  userScope: 'ALL' | 'SELECTED';
  /** 变化时重新拉取快照（discover 成功后由父级递增） */
  reloadKey?: number;
}

export function McpToolTable(props: McpToolTableProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<McpToolEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<McpToolDetail | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listTools(props.serverId, { page: 1, page_size: 100 });
      setItems(page.items);
    } finally {
      setLoading(false);
    }
  }, [props.serverId]);

  useEffect(() => {
    void reload();
  }, [reload, props.reloadKey]);

  const openDetail = async (tool: McpToolEntry): Promise<void> => {
    const loaded = await getTool(props.serverId, tool.name).catch(() => null);
    if (loaded) {
      setDetail(loaded);
    }
  };

  return (
    <>
      <Table<McpToolEntry>
        rowKey="name"
        loading={loading}
        pagination={false}
        dataSource={items}
        empty={<EmptyState title={t('common.empty')} description={t('mcp.tools.emptyHint')} />}
        columns={[
          {
            title: t('mcp.tools.name'),
            dataIndex: 'name',
            render: (value: string, record: McpToolEntry) => (
              <Button
                theme="borderless"
                data-testid={`mcp-tool-${value}`}
                onClick={() => void openDetail(record)}
              >
                {value}
              </Button>
            )
          },
          { title: t('mcp.tools.description'), dataIndex: 'description' },
          {
            title: t('mcp.tools.effect'),
            dataIndex: 'effect',
            render: (value: string) => t(`mcp.tools.effect_${value}`)
          }
        ]}
      />
      <Modal
        visible={detail !== null}
        title={t('mcp.tools.detailTitle', { name: detail?.name ?? '' })}
        footer={null}
        width={560}
        onCancel={() => setDetail(null)}
      >
        {detail === null ? null : (
          <>
            <p>{detail.description}</p>
            <h5>{t('mcp.tools.inputSchema')}</h5>
            <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 280, overflow: 'auto' }}>
              {JSON.stringify(detail.input_schema, null, 2)}
            </pre>
            <p>
              {t('mcp.tools.effect')}: {t(`mcp.tools.effect_${detail.effect}`)} ·{' '}
              {t('mcp.detail.catalogRevision')}: {detail.catalog_revision}
            </p>
          </>
        )}
      </Modal>
    </>
  );
}
