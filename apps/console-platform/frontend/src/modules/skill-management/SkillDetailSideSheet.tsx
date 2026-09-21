import { Banner, Button, Spin, Table, Tabs, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import {
  getSkill,
  listArtifacts,
  listSkillAgents,
  type SkillAgentItem,
  type SkillArtifactDetail,
  type SkillDetail,
  type SkillListItem
} from './services/skills';
import { SelectedUserTable } from './SelectedUserTable';
import { SkillScopeModal } from './SkillScopeModal';
import { SkillArtifactDetailModal } from './SkillArtifactDetailModal';
import { SkillImportModal } from './SkillImportModal';

export interface SkillDetailSideSheetProps {
  skill: SkillListItem | null;
  onCancel(): void;
  onSkillMutated?(): void;
}

const DETAIL_PAGE_SIZE = 10;

function SkillAgentsTable(props: { skillId: string }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<SkillAgentItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DETAIL_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listSkillAgents(props.skillId, { page, page_size: pageSize });
      setItems(result.items);
      setTotal(result.total);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, props.skillId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (failed) {
    return <ErrorState onRetry={() => void reload()} />;
  }

  return (
    <div data-testid="skill-agents">
      <Table<SkillAgentItem>
        rowKey="agent_id"
        loading={loading}
        pagination={false}
        dataSource={items}
        empty={<EmptyState title={t('skill.agents.empty')} />}
        columns={[
          { title: t('skill.agents.agentName'), dataIndex: 'name' },
          { title: t('skill.agents.agentKey'), dataIndex: 'key' },
          {
            title: t('skill.columns.enabled'),
            dataIndex: 'enabled',
            render: (value: boolean) => (
              <Tag color={value ? 'green' : 'grey'}>
                {t(value ? 'common.status.enabled' : 'common.status.disabled')}
              </Tag>
            )
          },
          { title: t('skill.agents.sortOrder'), dataIndex: 'sort_order' },
          {
            title: t('skill.agents.boundAt'),
            dataIndex: 'create_time',
            render: (value: string) => <DateTimeText value={value} />
          }
        ]}
      />
      <PaginationFooter
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
      />
    </div>
  );
}

export function SkillDetailSideSheet(props: SkillDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [artifacts, setArtifacts] = useState<SkillArtifactDetail[]>([]);
  const [artifactPage, setArtifactPage] = useState(1);
  const [artifactTotal, setArtifactTotal] = useState(0);
  const [artifactDetailId, setArtifactDetailId] = useState<string | null>(null);
  const [importVisible, setImportVisible] = useState(false);
  const [scopeVisible, setScopeVisible] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');

  const reload = useCallback(async () => {
    if (!props.skill) {
      setDetail(null);
      setArtifacts([]);
      setFailed(false);
      return;
    }
    setLoading(true);
    try {
      const [loaded, page] = await Promise.all([
        getSkill(props.skill.id),
        listArtifacts(props.skill.id, { page: artifactPage, page_size: DETAIL_PAGE_SIZE })
      ]);
      setDetail(loaded);
      setArtifacts(page.items);
      setArtifactTotal(page.total);
      setFailed(false);
    } catch {
      setDetail(null);
      setArtifacts([]);
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [props.skill, artifactPage]);

  useEffect(() => {
    setActiveTab('basic');
    setArtifactPage(1);
  }, [props.skill?.id]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!props.skill) {
    return null;
  }

  const currentVersion = detail?.current_version ?? props.skill.current_version;

  return (
    <>
      <DetailSideSheet
        visible
        title={props.skill.name}
        subtitle={currentVersion ? `${props.skill.key} · v${currentVersion}` : props.skill.key}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onCancel={props.onCancel}
        actions={
          <>
            <Button
              data-testid="change-user-scope"
              disabled={detail === null}
              onClick={() => setScopeVisible(true)}
            >
              {t('skill.scope.changeAction')}
            </Button>
            <Button
              theme="solid"
              data-testid="import-artifact"
              onClick={() => setImportVisible(true)}
            >
              {t('skill.artifact.importNew')}
            </Button>
          </>
        }
      >
        <Tabs.TabPane itemKey="basic" tab={t('skill.detail.tabs.basic')}>
          {failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : loading ? (
            <Spin />
          ) : detail === null ? (
            <EmptyState title={t('common.empty')} />
          ) : (
            <>
              <Banner
                type="info"
                closeIcon={null}
                description={t(
                  detail.user_scope === 'ALL'
                    ? 'skill.detail.scopeAllNotice'
                    : 'skill.detail.scopeSelectedNotice'
                )}
              />
              <DetailGrid
                items={[
                  { label: t('skill.form.key'), value: detail.key },
                  { label: t('skill.form.name'), value: detail.name },
                  { label: t('skill.detail.description'), value: detail.description },
                  { label: t('skill.columns.currentVersion'), value: detail.current_version ?? '-' },
                  { label: t('skill.columns.executionMode'), value: detail.execution_mode ?? '-' },
                  {
                    label: t('skill.columns.userScope'),
                    value: t(`skill.scope.${detail.user_scope.toLowerCase()}`)
                  },
                  {
                    label: t('skill.columns.enabled'),
                    value: t(`common.status.${detail.enabled ? 'enabled' : 'disabled'}`)
                  },
                  { label: t('skill.detail.agentCount'), value: detail.agent_count },
                  { label: t('skill.detail.userCount'), value: detail.user_count }
                ]}
              />
              {detail.current_artifact?.instructions ? (
                <>
                  <div className="detail-section-title">{t('skill.detail.skillMd')}</div>
                  <pre
                    data-testid="skill-md-preview"
                    style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto' }}
                  >
                    {detail.current_artifact.instructions}
                  </pre>
                </>
              ) : null}
            </>
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="artifacts" tab={t('skill.detail.tabs.artifacts')}>
          {failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : artifacts.length === 0 ? (
            <EmptyState title={t('common.empty')} description={t('skill.artifact.emptyHint')} />
          ) : (
            <>
              <ul style={{ listStyle: 'none', padding: 0 }} data-testid="artifact-list">
                {artifacts.map((artifact) => (
                  <li
                    key={artifact.artifact_id}
                    style={{ padding: '8px 0', borderBottom: '1px solid var(--semi-color-border)' }}
                  >
                    <Button
                      theme="borderless"
                      data-testid={`artifact-link-${artifact.version}`}
                      onClick={() => setArtifactDetailId(artifact.artifact_id)}
                    >
                      {artifact.version}
                    </Button>
                    {artifact.version === detail?.current_version ? (
                      <span style={{ marginLeft: 8, color: 'var(--semi-color-primary)' }}>
                        {t('skill.artifact.current')}
                      </span>
                    ) : null}
                    <span style={{ marginLeft: 16, color: 'var(--semi-color-text-2)' }}>
                      <DateTimeText value={artifact.create_time} />
                    </span>
                  </li>
                ))}
              </ul>
              <PaginationFooter
                page={artifactPage}
                pageSize={DETAIL_PAGE_SIZE}
                total={artifactTotal}
                onPageChange={setArtifactPage}
              />
            </>
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="agents" tab={t('skill.detail.tabs.agents')}>
          <SkillAgentsTable skillId={props.skill.id} />
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="users" tab={t('skill.detail.tabs.users')}>
          {failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : detail === null ? (
            <Spin />
          ) : (
            <SelectedUserTable
              skillId={props.skill.id}
              userScope={detail.user_scope}
              onChanged={() => void reload()}
            />
          )}
        </Tabs.TabPane>
      </DetailSideSheet>
      <SkillArtifactDetailModal
        visible={artifactDetailId !== null}
        skillId={props.skill.id}
        artifactId={artifactDetailId}
        onCancel={() => setArtifactDetailId(null)}
      />
      <SkillScopeModal
        visible={scopeVisible}
        skillId={props.skill.id}
        currentScope={detail?.user_scope ?? 'SELECTED'}
        onCancel={() => setScopeVisible(false)}
        onSaved={() => {
          setScopeVisible(false);
          void reload();
          props.onSkillMutated?.();
        }}
      />
      <SkillImportModal
        visible={importVisible}
        skillId={props.skill.id}
        onCancel={() => setImportVisible(false)}
        onSaved={() => {
          setImportVisible(false);
          setActiveTab('artifacts');
          setArtifactPage(1);
          void reload();
          props.onSkillMutated?.();
        }}
      />
    </>
  );
}
