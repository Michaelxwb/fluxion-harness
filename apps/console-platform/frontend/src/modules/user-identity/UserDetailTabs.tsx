import {
  Banner,
  Button,
  Empty,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography
} from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { MetricCards } from '../../components/common/MetricCards';
import {
  clearMemory,
  createBindCode,
  deleteMemory,
  grantAgent,
  listAgentGrants,
  listAgents,
  listIdentities,
  listMemory,
  revokeAgent,
  unbindIdentity,
  type AgentGrant,
  type AgentSummary,
  type BindCode,
  type Identity,
  type Memory,
  type UserDetail
} from './services/users';

export interface UserDetailTabsProps {
  user: UserDetail;
  visible: boolean;
  onCancel(): void;
  onEdit(): void;
}

function useAsyncList<T>(loader: () => Promise<T[]>, deps: unknown[]) {
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      setItems(await loader());
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { items, loading, failed, reload };
}

function TabState(props: { loading: boolean; failed: boolean; empty: string; children: ReactNode }) {
  const { t } = useTranslation();
  if (props.loading) {
    return <Spin style={{ display: 'block', margin: '32px auto' }} />;
  }
  if (props.failed) {
    return <Banner type="danger" description={t('user.common.loadFailed')} />;
  }
  if (props.children === null || props.children === undefined) {
    return <Empty description={props.empty} />;
  }
  return <>{props.children}</>;
}

function AgentGrantTab(props: { userId: string }) {
  const { t } = useTranslation();
  const { items, loading, failed, reload } = useAsyncList<AgentGrant>(
    async () => (await listAgentGrants(props.userId)).items,
    [props.userId]
  );
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selected, setSelected] = useState<string>('');

  useEffect(() => {
    listAgents()
      .then((page) => setAgents(page.items))
      .catch(() => setAgents([]));
  }, []);

  const grant = async (): Promise<void> => {
    if (!selected) {
      return;
    }
    await grantAgent(props.userId, selected);
    setSelected('');
    await reload();
  };

  const revoke = async (agentId: string): Promise<void> => {
    await revokeAgent(props.userId, agentId);
    await reload();
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <Select
          value={selected}
          onChange={(value) => setSelected(String(value))}
          placeholder={t('user.agents.grant')}
          style={{ width: 260 }}
          optionList={agents.map((agent) => ({ value: agent.id, label: agent.name }))}
        />
        <Button theme="solid" disabled={!selected} onClick={() => void grant()}>
          {t('user.agents.grant')}
        </Button>
      </div>
      <TabState loading={loading} failed={failed} empty={t('user.agents.empty')}>
        {items.length > 0 ? (
          <Table
            dataSource={items}
            rowKey="agent_id"
            pagination={false}
            columns={[
              { title: t('user.columns.displayName'), dataIndex: 'agent_name' },
              { title: 'Key', dataIndex: 'agent_key' },
              {
                title: t('user.columns.status'),
                dataIndex: 'enabled',
                render: (value: boolean) => (
                  <Tag color={value ? 'green' : 'grey'}>
                    {t(value ? 'common.status.enabled' : 'common.status.disabled')}
                  </Tag>
                )
              },
              {
                title: '',
                render: (_: unknown, entry: AgentGrant) => (
                  <Popconfirm title={t('user.common.confirmRevoke')} onConfirm={() => void revoke(entry.agent_id)}>
                    <Button theme="borderless" type="danger">
                      {t('user.agents.revoke')}
                    </Button>
                  </Popconfirm>
                )
              }
            ]}
          />
        ) : null}
      </TabState>
    </div>
  );
}

function CredentialsTab() {
  const { t } = useTranslation();
  return <Banner type="danger" description={t('user.common.loadFailed')} />;
}

function IdentityTab(props: { userId: string }) {
  const { t } = useTranslation();
  const { items, loading, failed, reload } = useAsyncList<Identity>(
    async () => (await listIdentities(props.userId)).items,
    [props.userId]
  );
  const [bindCode, setBindCode] = useState<BindCode | null>(null);

  const generate = async (): Promise<void> => {
    try {
      setBindCode(await createBindCode(props.userId));
    } catch {
      setBindCode(null);
    }
  };

  const unbind = async (identityId: string): Promise<void> => {
    await unbindIdentity(props.userId, identityId);
    await reload();
  };

  return (
    <div>
      <Button
        theme="solid"
        data-testid="generate-bind-code"
        style={{ marginBottom: 12 }}
        onClick={() => void generate()}
      >
        {t('user.identities.generate')}
      </Button>
      <Modal
        visible={bindCode !== null}
        title={t('user.bindCode.title')}
        footer={null}
        onCancel={() => setBindCode(null)}
      >
        {bindCode ? (
          <>
            <Typography.Title heading={3} data-testid="bind-code-value">
              {bindCode.bind_code}
            </Typography.Title>
            <Typography.Paragraph>
              {t('user.bindCode.expiresAt')}: <DateTimeText value={bindCode.expires_at} />
            </Typography.Paragraph>
            <Typography.Paragraph type="tertiary">{t('user.bindCode.hint')}</Typography.Paragraph>
          </>
        ) : null}
      </Modal>
      <TabState loading={loading} failed={failed} empty={t('user.identities.empty')}>
        {items.length > 0 ? (
          <Table
            dataSource={items}
            rowKey="id"
            pagination={false}
            columns={[
              { title: 'Channel', dataIndex: 'channel' },
              { title: t('user.identities.externalUserId'), dataIndex: 'external_user_id' },
              { title: 'bot_id', dataIndex: 'bot_id' },
              {
                title: t('user.identities.boundAt'),
                dataIndex: 'bound_at',
                render: (value: string) => <DateTimeText value={value} />
              },
              {
                title: t('user.identities.lastActive'),
                dataIndex: 'last_active_at',
                render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
              },
              {
                title: '',
                render: (_: unknown, entry: Identity) => (
                  <Popconfirm title={t('user.common.confirmUnbind')} onConfirm={() => void unbind(entry.id)}>
                    <Button theme="borderless" type="danger">
                      {t('user.identities.unbind')}
                    </Button>
                  </Popconfirm>
                )
              }
            ]}
          />
        ) : null}
      </TabState>
    </div>
  );
}

function MemoryTab(props: { userId: string }) {
  const { t } = useTranslation();
  const { items, loading, failed, reload } = useAsyncList<Memory>(
    async () => (await listMemory(props.userId)).items,
    [props.userId]
  );

  const remove = async (memoryId: string): Promise<void> => {
    await deleteMemory(props.userId, memoryId);
    await reload();
  };

  const clearAll = async (): Promise<void> => {
    await clearMemory(props.userId);
    await reload();
  };

  return (
    <div>
      <Popconfirm title={t('user.common.confirmClear')} onConfirm={() => void clearAll()}>
        <Button type="danger" style={{ marginBottom: 12 }}>
          {t('user.memory.clear')}
        </Button>
      </Popconfirm>
      <TabState loading={loading} failed={failed} empty={t('user.memory.empty')}>
        {items.length > 0 ? (
          <Table
            dataSource={items}
            rowKey="id"
            pagination={false}
            columns={[
              { title: t('user.memory.category'), dataIndex: 'category' },
              {
                title: t('user.memory.content'),
                dataIndex: 'content',
                render: (value: Record<string, unknown>) => JSON.stringify(value)
              },
              {
                title: '',
                render: (_: unknown, entry: Memory) => (
                  <Popconfirm title={t('user.common.confirmClear')} onConfirm={() => void remove(entry.id)}>
                    <Button theme="borderless" type="danger">
                      {t('user.memory.delete')}
                    </Button>
                  </Popconfirm>
                )
              }
            ]}
          />
        ) : null}
      </TabState>
    </div>
  );
}

export function UserDetailTabs(props: UserDetailTabsProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('basic');
  const { user } = props;

  return (
    <DetailSideSheet
      visible={props.visible}
      title={user.display_name}
      subtitle={user.user_code}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      actions={
        <Button theme="borderless" onClick={props.onEdit}>
          {t('user.detail.edit')}
        </Button>
      }
      onCancel={props.onCancel}
    >
      <Tabs.TabPane itemKey="basic" tab={t('user.tabs.basic')}>
        <div className="detail-section-title">{t('user.detail.basicTitle')}</div>
        <DetailGrid
          items={[
            { label: t('user.detail.name'), value: user.display_name },
            { label: t('user.detail.account'), value: user.user_code },
            {
              label: t('user.form.status'),
              value: (
                <Tag color={user.status === 'ACTIVE' ? 'green' : 'grey'}>
                  {t(user.status === 'ACTIVE' ? 'common.status.enabled' : 'common.status.disabled')}
                </Tag>
              )
            },
            { label: t('user.detail.createdAt'), value: <DateTimeText value={user.create_time} /> },
            {
              label: t('user.detail.agentCountLabel'),
              value: t('user.detail.countValue', { count: user.agent_grant_count })
            },
            {
              label: t('user.detail.credentialCountLabel'),
              value:
                user.credential_count > 0
                  ? t('user.detail.credentialCountValue', { count: user.credential_count })
                  : t('user.detail.countValue', { count: user.credential_count })
            },
            {
              label: t('user.detail.identityCountLabel'),
              value: t('user.detail.countValue', { count: user.identity_count })
            },
            {
              label: t('user.detail.memoryCountLabel'),
              value: t('user.detail.memoryCountValue', { count: user.memory_count })
            },
            { label: t('user.detail.updatedAt'), value: <DateTimeText value={user.update_time} /> },
            { label: t('user.detail.userId'), value: user.id }
          ]}
        />
        <div className="detail-section-title">{t('user.detail.overview')}</div>
        <MetricCards
          items={[
            {
              label: t('user.metric.agent'),
              value: user.agent_grant_count,
              hint: t('user.metric.agentHint')
            },
            {
              label: t('user.metric.credential'),
              value: user.credential_count,
              hint: t('user.metric.credentialHint')
            },
            {
              label: t('user.metric.identity'),
              value: user.identity_count,
              hint: t('user.metric.identityHint')
            },
            {
              label: t('user.metric.memory'),
              value: user.memory_count,
              hint: t('user.metric.memoryHint')
            }
          ]}
        />
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="agents" tab={`${t('user.tabs.agents')} (${user.agent_grant_count})`}>
        <AgentGrantTab userId={user.id} />
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="credentials" tab={`${t('user.tabs.credentials')} (${user.credential_count})`}>
        <CredentialsTab />
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="identities" tab={`${t('user.tabs.identities')} (${user.identity_count})`}>
        <IdentityTab userId={user.id} />
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="memory" tab={`${t('user.tabs.memory')} (${user.memory_count})`}>
        <MemoryTab userId={user.id} />
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
