import { Button, Select, Spin, Table, Tabs, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { listUsers, type Page, type UserListItem } from '../user-identity/services/users';

import {
  bindMcp,
  bindSkill,
  getAgent,
  grantAgentUser,
  listAgentChannels,
  listAgentMcps,
  listAgentSkills,
  listAgentUsers,
  listAudits,
  removeAgentChannel,
  revokeAgentUser,
  unbindMcp,
  unbindSkill,
  type AgentChannelItem,
  type AgentDetail,
  type AgentGrantItem,
  type AgentMcpItem,
  type AgentSkillItem,
  type AuditItem
} from './services/agents';

export interface AgentDetailSideSheetProps {
  agentId: string;
  reloadKey?: number;
  onClose(): void;
  onEdit(): void;
  onMutated?(): void;
}

function RecentRuns({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<AuditItem[] | null>(null);

  useEffect(() => {
    listAudits({ resource_id: agentId, page: 1, page_size: 6 })
      .then((page) => setItems(page.items))
      .catch(() => setItems([]));
  }, [agentId]);

  if (items === null) {
    return <Spin />;
  }
  if (items.length === 0) {
    return <EmptyState title={t('agent.detail.noRuns')} />;
  }
  return (
    <Table<AuditItem>
      rowKey="id"
      size="small"
      pagination={false}
      dataSource={items}
      columns={[
        { title: t('agent.detail.runTime'), dataIndex: 'create_time', render: (v: string) => <DateTimeText value={v} /> },
        { title: t('agent.detail.runUser'), dataIndex: 'actor_user_id' },
        { title: t('agent.detail.runAction'), dataIndex: 'action' },
        { title: t('agent.detail.runResource'), dataIndex: 'resource_type' }
      ]}
    />
  );
}

function RelationPicker({
  onLoad,
  onSelect,
  testId
}: {
  onLoad: () => Promise<Array<{ value: string; label: string }>>;
  onSelect: (value: string) => Promise<void>;
  testId: string;
}) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<Array<{ value: string; label: string }>>([]);
  const [picked, setPicked] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // onLoad 为父级内联函数，仅挂载时加载一次候选
    onLoad()
      .then(setOptions)
      .catch(() => {
        // 候选加载失败由 ApiClient 提示
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const add = async (): Promise<void> => {
    if (!picked) {
      return;
    }
    setBusy(true);
    try {
      await onSelect(picked);
      setPicked(undefined);
    } catch {
      // [E-10] 目标资源不存在：Toast 由 ApiClient，保持当前 Tab
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
      <Select
        data-testid={testId}
        style={{ width: 300 }}
        showClear
        filter
        placeholder={t('agent.relation.pickPlaceholder')}
        optionList={options}
        value={picked}
        onChange={(value) => setPicked(value ? String(value) : undefined)}
      />
      <Button theme="solid" loading={busy} onClick={() => void add()}>
        {t('agent.relation.add')}
      </Button>
    </div>
  );
}

export function AgentDetailSideSheet(props: AgentDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [skills, setSkills] = useState<AgentSkillItem[]>([]);
  const [mcps, setMcps] = useState<AgentMcpItem[]>([]);
  const [grants, setGrants] = useState<AgentGrantItem[]>([]);
  const [channels, setChannels] = useState<AgentChannelItem[]>([]);
  const [activeTab, setActiveTab] = useState('basic');

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [agentDetail, skillPage, mcpPage, grantPage, channelPage] = await Promise.all([
        getAgent(props.agentId),
        listAgentSkills(props.agentId, { page: 1, page_size: 100 }),
        listAgentMcps(props.agentId, { page: 1, page_size: 100 }),
        listAgentUsers(props.agentId, { page: 1, page_size: 100 }),
        listAgentChannels(props.agentId, { page: 1, page_size: 100 })
      ]);
      setDetail(agentDetail);
      setSkills(skillPage.items);
      setMcps(mcpPage.items);
      setGrants(grantPage.items);
      setChannels(channelPage.items);
    } catch {
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, [props.agentId]);

  useEffect(() => {
    void reload();
  }, [reload, props.reloadKey]);

  return (
    <DetailSideSheet
      visible
      title={detail?.name ?? ''}
      subtitle={detail?.key ?? ''}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onClose}
      actions={
        <>
          <Button data-testid="edit-agent" theme="solid" onClick={() => props.onEdit()}>
            {t('agent.actions.edit')}
          </Button>
          <ConfirmAction theme="light" danger title={t('agent.confirmDelete')} onConfirm={props.onClose}>
            {t('agent.actions.delete')}
          </ConfirmAction>
        </>
      }
    >
      <Tabs.TabPane itemKey="basic" tab={t('agent.detail.tabs.basic')}>
        {loading || detail === null ? (
          <Spin />
        ) : (
          <>
            <DetailGrid
              items={[
                { label: t('agent.form.key'), value: detail.key },
                { label: t('agent.form.name'), value: detail.name },
                { label: t('agent.form.model'), value: detail.model_name },
                { label: t('agent.detail.revision'), value: detail.revision },
                {
                  label: t('agent.columns.enabled'),
                  value: t(`common.status.${detail.enabled ? 'enabled' : 'disabled'}`)
                },
                { label: t('agent.detail.skillCount'), value: detail.skill_count },
                { label: t('agent.detail.mcpCount'), value: detail.mcp_count },
                { label: t('agent.detail.channelCount'), value: detail.channel_count },
                { label: t('agent.detail.userCount'), value: detail.user_count },
                { label: t('agent.form.instructions'), value: detail.instructions }
              ]}
            />
            <h5>{t('agent.detail.recentRuns')}</h5>
            <RecentRuns agentId={detail.id} />
          </>
        )}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="skills" tab={t('agent.detail.tabs.skills')}>
        <RelationPicker
          testId="bind-skill-select"
          onLoad={async () => {
            const { listSkills } = await import('../skill-management/services/skills');
            const page = await listSkills({ page: 1, page_size: 100 });
            return page.items.map((s) => ({ value: s.id, label: `${s.name}（${s.key}）` }));
          }}
          onSelect={async (value) => {
            await bindSkill(props.agentId, value);
            await reload();
            props.onMutated?.();
          }}
        />
        {skills.length === 0 ? (
          <EmptyState title={t('common.empty')} />
        ) : (
          <Table<AgentSkillItem>
            rowKey="skill_id"
            size="small"
            pagination={false}
            dataSource={skills}
            columns={[
              { title: t('agent.relation.name'), dataIndex: 'name' },
              { title: t('agent.relation.key'), dataIndex: 'key' },
              { title: t('agent.relation.version'), dataIndex: 'current_artifact_version' },
              {
                title: t('agent.relation.enabled'),
                dataIndex: 'enabled',
                render: (v: boolean) => (
                  <Tag color={v ? 'green' : 'grey'}>{t(`common.status.${v ? 'enabled' : 'disabled'}`)}</Tag>
                )
              },
              {
                title: t('agent.columns.actions'),
                render: (_: unknown, record: AgentSkillItem) => (
                  <ConfirmAction
                    danger
                    title={t('agent.relation.confirmUnbind')}
                    onConfirm={async () => {
                      await unbindSkill(props.agentId, record.skill_id);
                      await reload();
                      props.onMutated?.();
                    }}
                  >
                    {t('agent.relation.unbind')}
                  </ConfirmAction>
                )
              }
            ]}
          />
        )}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="mcp" tab={t('agent.detail.tabs.mcp')}>
        <RelationPicker
          testId="bind-mcp-select"
          onLoad={async () => {
            const { listMcpServers } = await import('../mcp-management/services/mcpServers');
            const page = await listMcpServers({ page: 1, page_size: 100 });
            return page.items.map((m) => ({ value: m.mcp_id, label: `${m.name}（${m.key}）` }));
          }}
          onSelect={async (value) => {
            await bindMcp(props.agentId, value);
            await reload();
            props.onMutated?.();
          }}
        />
        {mcps.length === 0 ? (
          <EmptyState title={t('common.empty')} />
        ) : (
          <Table<AgentMcpItem>
            rowKey="mcp_server_id"
            size="small"
            pagination={false}
            dataSource={mcps}
            columns={[
              { title: t('agent.relation.name'), dataIndex: 'name' },
              { title: t('agent.relation.key'), dataIndex: 'key' },
              { title: t('agent.relation.toolCount'), dataIndex: 'tool_count' },
              {
                title: t('agent.relation.enabled'),
                dataIndex: 'enabled',
                render: (v: boolean) => (
                  <Tag color={v ? 'green' : 'grey'}>{t(`common.status.${v ? 'enabled' : 'disabled'}`)}</Tag>
                )
              },
              {
                title: t('agent.columns.actions'),
                render: (_: unknown, record: AgentMcpItem) => (
                  <ConfirmAction
                    danger
                    title={t('agent.relation.confirmUnbind')}
                    onConfirm={async () => {
                      await unbindMcp(props.agentId, record.mcp_server_id);
                      await reload();
                      props.onMutated?.();
                    }}
                  >
                    {t('agent.relation.unbind')}
                  </ConfirmAction>
                )
              }
            ]}
          />
        )}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="users" tab={t('agent.detail.tabs.users')}>
        <RelationPicker
          testId="grant-user-select"
          onLoad={async () => {
            const page: Page<UserListItem> = await listUsers({
              page: 1,
              page_size: 100,
              keyword: ''
            });
            return page.items.map((user: UserListItem) => ({
              value: user.id,
              label: `${user.display_name}（${user.user_code}）`
            }));
          }}
          onSelect={async (value) => {
            await grantAgentUser(props.agentId, value);
            await reload();
            props.onMutated?.();
          }}
        />
        {grants.length === 0 ? (
          <EmptyState title={t('common.empty')} />
        ) : (
          <Table<AgentGrantItem>
            rowKey="user_id"
            size="small"
            pagination={false}
            dataSource={grants}
            columns={[
              { title: t('agent.relation.userCode'), dataIndex: 'user_code' },
              { title: t('agent.relation.displayName'), dataIndex: 'display_name' },
              {
                title: t('agent.relation.grantedAt'),
                dataIndex: 'granted_at',
                render: (v: string) => <DateTimeText value={v} />
              },
              {
                title: t('agent.columns.actions'),
                render: (_: unknown, record: AgentGrantItem) => (
                  <ConfirmAction
                    danger
                    title={t('agent.relation.confirmRevoke')}
                    onConfirm={async () => {
                      await revokeAgentUser(props.agentId, record.user_id);
                      await reload();
                      props.onMutated?.();
                    }}
                  >
                    {t('agent.relation.revoke')}
                  </ConfirmAction>
                )
              }
            ]}
          />
        )}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="channels" tab={t('agent.detail.tabs.channels')}>
        {channels.length === 0 ? (
          <EmptyState title={t('common.empty')} description={t('agent.relation.noChannels')} />
        ) : (
          <Table<AgentChannelItem>
            rowKey="channel_account_id"
            size="small"
            pagination={false}
            dataSource={channels}
            columns={[
              { title: t('agent.relation.channelName'), dataIndex: 'name' },
              { title: t('agent.relation.botId'), dataIndex: 'bot_id' },
              { title: t('agent.relation.channel'), dataIndex: 'channel' },
              {
                title: t('agent.relation.enabled'),
                dataIndex: 'enabled',
                render: (v: boolean) => (
                  <Tag color={v ? 'green' : 'grey'}>{t(`common.status.${v ? 'enabled' : 'disabled'}`)}</Tag>
                )
              },
              {
                title: t('agent.columns.actions'),
                render: (_: unknown, record: AgentChannelItem) => (
                  <ConfirmAction
                    danger
                    title={t('agent.relation.confirmRemoveChannel')}
                    onConfirm={async () => {
                      await removeAgentChannel(props.agentId, record.channel_account_id);
                      await reload();
                      props.onMutated?.();
                    }}
                  >
                    {t('agent.relation.remove')}
                  </ConfirmAction>
                )
              }
            ]}
          />
        )}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
