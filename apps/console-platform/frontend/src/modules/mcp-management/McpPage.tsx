import { Button, Input, Select, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { McpDetailSideSheet } from './McpDetailSideSheet';
import { McpFormModal } from './McpFormModal';
import {
  deleteMcpServer,
  listMcpServers,
  type McpServerDetail,
  type McpServerListItem
} from './services/mcpServers';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', user_scope: '' };

export function McpPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [items, setItems] = useState<McpServerListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<McpServerListItem | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [formServer, setFormServer] = useState<McpServerDetail | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listMcpServers({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        user_scope: params.user_scope === '' ? undefined : (params.user_scope as 'ALL' | 'SELECTED')
      });
      setItems(page.items);
      setTotal(page.total);
    } finally {
      setLoading(false);
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const remove = async (server: McpServerListItem): Promise<void> => {
    await deleteMcpServer(server.mcp_id);
    setDetail(null);
    await reload();
  };

  return (
    <>
      <PageHeader title={t('mcp.title')} description={t('mcp.subtitle')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button theme="solid" data-testid="create-mcp" onClick={() => {
              setFormServer(null);
              setFormVisible(true);
            }}>
              {t('mcp.actions.create')}
            </Button>
          }
          search={
            <>
              <Input
                value={params.keyword}
                placeholder={t('mcp.searchPlaceholder')}
                style={{ width: 200 }}
                onChange={(value) => setParams((prev) => ({ ...prev, keyword: value, page: 1 }))}
              />
              <Select
                value={params.user_scope || undefined}
                style={{ width: 150 }}
                showClear
                placeholder={t('mcp.columns.userScope')}
                optionList={[
                  { value: 'SELECTED', label: t('mcp.scope.selected') },
                  { value: 'ALL', label: t('mcp.scope.all') }
                ]}
                onChange={(value) =>
                  setParams((prev) => ({ ...prev, user_scope: value ? String(value) : '', page: 1 }))
                }
              />
              <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
            </>
          }
        />
        <RemoteTable<McpServerListItem>
          rowKey="mcp_id"
          loading={loading}
          columns={[
            {
              title: t('mcp.form.name'),
              dataIndex: 'name',
              render: (value: string, record: McpServerListItem) => (
                <Button theme="borderless" data-testid={`mcp-link-${record.key}`} onClick={() => setDetail(record)}>
                  {value}
                </Button>
              )
            },
            { title: t('mcp.form.key'), dataIndex: 'key' },
            { title: t('mcp.form.endpoint'), dataIndex: 'endpoint', width: 220 },
            {
              title: t('mcp.columns.userScope'),
              dataIndex: 'user_scope',
              render: (value: McpServerListItem['user_scope']) => (
                <Tag color={value === 'ALL' ? 'green' : 'blue'}>{t(`mcp.scope.${value.toLowerCase()}`)}</Tag>
              )
            },
            { title: t('mcp.columns.selectedUserCount'), dataIndex: 'selected_user_count' },
            { title: t('mcp.columns.toolCount'), dataIndex: 'tool_count' },
            { title: t('mcp.columns.usingAgentCount'), dataIndex: 'using_agent_count' },
            {
              title: t('mcp.columns.enabled'),
              dataIndex: 'enabled',
              render: (value: boolean) => (
                <Tag color={value ? 'green' : 'grey'}>
                  {t(`common.status.${value ? 'enabled' : 'disabled'}`)}
                </Tag>
              )
            },
            {
              title: t('mcp.columns.connectionStatus'),
              dataIndex: 'connection_status',
              render: (value: string) => (
                <Tag color={value === 'AVAILABLE' ? 'green' : value === 'UNKNOWN' ? 'grey' : 'red'}>
                  {t(`mcp.connection.${value}`)}
                </Tag>
              )
            },
            {
              title: t('mcp.columns.lastDiscoveredAt'),
              dataIndex: 'last_discovered_at',
              render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
            },
            {
              title: t('mcp.columns.actions'),
              render: (_: unknown, record: McpServerListItem) => (
                <Button
                  theme="borderless"
                  data-testid={`refresh-mcp-${record.key}`}
                  onClick={() => setDetail(record)}
                >
                  {t('mcp.actions.manage')}
                </Button>
              )
            }
          ]}
          dataSource={items}
          page={params.page}
          pageSize={params.page_size}
          total={total}
          onPageChange={(page) => setParams((prev) => ({ ...prev, page }))}
          onPageSizeChange={(page_size) => setParams((prev) => ({ ...prev, page: 1, page_size }))}
          empty={<EmptyState title={t('common.empty')} description={t('common.emptyHint')} />}
        />
      </PageSection>
      <McpFormModal
        visible={formVisible}
        server={formServer}
        onCancel={() => {
          setFormVisible(false);
          setFormServer(null);
        }}
        onSaved={() => {
          setFormVisible(false);
          setFormServer(null);
          setDetail(null);
          void reload();
        }}
      />
      <McpDetailSideSheet
        server={detail}
        onCancel={() => setDetail(null)}
        onEdit={(server) => {
          setFormServer(server as McpServerDetail);
          setFormVisible(true);
        }}
        onDelete={(server) => void remove(server)}
        onMutated={() => void reload()}
      />
    </>
  );
}
