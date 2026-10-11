import { actionColumn } from '../../components/common/tableLayout';
import { Button, Input, Select, Switch, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ListActionButton } from '../../components/common/ListActionButton';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { EntityLink } from '../../components/common/EntityLink';
import { TableText } from '../../components/common/TableText';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { SkillDetailSideSheet } from './SkillDetailSideSheet';
import { CopyableText } from '../../components/common/CopyableText';
import { SkillScopeModal } from './SkillScopeModal';
import { SkillImportModal } from './SkillImportModal';
import { getSkill, listSkills, updateSkill, type SkillListItem } from './services/skills';

const DEFAULT_PARAMS = { page: 1, page_size: 15, keyword: '', user_scope: '' };

export function SkillPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [keywordInput, setKeywordInput] = useState('');
  const [items, setItems] = useState<SkillListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detail, setDetail] = useState<SkillListItem | null>(null);
  const [importVisible, setImportVisible] = useState(false);
  const [importTarget, setImportTarget] = useState<SkillListItem | null>(null);
  const [scopeTarget, setScopeTarget] = useState<SkillListItem | null>(null);
  const [detailReloadKey, setDetailReloadKey] = useState(0);
  const requestSeq = useRef(0);

  /** 关键字：回车才写入查询参数并复位第 1 页（值未变则不产生新 params，避免无谓重查）。 */
  const commitKeyword = (): void => {
    setParams((prev) =>
      prev.keyword === keywordInput ? prev : { ...prev, keyword: keywordInput, page: 1 }
    );
  };

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listSkills({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        user_scope: params.user_scope === '' ? undefined : (params.user_scope as 'ALL' | 'SELECTED')
      });
      if (current !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
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
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const openScope = async (skill: SkillListItem): Promise<void> => {
    try {
      setScopeTarget(await getSkill(skill.id));
    } catch {
      // ApiClient displays the request failure; keep the modal closed.
    }
  };

  const onMutated = (): void => {
    setDetailReloadKey((value) => value + 1);
    void reload();
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
                value={keywordInput}
                placeholder={t('skill.searchPlaceholder')}
                style={{ width: 200 }}
                onChange={setKeywordInput}
                onEnterPress={commitKeyword}
                showClear
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
              <ListActionButton onClick={() => void reload()} action="refresh" />
            </>
          }
        />
        <RemoteTable<SkillListItem>
          className="skill-table app-table-fit app-table-nowrap"
          scroll={{ x: '100%' }}
          rowKey="id"
          loading={loading}
          columns={[
            {
              title: t('skill.form.name'),
              dataIndex: 'name',
              width: 'auto',
              ellipsis: true,
              render: (value: string, record: SkillListItem) => (
                <EntityLink testId={`skill-link-${record.key}`} onClick={() => setDetail(record)}>
                  <TableText>{value}</TableText>
                </EntityLink>
              )
            },
            {
              title: t('skill.form.key'),
              dataIndex: 'key',
              width: 'auto',
              render: (value: string) => <CopyableText display={value} full={value} copyLabel={t('skill.actions.copyKey')} />
            },
            { title: t('skill.columns.currentVersion'), dataIndex: 'current_version', width: 116, ellipsis: true },
            {
              title: t('skill.columns.userScope'),
              dataIndex: 'user_scope',
              width: 128,
              render: (value: SkillListItem['user_scope']) => (
                <Tag color={value === 'ALL' ? 'green' : 'blue'}>{t(`skill.scope.${value.toLowerCase()}`)}</Tag>
              )
            },
            { title: t('skill.columns.agentCount'), dataIndex: 'agent_count', width: 108 },
            { title: t('skill.columns.userCount'), dataIndex: 'user_count', width: 108 },
            {
              title: t('skill.columns.enabled'),
              dataIndex: 'enabled',
              width: 74,
              render: (value: boolean, record: SkillListItem) => (
                <Switch size="small" checked={value} onChange={() => void toggleEnabled(record)} />
              )
            },
            {
              title: t('skill.columns.updateTime'),
              dataIndex: 'update_time',
              width: 176,
              render: (value: string) => <DateTimeText value={value} />
            },
            {
              ...actionColumn(132),
              title: t('skill.columns.actions'),
              render: (_: unknown, record: SkillListItem) => (
                <>
                  <Button theme="borderless" data-testid={`change-user-scope-${record.key}`} onClick={() => void openScope(record)}>
                    {t('skill.scope.changeAction')}
                  </Button>
                  <Button theme="borderless" data-testid={`import-artifact-${record.key}`} onClick={() => setImportTarget(record)}>
                    {t('skill.artifact.importNew')}
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
          empty={
            failed ? (
              <ErrorState onRetry={() => void reload()} />
            ) : (
              <EmptyState title={t('common.empty')} description={t('common.emptyHint')} />
            )
          }
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
      <SkillImportModal
        visible={importTarget !== null}
        skillId={importTarget?.id}
        onCancel={() => setImportTarget(null)}
        onSaved={() => {
          setImportTarget(null);
          onMutated();
        }}
      />
      <SkillScopeModal
        visible={scopeTarget !== null}
        skillId={scopeTarget?.id ?? null}
        currentScope={scopeTarget?.user_scope ?? 'SELECTED'}
        onCancel={() => setScopeTarget(null)}
        onSaved={() => {
          setScopeTarget(null);
          onMutated();
        }}
      />
      <SkillDetailSideSheet reloadKey={detailReloadKey} skill={detail} onCancel={() => setDetail(null)} onSkillMutated={() => void reload()} />
    </>
  );
}
