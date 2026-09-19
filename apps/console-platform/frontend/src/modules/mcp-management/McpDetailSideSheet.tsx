import { Banner, Button, Popconfirm, Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { getMcpServer, type McpServerListItem } from './services/mcpServers';

export interface McpDetailSideSheetProps {
  server: McpServerListItem | null;
  onCancel(): void;
  onEdit(server: NonNullable<McpServerListItem | null>): void;
  onDelete(server: NonNullable<McpServerListItem | null>): void;
  onMutated?(): void;
  children?: (detail: NonNullable<McpServerListItem | null> & { mcp_id: string }) => React.ReactNode;
}

interface McpDetail extends McpServerListItem {
  tool_catalog_revision: number;
  tool_catalog_hash: string | null;
  last_discovery_error: string | null;
  connect_timeout_ms: number;
  tool_cache_ttl_sec: number;
  auth_config: Record<string, unknown>;
  auth_secret_configured: boolean;
}

export function McpDetailSideSheet(props: McpDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<McpDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');

  const reload = useCallback(async () => {
    if (!props.server) {
      setDetail(null);
      return;
    }
    setLoading(true);
    try {
      setDetail(await getMcpServer(props.server.mcp_id));
    } catch {
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, [props.server]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!props.server) {
    return null;
  }

  return (
    <DetailSideSheet
      visible
      title={props.server.name}
      subtitle={props.server.key}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onCancel}
      actions={
        <>
          <Button data-testid="edit-mcp" theme="solid" onClick={() => props.onEdit(props.server!)}>
            {t('mcp.actions.edit')}
          </Button>
          <Popconfirm title={t('mcp.confirmDelete')} onConfirm={() => props.onDelete(props.server!)}>
            <Button type="danger">{t('mcp.actions.delete')}</Button>
          </Popconfirm>
        </>
      }
    >
      <Tabs.TabPane itemKey="basic" tab={t('mcp.detail.tabs.basic')}>
        {loading ? (
          <Spin />
        ) : detail === null ? (
          <EmptyState title={t('common.empty')} />
        ) : (
          <>
            <Banner
              type={detail.connection_status === 'AVAILABLE' ? 'info' : 'warning'}
              closeIcon={null}
              description={t(`mcp.connection.${detail.connection_status}`)}
            />
            <DetailGrid
              items={[
                { label: t('mcp.form.key'), value: detail.key },
                { label: t('mcp.form.name'), value: detail.name },
                { label: t('mcp.form.transport'), value: detail.transport },
                { label: t('mcp.form.endpoint'), value: detail.endpoint },
                { label: t('mcp.columns.connectionStatus'), value: t(`mcp.connection.${detail.connection_status}`) },
                { label: t('mcp.columns.toolCount'), value: detail.tool_count },
                { label: t('mcp.detail.catalogRevision'), value: detail.tool_catalog_revision },
                { label: t('mcp.columns.userScope'), value: t(`mcp.scope.${detail.user_scope.toLowerCase()}`) },
                { label: t('mcp.columns.enabled'), value: t(`common.status.${detail.enabled ? 'enabled' : 'disabled'}`) },
                { label: t('mcp.detail.authConfigured'), value: t(detail.auth_secret_configured ? 'mcp.authConfigured' : 'mcp.authMissing') },
                { label: t('mcp.columns.usingAgentCount'), value: detail.using_agent_count },
                { label: t('mcp.columns.selectedUserCount'), value: detail.selected_user_count },
                {
                  label: t('mcp.columns.lastDiscoveredAt'),
                  value: detail.last_discovered_at ? <DateTimeText value={detail.last_discovered_at} /> : '-'
                }
              ]}
            />
          </>
        )}
      </Tabs.TabPane>
      {props.children ? props.children(detail ?? props.server as never) : null}
    </DetailSideSheet>
  );
}
