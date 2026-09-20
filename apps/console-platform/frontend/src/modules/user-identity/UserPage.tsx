import { Button, Input, Select, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { EmptyState } from '../../components/common/EmptyState';
import { EntityLink } from '../../components/common/EntityLink';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { StatusTag } from '../../components/common/StatusTag';
import { UserDetailTabs } from './UserDetailTabs';
import { UserFormModal } from './UserFormModal';
import { getUser, listUsers, type UserBasic, type UserDetail, type UserListItem } from './services/users';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', status: '' };
const EMPTY_FILTERS = { keyword: '', status: '' };

export function UserPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [items, setItems] = useState<UserListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [formUser, setFormUser] = useState<UserDetail | null>(null);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const page = await listUsers({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        status: params.status || undefined
      });
      if (seq !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
    } catch {
      if (seq !== requestSeq.current) {
        return;
      }
      setFailed(true);
      setItems([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const applyFilters = (): void => {
    setParams((prev) => ({ ...prev, keyword: filters.keyword, status: filters.status, page: 1 }));
  };

  const resetFilters = (): void => {
    setFilters(EMPTY_FILTERS);
    setParams(DEFAULT_PARAMS);
  };

  const openDetail = async (id: string): Promise<void> => {
    try {
      setDetail(await getUser(id));
    } catch {
      Toast.error(t('common.loadFailed'));
    }
  };

  const onSaved = async (saved: UserBasic): Promise<void> => {
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
              value={filters.keyword}
              placeholder={t('user.searchPlaceholder')}
              style={{ width: 220 }}
              onChange={(value) => setFilters((prev) => ({ ...prev, keyword: value }))}
              onEnterPress={applyFilters}
            />
            <Select
              value={filters.status || undefined}
              style={{ width: 150 }}
              placeholder={t('user.columns.status')}
              showClear
              optionList={[
                { value: 'ACTIVE', label: t('common.status.enabled') },
                { value: 'DISABLED', label: t('common.status.disabled') }
              ]}
              onChange={(value) => setFilters((prev) => ({ ...prev, status: value ? String(value) : '' }))}
            />
            <Button data-testid="search-user" onClick={applyFilters}>
              {t('common.search')}
            </Button>
            <Button data-testid="reset-user" onClick={resetFilters}>
              {t('user.reset')}
            </Button>
            <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
          </>
        }
      />
      {failed ? (
        <ErrorState onRetry={() => void reload()} />
      ) : (
        <RemoteTable<UserListItem>
          rowKey="id"
          loading={loading}
          columns={[
            {
              title: t('user.columns.displayName'),
              dataIndex: 'display_name',
              render: (value: string, record: UserListItem) => (
                <EntityLink
                  testId={`user-link-${record.user_code}`}
                  onClick={() => void openDetail(record.id)}
                >
                  {value}
                </EntityLink>
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
      )}
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
