import { Button, Input, Select, Tag, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { McpDetailSideSheet } from './McpDetailSideSheet';
import { McpFormModal } from './McpFormModal';
import {
  deleteMcpServer,
  discoverTools,
  listMcpServers,
  type McpServerDetail,
  type McpServerListItem
} from './services/mcpServers';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', user_scope: '', connection_status: '' };

const CONNECTION_STATUSES = ['UNKNOWN', 'AVAILABLE', 'UNAVAILABLE', 'DISCOVERY_FAILED'] as const;

export function McpPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [keywordInput, setKeywordInput] = useState('');
  const [items, setItems] = useState<McpServerListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detail, setDetail] = useState<McpServerListItem | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [formServer, setFormServer] = useState<McpServerDetail | null>(null);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setParams((prev) =>
        prev.keyword === keywordInput ? prev : { ...prev, keyword: keywordInput, page: 1 }
      );
    }, 300);
    return () => clearTimeout(timer);
  }, [keywordInput]);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listMcpServers({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        user_scope: params.user_scope === '' ? undefined : (params.user_scope as 'ALL' | 'SELECTED'),
        connection_status: params.connection_status || undefined
      });
      if (current !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
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
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const remove = async (server: McpServerListItem): Promise<void> => {
    try {
      await deleteMcpServer(server.mcp_id);
    } catch {
      return;
    }
    setDetail(null);
    if (items.length === 1 && params.page > 1) {
      setParams((prev) => ({ ...prev, page: prev.page - 1 }));
    } else {
      await reload();
    }
  };

  const refreshTools = async (server: McpServerListItem): Promise<void> => {
    setRefreshingId(server.mcp_id);
    try {
      await discoverTools(server.mcp_id);
      Toast.success(t('mcp.actions.discoverSuccess'));
      await reload();
    } catch {
      // 失败 Toast 由 ApiClient 展示；保留上一成功 Catalog
    } finally {
      setRefreshingId(null);
    }
  };

  return (
    <>
      <PageHeader title={t('mcp.title')} description={t('mcp.subtitle')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button
              theme="solid"
              data-testid="create-mcp"
              onClick={() => {
                setFormServer(null);
                setFormVisible(true);
              }}
            >
              {t('mcp.actions.create')}
            </Button>
          }
          search={
            <>
              <Input
                value={keywordInput}
                placeholder={t('mcp.searchPlaceholder')}
                style={{ width: 200 }}
                onChange={setKeywordInput}
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
              <Select
                value={params.connection_status || undefined}
                style={{ width: 170 }}
                showClear
                placeholder={t('mcp.columns.connectionStatus')}
                optionList={CONNECTION_STATUSES.map((status) => ({
                  value: status,
                  label: t(`mcp.connection.${status}`)
                }))}
                onChange={(value) =>
                  setParams((prev) => ({
                    ...prev,
                    connection_status: value ? String(value) : '',
                    page: 1
                  }))
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
                <Button
                  theme="borderless"
                  data-testid={`mcp-link-${record.key}`}
                  onClick={() => setDetail(record)}
                >
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
                <Tag color={value === 'ALL' ? 'green' : 'blue'}>
                  {t(`mcp.scope.${value.toLowerCase()}`)}
                </Tag>
              )
            },
            { title: t('mcp.columns.selectedUserCount'), dataIndex: 'selected_user_count' },
            { title: t('mcp.columns.toolCount'), dataIndex: 'tool_count' },
            { title: t('mcp.columns.usingAgentCount'), dataIndex: 'using_agent_count' },
            {
              title: t('mcp.columns.enabled'),
              dataIndex: 'enabled',
              render: (value: boolean) => (
                <Tag color={value ? 'light-blue' : 'grey'}>
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
                  loading={refreshingId === record.mcp_id}
                  onClick={() => void refreshTools(record)}
                >
                  {t('mcp.actions.discover')}
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
          empty={
            failed ? (
              <ErrorState onRetry={() => void reload()} />
            ) : (
              <EmptyState title={t('common.empty')} description={t('common.emptyHint')} />
            )
          }
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
          setFormServer(server);
          setFormVisible(true);
        }}
        onDelete={(server) => void remove(server)}
        onMutated={() => void reload()}
      />
    </>
  );
}
