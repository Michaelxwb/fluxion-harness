import { Button, Input, Select, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { EmptyState } from '../../components/common/EmptyState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { UserDetailTabs } from './UserDetailTabs';
import { UserFormModal } from './UserFormModal';
import { getUser, listUsers, type UserDetail, type UserListItem } from './services/users';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', status: '' };

export function UserPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [items, setItems] = useState<UserListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [formUser, setFormUser] = useState<UserDetail | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listUsers({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        status: params.status || undefined
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

  const openDetail = async (id: string): Promise<void> => {
    setDetail(await getUser(id));
  };

  const onSaved = async (saved: UserDetail): Promise<void> => {
    setFormVisible(false);
    setFormUser(null);
    if (detail?.id === saved.id) {
      setDetail(await getUser(saved.id));
    }
    await reload();
  };

  return (
    <>
      <PageHeader title={t('user.title')} description={t('user.subtitle')} />
      <PageSection>
      <ModuleToolbar
        actions={
          <Button
            theme="solid"
            data-testid="create-user"
            onClick={() => {
              setFormUser(null);
              setFormVisible(true);
            }}
          >
            {t('user.add')}
          </Button>
        }
        search={
          <>
            <Input
              value={params.keyword}
              placeholder={t('user.searchPlaceholder')}
              style={{ width: 220 }}
              onChange={(value) => setParams((prev) => ({ ...prev, keyword: value, page: 1 }))}
            />
            <Select
              value={params.status || undefined}
              style={{ width: 150 }}
              placeholder={t('user.columns.status')}
              showClear
              optionList={[
                { value: 'ACTIVE', label: t('common.status.enabled') },
                { value: 'DISABLED', label: t('common.status.disabled') }
              ]}
              onChange={(value) => setParams((prev) => ({ ...prev, status: value ? String(value) : '', page: 1 }))}
            />
            <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
          </>
        }
      />
      <RemoteTable<UserListItem>
        rowKey="id"
        loading={loading}
        columns={[
          {
            title: t('user.columns.displayName'),
            dataIndex: 'display_name',
            render: (value: string, record: UserListItem) => (
              <Button
                theme="borderless"
                data-testid={`user-link-${record.user_code}`}
                onClick={() => void openDetail(record.id)}
              >
                {value}
              </Button>
            )
          },
          { title: t('user.columns.userCode'), dataIndex: 'user_code' },
          {
            title: t('user.columns.agentCount'),
            dataIndex: 'agent_grant_count',
            render: (value: number) => <span className="count-cell">{value}</span>
          },
          {
            title: t('user.columns.credentialCount'),
            dataIndex: 'credential_count',
            render: (value: number) => <span className="count-cell">{value}</span>
          },
          {
            title: t('user.columns.identityCount'),
            dataIndex: 'identity_count',
            render: (value: number) => <span className="count-cell">{value}</span>
          },
          {
            title: t('user.columns.memoryCount'),
            dataIndex: 'memory_count',
            render: (value: number) => <span className="count-cell">{value}</span>
          },
          {
            title: t('user.columns.status'),
            dataIndex: 'status',
            render: (value: string) => (
              <Tag color={value === 'ACTIVE' ? 'green' : 'grey'}>
                {t(value === 'ACTIVE' ? 'common.status.enabled' : 'common.status.disabled')}
              </Tag>
            )
          },
          {
            title: t('user.columns.updateTime'),
            dataIndex: 'update_time',
            render: (value: string) => <DateTimeText value={value} />
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
      <UserFormModal
        visible={formVisible}
        user={formUser}
        onCancel={() => {
          setFormVisible(false);
          setFormUser(null);
        }}
        onSaved={(saved) => void onSaved(saved)}
      />
      {detail ? (
        <UserDetailTabs
          user={detail}
          visible
          onCancel={() => setDetail(null)}
          onEdit={() => {
            setFormUser(detail);
            setFormVisible(true);
          }}
        />
      ) : null}
      </PageSection>
    </>
  );
}
