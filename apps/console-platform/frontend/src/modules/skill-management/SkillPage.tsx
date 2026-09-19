import { Button, Input, Select, Switch, Tag, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { SkillDetailSideSheet } from './SkillDetailSideSheet';
import { SkillImportModal } from './SkillImportModal';
import { listSkills, updateSkill, type SkillListItem } from './services/skills';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', user_scope: '' };

export function SkillPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [items, setItems] = useState<SkillListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<SkillListItem | null>(null);
  const [importVisible, setImportVisible] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listSkills({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        user_scope: params.user_scope === '' ? undefined : (params.user_scope as 'ALL' | 'SELECTED')
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

  const copyKey = async (skill: SkillListItem): Promise<void> => {
    await navigator.clipboard.writeText(skill.key);
    Toast.success(t('skill.actions.keyCopied'));
  };

  const toggleEnabled = async (skill: SkillListItem): Promise<void> => {
    try {
      await updateSkill(skill.id, { enabled: !skill.enabled });
      await reload();
    } catch {
      // 错误由 ApiClient 展示
    }
  };

  return (
    <>
      <PageHeader title={t('skill.title')} description={t('skill.subtitle')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button theme="solid" data-testid="import-skill" onClick={() => setImportVisible(true)}>
              {t('skill.import.action')}
            </Button>
          }
          search={
            <>
              <Input
                value={params.keyword}
                placeholder={t('skill.searchPlaceholder')}
                style={{ width: 200 }}
                onChange={(value) => setParams((prev) => ({ ...prev, keyword: value, page: 1 }))}
              />
              <Select
                value={params.user_scope || undefined}
                style={{ width: 150 }}
                showClear
                placeholder={t('skill.columns.userScope')}
                optionList={[
                  { value: 'SELECTED', label: t('skill.scope.selected') },
                  { value: 'ALL', label: t('skill.scope.all') }
                ]}
                onChange={(value) =>
                  setParams((prev) => ({ ...prev, user_scope: value ? String(value) : '', page: 1 }))
                }
              />
              <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
            </>
          }
        />
        <RemoteTable<SkillListItem>
          rowKey="id"
          loading={loading}
          columns={[
            {
              title: t('skill.form.name'),
              dataIndex: 'name',
              render: (value: string, record: SkillListItem) => (
                <Button theme="borderless" data-testid={`skill-link-${record.key}`} onClick={() => setDetail(record)}>
                  {value}
                </Button>
              )
            },
            { title: t('skill.form.key'), dataIndex: 'key' },
            { title: t('skill.columns.currentVersion'), dataIndex: 'current_version' },
            {
              title: t('skill.columns.userScope'),
              dataIndex: 'user_scope',
              render: (value: SkillListItem['user_scope']) => (
                <Tag color={value === 'ALL' ? 'green' : 'blue'}>{t(`skill.scope.${value.toLowerCase()}`)}</Tag>
              )
            },
            { title: t('skill.columns.agentCount'), dataIndex: 'agent_count' },
            { title: t('skill.columns.userCount'), dataIndex: 'user_count' },
            {
              title: t('skill.columns.enabled'),
              dataIndex: 'enabled',
              render: (value: boolean, record: SkillListItem) => (
                <Switch size="small" checked={value} onChange={() => void toggleEnabled(record)} />
              )
            },
            {
              title: t('skill.columns.updateTime'),
              dataIndex: 'update_time',
              render: (value: string) => <DateTimeText value={value} />
            },
            {
              title: t('skill.columns.actions'),
              render: (_: unknown, record: SkillListItem) => (
                <>
                  <Button theme="borderless" onClick={() => void copyKey(record)}>
                    {t('skill.actions.copyKey')}
                  </Button>
                </>
              )
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
      </PageSection>
      <SkillImportModal
        visible={importVisible}
        onCancel={() => setImportVisible(false)}
        onSaved={() => {
          setImportVisible(false);
          void reload();
        }}
      />
      <SkillDetailSideSheet skill={detail} onCancel={() => setDetail(null)} onSkillMutated={() => void reload()} />
    </>
  );
}
