import { actionColumn } from '../../components/common/tableLayout';
import { DataTable } from '../../components/common/DataTable';
import {
  Button,
  Empty,
  Modal,
  Select,
  Spin,
  Tabs,
  Typography
} from '@douyinfe/semi-ui';
import { IconPlus } from '@douyinfe/semi-icons';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../auth/AuthContext';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import { MetricCards } from '../../components/common/MetricCards';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { StatusTag } from '../../components/common/StatusTag';
import { TableText } from '../../components/common/TableText';
import {
  CredentialSchemaFields,
  credentialFields,
  validateCredentialValues
} from '../project-platform/CredentialSchemaFields';
import {
  getAdapter,
  listPlatforms,
  saveUserCredential,
  type AdapterMetadata,
  type PlatformItem
} from '../project-platform/services/platforms';
import {
  AGENT_PICKER_PAGE_SIZE,
  createBindCode,
  getWebchatToken,
  grantAgent,
  listAgentGrants,
  listAgentsForPicker,
  listIdentities,
  revokeAgent,
  unbindIdentity,
  type AgentGrant,
  type AgentSummary,
  type BindCode,
  type Identity,
  type Page,
  type UserDetail,
  type WebchatTokenMetadata
} from './services/users';
import { MemoryTab } from './MemoryTab';
import { UserChatTokenCard } from './UserChatTokenCard';

export interface UserDetailTabsProps {
  user: UserDetail;
  visible: boolean;
  onCancel(): void;
  /** 记忆等写操作成功后通知外层刷新用户详情（Tab 标签/概览计数）。 */
  onUserMutated?(): void;
}

const DEFAULT_TAB_PAGE_SIZE = 15;

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
          <DataTable
            className="app-table-fit user-grants-table"
            scroll={{ x: '100%' }}
            dataSource={items}
            rowKey="agent_id"
            pagination={false}
            columns={[
              { title: t('user.agents.nameLabel'), dataIndex: 'agent_name', width: 'auto', ellipsis: true,
                render: (value: string) => <TableText>{value}</TableText> },
              { title: t('user.agents.keyLabel'), dataIndex: 'agent_key', width: 'auto', ellipsis: true,
                render: (value: string) => <TableText>{value}</TableText> },
              {
                title: t('user.columns.status'),
                dataIndex: 'enabled',
                width: 96,
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
                width: 176,
                render: (value: string) => <DateTimeText value={value} />
              },
              {
                ...actionColumn(112),
                title: t('user.columns.action'),
                render: (_: unknown, entry: AgentGrant) => (
                  <ConfirmAction
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

/**
 * IM 身份区域的 Webchat Token 单例（API-03）：元信息由这里读取（带请求竞态守卫，
 * 切换用户时迟到响应不写状态），卡片只负责展示与写入；每次写入后都重读元信息，
 * 版本与状态一律以读取结果为准。
 *
 * 重读是**后台刷新**：只有首次读取（还没有元信息）才用骨架替换内容——否则刷新会把卡片卸载，
 * 本次签发的原文结果框随之消失（S-114 要求原文只在本次结果框显示、关闭才清除）。
 */
function WebchatTokenSection(props: { userId: string }) {
  const { t } = useTranslation();
  const [metadata, setMetadata] = useState<WebchatTokenMetadata | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const load = useCallback(async (): Promise<void> => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const next = await getWebchatToken(props.userId);
      if (seq !== requestSeq.current) {
        return;
      }
      setMetadata(next);
    } catch {
      if (seq !== requestSeq.current) {
        return;
      }
      setFailed(true);
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [props.userId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div className="detail-section-title">{t('user.webchatToken.title')}</div>
      {loading && metadata === null ? (
        <Spin style={{ display: 'block', margin: '24px auto' }} />
      ) : failed ? (
        <ErrorState onRetry={() => void load()} />
      ) : metadata ? (
        <UserChatTokenCard
          userId={props.userId}
          metadata={metadata}
          onChanged={() => void load()}
        />
      ) : null}
    </div>
  );
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
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const openForm = async (platform: PlatformItem): Promise<void> => {
    setTarget(platform);
    setValues({});
    setFormError(null);
    try {
      setAdapter(await getAdapter(platform.adapter_key));
    } catch {
      setAdapter(null);
    }
  };

  const closeForm = (): void => {
    setTarget(null);
    setValues({});
    setFormError(null);
  };

  const save = async (): Promise<void> => {
    if (!target) {
      return;
    }
    const invalid = validateCredentialValues(credentialFields(adapter), values);
    if (invalid) {
      setFormError(t(invalid.key));
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await saveUserCredential(target.platform_id, props.userId, values);
      closeForm();
      await reload();
    } catch {
      setFormError(t('common.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

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
          <DataTable
            className="app-table-fit user-credentials-table"
            scroll={{ x: '100%' }}
            dataSource={items}
            rowKey="platform_id"
            pagination={false}
            columns={[
              { title: t('user.credentials.platform'), dataIndex: 'name', width: 'auto', ellipsis: true,
                render: (value: string) => <TableText>{value}</TableText> },
              { title: t('user.credentials.adapter'), dataIndex: 'adapter_key', width: 120, ellipsis: true,
                render: (value: string) => <TableText>{value}</TableText> },
              {
                title: t('user.credentials.mode'),
                dataIndex: 'credential_mode',
                width: 144,
                render: (value: string) => t(`platform.credentialMode.${value}`)
              },
              {
                title: t('user.credentials.status'),
                dataIndex: 'user_credential_status',
                width: 128,
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
                dataIndex: 'user_credential_updated_time',
                width: 176,
                render: (value: string | null | undefined) =>
                  value ? <DateTimeText value={value} /> : '-'
              },
              {
                ...actionColumn(104),
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
        confirmLoading={saving}
        onOk={() => void save()}
        onCancel={closeForm}
      >
        {formError ? (
          <div className="detail-hint" data-testid="credential-form-error">
            {formError}
          </div>
        ) : null}
        <CredentialSchemaFields
          adapter={adapter}
          values={values}
          onChange={(name, value) => setValues((prev) => ({ ...prev, [name]: value }))}
        />
        <div className="detail-hint">{t('user.credentials.notEchoed')}</div>
      </FormModal>
    </div>
  );
}

function IdentityTab(props: { userId: string; canManageToken: boolean }) {
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
      {props.canManageToken ? <WebchatTokenSection userId={props.userId} /> : null}
      <div className="detail-section-title">{t('user.tabs.identities')}</div>
      <Button
        theme="solid"
        data-testid="generate-bind-code"
        style={{ marginBottom: 12 }}
        onClick={() => void generate()}
      >
        {t('user.identities.generate')}
      </Button>
      <Modal
        centered
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
      <TabState
        loading={loading}
        failed={failed}
        empty={t('user.identities.empty')}
        onRetry={() => void reload()}
      >
        {items.length > 0 ? (
          <DataTable
            className="app-table-fit user-identities-table"
            scroll={{ x: '100%' }}
            dataSource={items}
            rowKey="id"
            pagination={false}
            columns={[
              { title: t('user.identities.channel'), dataIndex: 'channel', width: 112, ellipsis: true,
                render: (value: string) => <TableText>{value}</TableText> },
              { title: t('user.identities.externalUserId'), dataIndex: 'external_user_id', width: 'auto', ellipsis: true,
                render: (value: string) => <TableText>{value}</TableText> },
              { title: 'bot_id', dataIndex: 'bot_id', width: 'auto', ellipsis: true,
                render: (value: string) => <TableText>{value}</TableText> },
              {
                title: t('user.identities.boundAt'),
                dataIndex: 'bound_at',
                width: 176,
                render: (value: string) => <DateTimeText value={value} />
              },
              {
                title: t('user.identities.lastActive'),
                dataIndex: 'last_active_at',
                width: 176,
                render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
              },
              {
                title: t('user.columns.status'),
                dataIndex: 'user_status',
                width: 96,
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
                ...actionColumn(80),
                title: t('user.columns.action'),
                render: (_: unknown, entry: Identity) => (
                  <ConfirmAction
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

export function UserDetailTabs(props: UserDetailTabsProps) {
  const { t } = useTranslation();
  const { account } = useAuth();
  const isAdmin = account?.role === 'ADMIN';
  const [activeTab, setActiveTab] = useState('basic');
  const { user } = props;

  return (
    <DetailSideSheet
      visible={props.visible}
      title={user.display_name}
      subtitle={user.user_code}
      activeTab={activeTab}
      onTabChange={setActiveTab}
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
      {isAdmin && (
        <Tabs.TabPane itemKey="credentials" tab={`${t('user.tabs.credentials')} (${user.credential_count})`}>
          <CredentialsTab userId={user.id} />
        </Tabs.TabPane>
      )}
      <Tabs.TabPane itemKey="identities" tab={`${t('user.tabs.identities')} (${user.identity_count})`}>
        <IdentityTab userId={user.id} canManageToken={isAdmin} />
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="memory" tab={`${t('user.tabs.memory')} (${user.memory_count})`}>
        <MemoryTab userId={user.id} canManage={isAdmin} onMutated={props.onUserMutated} />
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
