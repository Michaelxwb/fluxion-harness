import { Banner, Button, Select, Table } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { listUsers, type UserListItem } from '../user-identity/services/users';
import {
  addSelectedUser,
  listSelectedUsers,
  removeSelectedUser,
  type McpGrantItem
} from './services/mcpServers';

export interface McpSelectedUserTableProps {
  serverId: string;
  userScope: 'ALL' | 'SELECTED';
  onChanged?(): void;
}

const PAGE_SIZE = 10;
const CANDIDATE_PAGE_SIZE = 20;

export function McpSelectedUserTable(props: McpSelectedUserTableProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<McpGrantItem[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [candidates, setCandidates] = useState<Array<{ value: string; label: string }>>([]);
  const [selectedUser, setSelectedUser] = useState<string | undefined>(undefined);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const result = await listSelectedUsers(props.serverId, { page, page_size: PAGE_SIZE });
      if (current !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setTotal(result.total);
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
  }, [page, props.serverId]);

  useEffect(() => {
    if (props.userScope === 'ALL') {
      setItems([]);
      setTotal(0);
      setFailed(false);
      return;
    }
    void reload();
  }, [props.userScope, reload]);

  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    if (props.userScope === 'ALL') {
      return;
    }
    let cancelled = false;
    listUsers({ page: 1, page_size: CANDIDATE_PAGE_SIZE, keyword: search || undefined })
      .then((result) => {
        if (!cancelled) {
          setCandidates(
            result.items.map((user: UserListItem) => ({
              value: user.id,
              label: `${user.display_name}（${user.user_code}）`
            }))
          );
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCandidates([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [props.userScope, search]);

  if (props.userScope === 'ALL') {
    return <Banner type="info" closeIcon={null} description={t('mcp.users.allScopeHint')} />;
  }

  const add = async (): Promise<void> => {
    if (!selectedUser) {
      return;
    }
    setAdding(true);
    try {
      await addSelectedUser(props.serverId, selectedUser);
      setSelectedUser(undefined);
      await reload();
      props.onChanged?.();
    } catch {
      // [E-07] 失败保持当前 Tab，Toast 由 ApiClient；不本地删行/加行
    } finally {
      setAdding(false);
    }
  };

  const remove = async (userId: string): Promise<void> => {
    try {
      await removeSelectedUser(props.serverId, userId);
    } catch {
      // [E-07] 失败不先本地删行
      return;
    }
    await reload();
    props.onChanged?.();
  };

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <Select
          data-testid="mcp-grant-user-select"
          style={{ width: 280 }}
          showClear
          remote
          filter
          placeholder={t('mcp.users.pickPlaceholder')}
          optionList={candidates}
          value={selectedUser}
          onSearch={setSearchInput}
          onChange={(value) => setSelectedUser(value ? String(value) : undefined)}
        />
        <Button theme="solid" loading={adding} data-testid="mcp-add-selected-user" onClick={() => void add()}>
          {t('mcp.users.add')}
        </Button>
      </div>
      {failed ? (
        <ErrorState onRetry={() => void reload()} />
      ) : (
        <div data-testid="mcp-selected-users">
          <Table<McpGrantItem>
            rowKey="user_id"
            loading={loading}
            pagination={false}
            dataSource={items}
            empty={<EmptyState title={t('common.empty')} description={t('mcp.users.emptyHint')} />}
            columns={[
              { title: t('mcp.users.userCode'), dataIndex: 'user_code' },
              { title: t('mcp.users.displayName'), dataIndex: 'display_name' },
              {
                title: t('mcp.users.grantedAt'),
                dataIndex: 'create_time',
                render: (value: string) => <DateTimeText value={value} />
              },
              {
                title: t('mcp.columns.actions'),
                render: (_: unknown, record: McpGrantItem) => (
                  <ConfirmAction
                    danger
                    title={t('mcp.users.confirmRemove')}
                    onConfirm={() => void remove(record.user_id)}
                  >
                    {t('mcp.users.remove')}
                  </ConfirmAction>
                )
              }
            ]}
          />
          <PaginationFooter page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </div>
      )}
    </>
  );
}
