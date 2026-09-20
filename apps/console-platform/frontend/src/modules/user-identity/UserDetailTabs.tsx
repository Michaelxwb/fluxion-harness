import {
  Button,
  Empty,
  Form,
  Modal,
  Select,
  Spin,
  Table,
  Tabs,
  Typography
} from '@douyinfe/semi-ui';
import { IconPlus } from '@douyinfe/semi-icons';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import { MetricCards } from '../../components/common/MetricCards';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { StatusTag } from '../../components/common/StatusTag';
import {
  getAdapter,
  listPlatforms,
  saveUserCredential,
  type AdapterMetadata,
  type PlatformItem
} from '../project-platform/services/platforms';
import {
  AGENT_PICKER_PAGE_SIZE,
  clearMemory,
  createBindCode,
  deleteMemory,
  grantAgent,
  listAgentGrants,
  listAgentsForPicker,
  listIdentities,
  listMemory,
  revokeAgent,
  unbindIdentity,
  type AgentGrant,
  type AgentSummary,
  type BindCode,
  type Identity,
  type Memory,
  type Page,
  type UserDetail
} from './services/users';

export interface UserDetailTabsProps {
  user: UserDetail;
  visible: boolean;
  onCancel(): void;
  onEdit(): void;
}

const DEFAULT_TAB_PAGE_SIZE = 10;

interface AsyncList<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  failed: boolean;
  reload(): Promise<void>;
  changePage(page: number): void;
  changePageSize(pageSize: number): void;
}

function useAsyncList<T>(
  loader: (page: number, pageSize: number) => Promise<Page<T>>,
  deps: unknown[]
): AsyncList<T> {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TAB_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const reload = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      const result = await loaderRef.current(page, pageSize);
      setItems(result.items);
      setTotal(result.total);
    } catch {
      setFailed(true);
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, ...deps]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return {
    items,
    total,
    page,
    pageSize,
    loading,
    failed,
    reload,
    changePage: setPage,
    changePageSize: (size: number) => {
      setPageSize(size);
      setPage(1);
    }
  };
}

function formatMemoryContent(value: Record<string, unknown>): string {
  const entries = Object.values(value);
  if (entries.length > 0 && entries.every((entry) => typeof entry === 'string' || typeof entry === 'number')) {
    return entries.join('；');
  }
  return JSON.stringify(value);
}

function TabState(props: {
  loading: boolean;
  failed: boolean;
  empty: string;
  onRetry(): void;
  children: ReactNode;
}) {
  if (props.loading) {
    return <Spin style={{ display: 'block', margin: '32px auto' }} />;
  }
  if (props.failed) {
    return <ErrorState onRetry={props.onRetry} />;
  }
  if (props.children === null || props.children === undefined) {
    return <Empty description={props.empty} />;
  }
  return <>{props.children}</>;
}

function TabPagination({ list }: { list: AsyncList<unknown> }) {
  if (list.failed || list.total === 0) {
    return null;
  }
  return (
    <PaginationFooter
      page={list.page}
      pageSize={list.pageSize}
      total={list.total}
      onPageChange={list.changePage}
      onPageSizeChange={list.changePageSize}
    />
  );
}

function AgentGrantTab(props: { userId: string }) {
  const { t } = useTranslation();
  const list = useAsyncList<AgentGrant>(
    (page, pageSize) => listAgentGrants(props.userId, { page, page_size: pageSize }),
    [props.userId]
  );
  const { items, loading, failed, reload } = list;
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agentsTotal, setAgentsTotal] = useState(0);
  const [selected, setSelected] = useState<string>('');
  const [grantVisible, setGrantVisible] = useState(false);

  useEffect(() => {
    listAgentsForPicker()
      .then((page) => {
        setAgents(page.items);
        setAgentsTotal(page.total);
      })
      .catch(() => {
        setAgents([]);
        setAgentsTotal(0);
      });
  }, []);

  const availableAgents = agents.filter((agent) => !items.some((entry) => entry.agent_id === agent.id));
  const grantableTotal = Math.max(0, agentsTotal - list.total);

  const closeGrant = (): void => {
    setGrantVisible(false);
    setSelected('');
  };

  const grant = async (): Promise<void> => {
    if (!selected) {
      return;
    }
    await grantAgent(props.userId, selected);
    closeGrant();
    await reload();
  };

  const revoke = async (agentId: string): Promise<void> => {
    await revokeAgent(props.userId, agentId);
    await reload();
  };

  return (
    <div>
      <Button
        theme="solid"
        icon={<IconPlus />}
        style={{ marginBottom: 12 }}
        onClick={() => setGrantVisible(true)}
      >
        {t('user.agents.grant')}
      </Button>
      <FormModal
        visible={grantVisible}
        title={t('user.agents.grant')}
        okText={t('user.agents.grant')}
        okButtonProps={{ disabled: !selected }}
        onOk={() => void grant()}
        onCancel={closeGrant}
      >
        {availableAgents.length > 0 ? (
          <>
            <Select
              value={selected}
              onChange={(value) => setSelected(String(value))}
              placeholder={t('user.agents.grant')}
              style={{ width: '100%' }}
              optionList={availableAgents.map((agent) => ({ value: agent.id, label: agent.name }))}
            />
            <div className="detail-hint">{t('user.agents.grantHint')}</div>
            {grantableTotal > availableAgents.length ? (
              <div className="detail-hint" data-testid="agent-picker-truncated">
                {t('user.agents.pickerTruncated', {
                  shown: availableAgents.length,
                  total: grantableTotal
                })}
              </div>
            ) : null}
          </>
        ) : (
          <Typography.Paragraph type="tertiary">{t('user.agents.allGranted')}</Typography.Paragraph>
        )}
      </FormModal>
      <div className="detail-section-title">{t('user.tabs.agents')}</div>
      <TabState
        loading={loading}
        failed={failed}
        empty={t('user.agents.empty')}
        onRetry={() => void reload()}
      >
        {items.length > 0 ? (
          <Table
            dataSource={items}
            rowKey="agent_id"
            pagination={false}
            columns={[
              { title: t('user.agents.nameLabel'), dataIndex: 'agent_name' },
              { title: t('user.agents.keyLabel'), dataIndex: 'agent_key' },
              {
                title: t('user.columns.status'),
                dataIndex: 'enabled',
                render: (value: boolean) => (
                  <StatusTag
                    status={value}
                    options={{
                      true: { color: 'green', label: t('common.status.enabled') },
                      false: { color: 'grey', label: t('common.status.disabled') }
                    }}
                  />
                )
              },
              {
                title: t('user.agents.grantedAt'),
                dataIndex: 'granted_at',
                render: (value: string) => <DateTimeText value={value} />
              },
              {
                title: t('user.columns.action'),
                render: (_: unknown, entry: AgentGrant) => (
                  <ConfirmAction
                    danger
                    title={t('user.common.confirmRevoke')}
                    onConfirm={() => void revoke(entry.agent_id)}
                  >
                    {t('user.agents.revoke')}
                  </ConfirmAction>
                )
              }
            ]}
          />
        ) : null}
      </TabState>
      <TabPagination list={list} />
      <div className="detail-hint">{t('user.agents.hint')}</div>
    </div>
  );
}

function credentialTagKey(status?: string): { color: 'green' | 'red' | 'grey'; key: string } {
  if (status === 'ACTIVE') {
    return { color: 'green', key: 'user.credentials.configured' };
  }
  if (status === 'INVALID') {
    return { color: 'red', key: 'user.credentials.invalid' };
  }
  return { color: 'grey', key: 'user.credentials.notConfigured' };
}

function CredentialsTab(props: { userId: string }) {
  const { t } = useTranslation();
  const list = useAsyncList<PlatformItem>(
    (page, pageSize) => listPlatforms({ page, page_size: pageSize, user_id: props.userId }),
    [props.userId]
  );
  const { items, loading, failed, reload } = list;
  const [target, setTarget] = useState<PlatformItem | null>(null);
  const [adapter, setAdapter] = useState<AdapterMetadata | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});

  const openForm = async (platform: PlatformItem): Promise<void> => {
    setTarget(platform);
    setValues({});
    try {
      setAdapter(await getAdapter(platform.adapter_key));
    } catch {
      setAdapter(null);
    }
  };

  const save = async (): Promise<void> => {
    if (!target) {
      return;
    }
    await saveUserCredential(target.platform_id, props.userId, values);
    setTarget(null);
    await reload();
  };

  const fields = Object.entries(
    (adapter?.credential_schema as { properties?: Record<string, { title?: string; 'x-secret'?: boolean }> })
      ?.properties ?? {}
  );

  return (
    <div>
      <div className="detail-section-title">{t('user.tabs.credentials')}</div>
      <TabState
        loading={loading}
        failed={failed}
        empty={t('user.credentials.empty')}
        onRetry={() => void reload()}
      >
        {items.length > 0 ? (
          <Table
            dataSource={items}
            rowKey="platform_id"
            pagination={false}
            columns={[
              { title: t('user.credentials.platform'), dataIndex: 'name' },
              { title: t('user.credentials.adapter'), dataIndex: 'adapter_key' },
              {
                title: t('user.credentials.mode'),
                dataIndex: 'credential_mode',
                render: (value: string) => t(`platform.credentialMode.${value}`)
              },
              {
                title: t('user.credentials.status'),
                dataIndex: 'user_credential_status',
                render: (value: string | undefined) => {
                  const status = value ?? 'NONE';
                  const tag = credentialTagKey(value);
                  return (
                    <StatusTag
                      status={status}
                      options={{ [status]: { color: tag.color, label: t(tag.key) } }}
                    />
                  );
                }
              },
              {
                title: t('user.credentials.updatedAt'),
                dataIndex: 'update_time',
                render: (value: string) => <DateTimeText value={value} />
              },
              {
                title: t('user.columns.action'),
                render: (_: unknown, entry: PlatformItem) => (
                  <Button theme="borderless" onClick={() => void openForm(entry)}>
                    {entry.user_credential_status === 'ACTIVE'
                      ? t('user.credentials.update')
                      : t('user.credentials.configure')}
                  </Button>
                )
              }
            ]}
          />
        ) : null}
      </TabState>
      <TabPagination list={list} />
      <div className="detail-hint">{t('user.credentials.hint')}</div>
      <FormModal
        visible={target !== null}
        width={520}
        title={t('user.credentials.formTitle')}
        okText={t('common.save')}
        onOk={() => void save()}
        onCancel={() => setTarget(null)}
      >
        {fields.map(([name, definition]) => (
          <Form.Input
            key={name}
            field={name}
            label={definition.title ?? name}
            mode={definition['x-secret'] === true ? 'password' : undefined}
            onChange={(value: string) => setValues((prev) => ({ ...prev, [name]: value }))}
          />
        ))}
        <div className="detail-hint">{t('user.credentials.notEchoed')}</div>
      </FormModal>
    </div>
  );
}

function IdentityTab(props: { userId: string }) {
  const { t } = useTranslation();
  const list = useAsyncList<Identity>(
    (page, pageSize) => listIdentities(props.userId, { page, page_size: pageSize }),
    [props.userId]
  );
  const { items, loading, failed, reload } = list;
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
      <div className="detail-section-title">{t('user.tabs.identities')}</div>
      <TabState
        loading={loading}
        failed={failed}
        empty={t('user.identities.empty')}
        onRetry={() => void reload()}
      >
        {items.length > 0 ? (
          <Table
            dataSource={items}
            rowKey="id"
            pagination={false}
            columns={[
              { title: t('user.identities.channel'), dataIndex: 'channel' },
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
                title: t('user.columns.status'),
                dataIndex: 'user_status',
                render: (value: string) => (
                  <StatusTag
                    status={value}
                    options={{
                      ACTIVE: { color: 'green', label: t('common.status.enabled') },
                      DISABLED: { color: 'grey', label: t('common.status.disabled') }
                    }}
                  />
                )
              },
              {
                title: t('user.columns.action'),
                render: (_: unknown, entry: Identity) => (
                  <ConfirmAction
                    danger
                    title={t('user.common.confirmUnbind')}
                    onConfirm={() => void unbind(entry.id)}
                  >
                    {t('user.identities.unbind')}
                  </ConfirmAction>
                )
              }
            ]}
          />
        ) : null}
      </TabState>
      <TabPagination list={list} />
      <div className="detail-hint">{t('user.identities.hint')}</div>
    </div>
  );
}

function MemoryTab(props: { userId: string }) {
  const { t } = useTranslation();
  const list = useAsyncList<Memory>(
    (page, pageSize) => listMemory(props.userId, { page, page_size: pageSize }),
    [props.userId]
  );
  const { items, loading, failed, reload } = list;

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
      <ConfirmAction
        danger
        style={{ marginBottom: 12 }}
        title={t('user.common.confirmClear')}
        onConfirm={() => void clearAll()}
      >
        {t('user.memory.clear')}
      </ConfirmAction>
      <div className="detail-section-title">{t('user.tabs.memory')}</div>
      <TabState
        loading={loading}
        failed={failed}
        empty={t('user.memory.empty')}
        onRetry={() => void reload()}
      >
        {items.length > 0 ? (
          <Table
            dataSource={items}
            rowKey="id"
            pagination={false}
            columns={[
              {
                title: t('user.memory.content'),
                dataIndex: 'content',
                render: (value: Record<string, unknown>) => formatMemoryContent(value)
              },
              { title: t('user.memory.source'), dataIndex: 'source_type' },
              {
                title: t('user.memory.updated'),
                dataIndex: 'update_time',
                render: (value: string) => <DateTimeText value={value} />
              },
              {
                title: t('user.columns.action'),
                render: (_: unknown, entry: Memory) => (
                  <ConfirmAction
                    danger
                    title={t('user.memory.confirmDelete')}
                    onConfirm={() => void remove(entry.id)}
                  >
                    {t('user.memory.delete')}
                  </ConfirmAction>
                )
              }
            ]}
          />
        ) : null}
      </TabState>
      <TabPagination list={list} />
      <div className="detail-hint">{t('user.memory.hint')}</div>
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
                <StatusTag
                  status={user.status}
                  options={{
                    ACTIVE: { color: 'green', label: t('common.status.enabled') },
                    DISABLED: { color: 'grey', label: t('common.status.disabled') }
                  }}
                />
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
        <CredentialsTab userId={user.id} />
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
