import { Banner, Button, Popconfirm, Select, Table } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { listUsers, type Page, type UserListItem } from '../user-identity/services/users';
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

export function McpSelectedUserTable(props: McpSelectedUserTableProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<McpGrantItem[]>([]);
  const [candidates, setCandidates] = useState<Array<{ value: string; label: string }>>([]);
  const [selectedUser, setSelectedUser] = useState<string | undefined>(undefined);
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listSelectedUsers(props.serverId, { page: 1, page_size: 100 });
      setItems(page.items);
    } finally {
      setLoading(false);
    }
  }, [props.serverId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    listUsers({ page: 1, page_size: 100, keyword: '' })
      .then((page: Page<UserListItem>) =>
        setCandidates(
          page.items.map((user) => ({ value: user.id, label: `${user.display_name}（${user.user_code}）` }))
        )
      )
      .catch(() => {
        // 候选列表加载失败由 ApiClient 提示
      });
  }, []);

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
      await reload();
      props.onChanged?.();
    } catch {
      // [E-07] 失败不先本地删行
    }
  };

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <Select
          data-testid="mcp-grant-user-select"
          style={{ width: 280 }}
          showClear
          filter
          placeholder={t('mcp.users.pickPlaceholder')}
          optionList={candidates}
          value={selectedUser}
          onChange={(value) => setSelectedUser(value ? String(value) : undefined)}
        />
        <Button theme="solid" loading={adding} data-testid="mcp-add-selected-user" onClick={() => void add()}>
          {t('mcp.users.add')}
        </Button>
      </div>
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
              <Popconfirm title={t('mcp.users.confirmRemove')} onConfirm={() => void remove(record.user_id)}>
                <Button theme="borderless" type="danger">
                  {t('mcp.users.remove')}
                </Button>
              </Popconfirm>
            )
          }
        ]}
      />
    </>
  );
}
