import { Button, Modal } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { RemoteTable } from '../../components/common/RemoteTable';
import {
  getTool,
  listTools,
  type McpToolDetail,
  type McpToolEntry
} from './services/mcpServers';

export interface McpToolTableProps {
  serverId: string;
  /** 变化时重新拉取快照（discover 成功后由父级递增） */
  reloadKey?: number;
}

const PAGE_SIZE = 10;

export function McpToolTable(props: McpToolTableProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<McpToolEntry[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detail, setDetail] = useState<McpToolDetail | null>(null);
  const [detailFailed, setDetailFailed] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const result = await listTools(props.serverId, { page, page_size: PAGE_SIZE });
      if (current !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setTotal(result.total);
      setFailed(false);
    } catch {
      if (current === requestSeq.current) {
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [page, props.serverId]);

  useEffect(() => {
    setPage(1);
  }, [props.reloadKey]);

  useEffect(() => {
    void reload();
  }, [reload, props.reloadKey]);

  const openDetail = async (tool: McpToolEntry): Promise<void> => {
    setDetailFailed(false);
    try {
      setDetail(await getTool(props.serverId, tool.name));
    } catch {
      setDetail(null);
      setDetailFailed(true);
    }
  };

  if (failed) {
    return <ErrorState onRetry={() => void reload()} />;
  }

  return (
    <>
      <RemoteTable<McpToolEntry>
        rowKey="name"
        loading={loading}
        dataSource={items}
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        onPageChange={setPage}
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
        visible={detail !== null || detailFailed}
        title={t('mcp.tools.detailTitle', { name: detail?.name ?? '' })}
        footer={null}
        width={560}
        onCancel={() => {
          setDetail(null);
          setDetailFailed(false);
        }}
      >
        {detailFailed ? (
          <ErrorState />
        ) : detail === null ? null : (
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
