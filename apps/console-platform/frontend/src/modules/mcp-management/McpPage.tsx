import { actionColumn } from '../../components/common/tableLayout';
import { Button, Input, Select, Tag, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../auth/AuthContext';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { McpScopeModal } from './McpScopeModal';
import { McpTestModal } from './McpTestModal';
import { ListActionButton } from '../../components/common/ListActionButton';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { EntityLink } from '../../components/common/EntityLink';
import { TableText } from '../../components/common/TableText';
import { McpDetailSideSheet } from './McpDetailSideSheet';
import { McpFormModal } from './McpFormModal';
import {
  deleteMcpServer,
  discoverTools,
  listMcpServers,
  getMcpServer,
  type McpServerDetail,
  type McpServerListItem
} from './services/mcpServers';

const DEFAULT_PARAMS = { page: 1, page_size: 15, keyword: '', user_scope: '', connection_status: '' };

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
  const { account } = useAuth();
  const [testTarget, setTestTarget] = useState<string | null>(null);
  const [scopeTarget, setScopeTarget] = useState<McpServerDetail | null>(null);
  const [detailReloadKey, setDetailReloadKey] = useState(0);
  const requestSeq = useRef(0);

  /** 关键字：回车才写入查询参数并复位第 1 页（值未变则不产生新 params，避免无谓重查）。 */
  const commitKeyword = (): void => {
    setParams((prev) =>
      prev.keyword === keywordInput ? prev : { ...prev, keyword: keywordInput, page: 1 }
    );
  };

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

  const openEdit = async (server: McpServerListItem): Promise<void> => {
    try {
      const latest = await getMcpServer(server.mcp_id);
      setFormServer(latest);
      setFormVisible(true);
    } catch {
      // ApiClient displays the request failure; keep the modal closed.
    }
  };

  const openScope = async (server: McpServerListItem): Promise<void> => {
    try {
      const latest = await getMcpServer(server.mcp_id);
      setScopeTarget(latest);
    } catch {
      // ApiClient displays the request failure; keep the modal closed.
    }
  };

  const refreshTools = async (server: McpServerListItem): Promise<void> => {
    setRefreshingId(server.mcp_id);
    try {
      await discoverTools(server.mcp_id);
      Toast.success(t('mcp.actions.discoverSuccess'));
      setDetailReloadKey((value) => value + 1);
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
                onEnterPress={commitKeyword}
                showClear
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
              <ListActionButton onClick={() => void reload()} action="refresh" />
            </>
          }
        />
        <RemoteTable<McpServerListItem>
          className="mcp-table app-table-fit app-table-nowrap"
          scroll={{ x: '100%' }}
          rowKey="mcp_id"
          loading={loading}
          columns={[
            {
              title: t('mcp.form.name'),
              dataIndex: 'name',
              width: 'auto',
              ellipsis: true,
              render: (value: string, record: McpServerListItem) => (
                <EntityLink
                  testId={`mcp-link-${record.key}`}
                  onClick={() => setDetail(record)}
                >
                  <TableText>{value}</TableText>
                </EntityLink>
              )
            },
            { title: t('mcp.form.key'), dataIndex: 'key', width: 'auto', ellipsis: true },
            { title: t('mcp.form.endpoint'), dataIndex: 'endpoint', width: 'auto', ellipsis: true },
            {
              title: t('mcp.columns.userScope'),
              dataIndex: 'user_scope',
              width: 128,
              render: (value: McpServerListItem['user_scope']) => (
                <Tag color={value === 'ALL' ? 'green' : 'blue'}>
                  {t(`mcp.scope.${value.toLowerCase()}`)}
                </Tag>
              )
            },
            { title: t('mcp.columns.selectedUserCount'), dataIndex: 'selected_user_count', width: 108 },
            { title: t('mcp.columns.toolCount'), dataIndex: 'tool_count', width: 88 },
            { title: t('mcp.columns.usingAgentCount'), dataIndex: 'using_agent_count', width: 108 },
            {
              title: t('mcp.columns.enabled'),
              dataIndex: 'enabled',
              width: 92,
              render: (value: boolean) => (
                <Tag color={value ? 'light-blue' : 'grey'}>
                  {t(`common.status.${value ? 'enabled' : 'disabled'}`)}
                </Tag>
              )
            },
            {
              title: t('mcp.columns.connectionStatus'),
              dataIndex: 'connection_status',
              width: 136,
              render: (value: string) => (
                <Tag color={value === 'AVAILABLE' ? 'green' : value === 'UNKNOWN' ? 'grey' : 'red'}>
                  {t(`mcp.connection.${value}`)}
                </Tag>
              )
            },
            {
              title: t('mcp.columns.lastDiscoveredAt'),
              dataIndex: 'last_discovered_at',
              width: 176,
              render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
            },
            {
              ...actionColumn(280),
              title: t('mcp.columns.actions'),
              render: (_: unknown, record: McpServerListItem) => (
                <div className="table-actions">
                  <Button theme="borderless" data-testid={`edit-mcp-${record.key}`} onClick={() => void openEdit(record)}>
                    {t('mcp.actions.edit')}
                  </Button>
                  <Button theme="borderless" data-testid={`test-mcp-${record.key}`} onClick={() => setTestTarget(record.mcp_id)}>
                    {t('mcp.test.action')}
                  </Button>
                  {account?.role === 'ADMIN' ? (
                    <Button theme="borderless" data-testid={`change-mcp-scope-${record.key}`} onClick={() => void openScope(record)}>
                      {t('mcp.scope.changeAction')}
                    </Button>
                  ) : null}
                  <Button
                    theme="borderless"
                    data-testid={`refresh-mcp-${record.key}`}
                    loading={refreshingId === record.mcp_id}
                    onClick={() => void refreshTools(record)}
                  >
                    {t('mcp.actions.discover')}
                  </Button>
                  <ConfirmAction
                    title={t('mcp.confirmDelete')}
                    testId={`delete-mcp-${record.key}`}
                    onConfirm={() => void remove(record)}
                  >
                    {t('mcp.actions.delete')}
                  </ConfirmAction>
                </div>
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
      <McpTestModal visible={testTarget !== null} serverId={testTarget} onCancel={() => setTestTarget(null)} />
      <McpScopeModal
        visible={scopeTarget !== null}
        serverId={scopeTarget?.mcp_id ?? null}
        currentScope={scopeTarget?.user_scope ?? 'SELECTED'}
        onCancel={() => setScopeTarget(null)}
        onSaved={() => {
          setScopeTarget(null);
          setDetailReloadKey((value) => value + 1);
          void reload();
        }}
      />
      <McpDetailSideSheet
        reloadKey={detailReloadKey}
        server={detail}
        onCancel={() => setDetail(null)}
        onMutated={() => void reload()}
      />
    </>
  );
}
