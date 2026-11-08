import { actionColumn } from '../../components/common/tableLayout';
import { Button, Input, Select, Switch, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { ListActionButton } from '../../components/common/ListActionButton';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { EntityLink } from '../../components/common/EntityLink';
import { TableText } from '../../components/common/TableText';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { Tips } from '../../components/common/Tips';
import { SkillDeleteConfirm } from './SkillDeleteConfirm';
import { SkillDetailSideSheet } from './SkillDetailSideSheet';
import { SkillPlatforms } from './SkillPlatforms';
import { CopyableText } from '../../components/common/CopyableText';
import { SkillReviewStatusFilter } from './SkillReviewStatusFilter';
import { SkillReviewStatusTag } from './SkillReviewStatusTag';
import { SkillScopeModal } from './SkillScopeModal';
import { SkillSourceFilter } from './SkillSourceFilter';
import { SkillSourceTag } from './SkillSourceTag';
import { SkillImportModal } from './SkillImportModal';
import {
  deleteSkill,
  getSkill,
  listAllSkillAgents,
  listSkills,
  updateSkill,
  type SkillAgentItem,
  type SkillListItem,
  type SkillReviewStatus,
  type SkillSource
} from './services/skills';

type UserScopeFilter = '' | 'ALL' | 'SELECTED';

interface SkillListFilters {
  page: number;
  page_size: number;
  keyword: string;
  user_scope: UserScopeFilter;
  source: '' | SkillSource;
  review_status: '' | SkillReviewStatus;
}

const DEFAULT_PARAMS: SkillListFilters = {
  page: 1,
  page_size: 15,
  keyword: '',
  user_scope: '',
  source: '',
  review_status: ''
};

export function SkillPage() {
  const { t } = useTranslation();
  const { account } = useAuth();
  const canManage = account?.role === 'ADMIN';
  const [params, setParams] = useState<SkillListFilters>(DEFAULT_PARAMS);
  const [keywordInput, setKeywordInput] = useState('');
  const [items, setItems] = useState<SkillListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detail, setDetail] = useState<SkillListItem | null>(null);
  const [detailTab, setDetailTab] = useState('basic');
  const [importVisible, setImportVisible] = useState(false);
  const [importTarget, setImportTarget] = useState<SkillListItem | null>(null);
  const [scopeTarget, setScopeTarget] = useState<SkillListItem | null>(null);
  const [detailReloadKey, setDetailReloadKey] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<SkillListItem | null>(null);
  const [deleteAgents, setDeleteAgents] = useState<SkillAgentItem[]>([]);
  const [deleteAgentsLoading, setDeleteAgentsLoading] = useState(false);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteAgentsError, setDeleteAgentsError] = useState<string | null>(null);
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
        user_scope: params.user_scope || undefined,
        source: params.source || undefined,
        review_status: params.review_status || undefined
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
      setScopeTarget(await getSkill(skill.id, { errorTip: 'always' }));
    } catch {
      // ApiClient displays the request failure; keep the modal closed.
    }
  };

  const openReview = (skill: SkillListItem): void => {
    setDetailTab('review');
    setDetail(skill);
  };

  const onMutated = (): void => {
    setDetailReloadKey((value) => value + 1);
    void reload();
  };

  const openDelete = async (skill: SkillListItem): Promise<void> => {
    setDeleteTarget(skill);
    setDeleteAgents([]);
    setDeleteError(null);
    setDeleteAgentsError(null);
    setDeleteAgentsLoading(true);
    try {
      setDeleteAgents(await listAllSkillAgents(skill.id));
    } catch (failure) {
      const body = apiErrorBody(failure);
      setDeleteAgentsError(body?.msg ?? t('skill.delete.agentsFailed'));
    } finally {
      setDeleteAgentsLoading(false);
    }
  };

  const confirmDelete = async (): Promise<void> => {
    if (deleteTarget === null) {
      return;
    }
    setDeleteLoading(true);
    setDeleteError(null);
    try {
      const result = await deleteSkill(deleteTarget.id, { unbind: true, confirm: true });
      const unbound = 'unbound_agents' in result ? result.unbound_agents.length : 0;
      Tips.success(t('skill.delete.success', { count: unbound }));
      if (detail?.id === deleteTarget.id) {
        setDetail(null);
        setDetailTab('basic');
      }
      setDeleteTarget(null);
      setDeleteAgents([]);
      if (items.length === 1 && params.page > 1) {
        setParams((prev) => ({ ...prev, page: prev.page - 1 }));
      } else {
        await reload();
      }
    } catch (failure) {
      const body = apiErrorBody(failure);
      setDeleteError(body?.msg ?? t('skill.delete.failed'));
    } finally {
      setDeleteLoading(false);
    }
  };

  const toggleEnabled = async (skill: SkillListItem): Promise<void> => {
    try {
      await updateSkill(skill.id, { enabled: !skill.enabled });
      await reload();
    } catch {
      // 错误由 ApiClient 的统一 Tips 展示
    }
  };

  const hasFilters = Boolean(
    params.keyword || params.user_scope || params.source || params.review_status
  );

  const clearFilters = (): void => {
    setKeywordInput('');
    setParams({ ...DEFAULT_PARAMS });
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
              <SkillSourceFilter
                value={params.source}
                onChange={(value) => setParams((prev) => ({ ...prev, source: value, page: 1 }))}
              />
              <SkillReviewStatusFilter
                value={params.review_status}
                onChange={(value) =>
                  setParams((prev) => ({ ...prev, review_status: value, page: 1 }))
                }
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
                  setParams((prev) => ({
                    ...prev,
                    user_scope: (value ? String(value) : '') as UserScopeFilter,
                    page: 1
                  }))
                }
              />
              <ListActionButton onClick={() => void reload()} action="refresh" />
            </>
          }
        />
        <RemoteTable<SkillListItem>
          className="skill-table app-table-fit app-table-nowrap"
          scroll={{ x: 1500 }}
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
              title: t('skill.columns.source'),
              dataIndex: 'source',
              width: 100,
              render: (value: SkillSource) => <SkillSourceTag source={value} />
            },
            {
              title: t('skill.columns.reviewStatus'),
              dataIndex: 'review_status',
              width: 118,
              render: (value: SkillListItem['review_status']) => (
                <SkillReviewStatusTag status={value} pendingBadge />
              )
            },
            {
              title: t('skill.columns.author'),
              dataIndex: 'author',
              width: 110,
              ellipsis: true,
              render: (value: string | null) => <TableText>{value ?? '-'}</TableText>
            },
            {
              title: t('skill.columns.platforms'),
              dataIndex: 'platforms',
              width: 'auto',
              render: (platforms: string[]) => <SkillPlatforms platforms={platforms} />
            },
            {
              title: t('skill.columns.userScope'),
              dataIndex: 'user_scope',
              width: 116,
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
              ...actionColumn(230),
              title: t('skill.columns.actions'),
              render: (_: unknown, record: SkillListItem) => (
                <>
                  {canManage && record.review_status === 'PENDING' ? (
                    <Button
                      theme="borderless"
                      data-testid={`approve-skill-${record.key}`}
                      onClick={() => openReview(record)}
                    >
                      {t('skill.review.tab')}
                    </Button>
                  ) : null}
                  <Button theme="borderless" data-testid={`change-user-scope-${record.key}`} onClick={() => void openScope(record)}>
                    {t('skill.scope.changeAction')}
                  </Button>
                  <Button theme="borderless" data-testid={`import-artifact-${record.key}`} onClick={() => setImportTarget(record)}>
                    {t('skill.artifact.importNew')}
                  </Button>
                  {canManage ? (
                    <Button
                      type="danger"
                      theme="borderless"
                      data-testid={`delete-skill-${record.key}`}
                      onClick={() => void openDelete(record)}
                    >
                      {t('skill.actions.delete')}
                    </Button>
                  ) : null}
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
            ) : hasFilters ? (
              <EmptyState
                title={t('skill.empty.filtered')}
                description={t('skill.empty.nextStep')}
                action={
                  <Button theme="borderless" data-testid="clear-skill-filters" onClick={clearFilters}>
                    {t('skill.empty.clearFilters')}
                  </Button>
                }
              />
            ) : (
              <EmptyState
                title={t('common.empty')}
                description={t('skill.empty.nextStep')}
                action={
                  <Button theme="borderless" data-testid="empty-import-skill" onClick={() => setImportVisible(true)}>
                    {t('skill.empty.importAction')}
                  </Button>
                }
              />
            )
          }
        />
      </PageSection>
      <SkillImportModal
        visible={importVisible}
        canManageSource={canManage}
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
      {deleteTarget !== null ? (
        <SkillDeleteConfirm
          skill={deleteTarget}
          agents={deleteAgents}
          visible
          confirmLoading={deleteLoading || deleteAgentsLoading}
          agentsLoading={deleteAgentsLoading}
          confirmDisabled={deleteAgentsError !== null}
          error={deleteError ?? deleteAgentsError}
          onConfirm={() => void confirmDelete()}
          onCancel={() => {
            if (!deleteLoading) {
              setDeleteTarget(null);
              setDeleteAgents([]);
              setDeleteError(null);
              setDeleteAgentsError(null);
            }
          }}
        />
      ) : null}
      <SkillDetailSideSheet
        reloadKey={detailReloadKey}
        skill={detail}
        canManage={canManage}
        initialTab={detailTab}
        onCancel={() => {
          setDetail(null);
          setDetailTab('basic');
        }}
        onSkillMutated={() => void reload()}
      />
    </>
  );
}
