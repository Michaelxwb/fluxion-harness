import { Banner, Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { getMcpServer, type McpServerDetail, type McpServerListItem } from './services/mcpServers';
import { McpAgentTable } from './McpAgentTable';
import { McpSelectedUserTable } from './SelectedUserTable';
import { McpToolTable } from './McpToolTable';

export interface McpDetailSideSheetProps {
  server: McpServerListItem | null;
  onCancel(): void;
  reloadKey?: number;
  onMutated?(): void;
}

export function McpDetailSideSheet(props: McpDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<McpServerDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    if (!props.server) {
      setDetail(null);
      setFailed(false);
      return;
    }
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const loaded = await getMcpServer(props.server.mcp_id);
      if (current !== requestSeq.current) {
        return;
      }
      setDetail(loaded);
      setFailed(false);
    } catch {
      if (current === requestSeq.current) {
        setDetail(null);
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [props.server]);

  useEffect(() => {
    setActiveTab('basic');
    void reload();
  }, [reload, props.reloadKey]);

  if (!props.server) {
    return null;
  }

  return (
    <>
      <DetailSideSheet
        visible
        title={props.server.name}
        subtitle={props.server.key}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onCancel={props.onCancel}
      >
        <Tabs.TabPane itemKey="basic" tab={t('mcp.detail.tabs.basic')}>
          {failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : loading ? (
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
              <div className="detail-section-title">{t('mcp.detail.tabs.basic')}</div>
              <DetailGrid
                items={[
                  { label: t('mcp.form.key'), value: detail.key },
                  { label: t('mcp.form.name'), value: detail.name },
                  { label: t('mcp.form.transport'), value: detail.transport },
                  { label: t('mcp.form.endpoint'), value: detail.endpoint },
                  {
                    label: t('mcp.columns.connectionStatus'),
                    value: t(`mcp.connection.${detail.connection_status}`)
                  },
                  { label: t('mcp.columns.toolCount'), value: detail.tool_count },
                  { label: t('mcp.detail.catalogRevision'), value: detail.tool_catalog_revision },
                  { label: t('mcp.detail.catalogHash'), value: detail.tool_catalog_hash ?? '-' },
                  {
                    fullWidth: true, label: t('mcp.detail.lastDiscoveryError'),
                    value: detail.last_discovery_error ?? '-'
                  },
                  {
                    label: t('mcp.columns.userScope'),
                    value: t(`mcp.scope.${detail.user_scope.toLowerCase()}`)
                  },
                  {
                    label: t('mcp.columns.enabled'),
                    value: t(`common.status.${detail.enabled ? 'enabled' : 'disabled'}`)
                  },
                  {
                    label: t('mcp.detail.authConfigured'),
                    value: t(detail.auth_secret_configured ? 'mcp.authConfigured' : 'mcp.authMissing')
                  },
                  { label: t('mcp.columns.usingAgentCount'), value: detail.using_agent_count },
                  { label: t('mcp.columns.selectedUserCount'), value: detail.selected_user_count },
                  {
                    label: t('mcp.columns.lastDiscoveredAt'),
                    value: detail.last_discovered_at ? (
                      <DateTimeText value={detail.last_discovered_at} />
                    ) : (
                      '-'
                    )
                  }
                ]}
              />
            </>
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="tools" tab={t('mcp.detail.tabs.tools')}>
          <div className="detail-section-title">{t('mcp.detail.tabs.tools')}</div>
          {failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : detail === null ? (
            <Spin />
          ) : (
            <McpToolTable serverId={detail.mcp_id} reloadKey={props.reloadKey ?? 0} />
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="agents" tab={t('mcp.detail.tabs.agents')}>
          <div className="detail-section-title">{t('mcp.detail.tabs.agents')}</div>
          {failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : detail === null ? (
            <Spin />
          ) : (
            <McpAgentTable serverId={detail.mcp_id} />
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="users" tab={t('mcp.detail.tabs.users')}>
          <div className="detail-section-title">{t('mcp.detail.tabs.users')}</div>
          {failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : detail === null ? (
            <Spin />
          ) : (
            <McpSelectedUserTable
              serverId={detail.mcp_id}
              userScope={detail.user_scope}
              onChanged={() => { void reload(); props.onMutated?.(); }}
            />
          )}
        </Tabs.TabPane>
      </DetailSideSheet>
    </>
  );
}
