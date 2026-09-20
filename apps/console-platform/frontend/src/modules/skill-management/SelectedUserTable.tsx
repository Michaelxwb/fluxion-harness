import { Banner, Button, Select, Table, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { listUsers, type Page, type UserListItem } from '../user-identity/services/users';
import {
  addSelectedUser,
  listSelectedUsers,
  removeSelectedUser,
  type SkillGrantItem
} from './services/skills';

export interface SelectedUserTableProps {
  skillId: string;
  userScope: 'ALL' | 'SELECTED';
  onChanged?(): void;
}

export function SelectedUserTable(props: SelectedUserTableProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<SkillGrantItem[]>([]);
  const [candidates, setCandidates] = useState<Array<{ value: string; label: string }>>([]);
  const [selectedUser, setSelectedUser] = useState<string | undefined>(undefined);
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listSelectedUsers(props.skillId, { page: 1, page_size: 100 });
      setItems(page.items);
    } finally {
      setLoading(false);
    }
  }, [props.skillId]);

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
    return (
      <Banner
        type="info"
        closeIcon={null}
        description={t('skill.users.allScopeHint')}
      />
    );
  }

  const add = async (): Promise<void> => {
    if (!selectedUser) {
      return;
    }
    setAdding(true);
    try {
      await addSelectedUser(props.skillId, selectedUser);
      setSelectedUser(undefined);
      await reload();
      props.onChanged?.();
    } catch {
      // 失败保持当前 Tab，Toast 由 ApiClient 展示（E-06）
    } finally {
      setAdding(false);
    }
  };

  const remove = async (userId: string): Promise<void> => {
    await removeSelectedUser(props.skillId, userId);
    await reload();
    props.onChanged?.();
  };

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <Select
          data-testid="grant-user-select"
          style={{ width: 280 }}
          showClear
          filter
          placeholder={t('skill.users.pickPlaceholder')}
          optionList={candidates}
          value={selectedUser}
          onChange={(value) => setSelectedUser(value ? String(value) : undefined)}
        />
        <Button theme="solid" loading={adding} data-testid="add-selected-user" onClick={() => void add()}>
          {t('skill.users.add')}
        </Button>
      </div>
      <Table<SkillGrantItem>
        rowKey="user_id"
        loading={loading}
        pagination={false}
        dataSource={items}
        empty={<EmptyState title={t('common.empty')} description={t('skill.users.emptyHint')} />}
        columns={[
          { title: t('skill.users.userCode'), dataIndex: 'user_code' },
          { title: t('skill.users.displayName'), dataIndex: 'display_name' },
          {
            title: t('skill.users.grantedAt'),
            dataIndex: 'create_time',
            render: (value: string) => <DateTimeText value={value} />
          },
          {
            title: t('skill.columns.actions'),
            render: (_: unknown, record: SkillGrantItem) => (
              <ConfirmAction danger title={t('skill.users.confirmRemove')} onConfirm={() => void remove(record.user_id)}>
                {t('skill.users.remove')}
              </ConfirmAction>
            )
          }
        ]}
      />
    </>
  );
}
