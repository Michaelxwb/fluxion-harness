import { Banner, Button, Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { getSkill, listArtifacts, type SkillArtifactDetail, type SkillListItem } from './services/skills';
import { SkillArtifactDetailModal } from './SkillArtifactDetailModal';
import { SkillImportModal } from './SkillImportModal';

export interface SkillDetailSideSheetProps {
  skill: SkillListItem | null;
  onCancel(): void;
  onSkillMutated?(): void;
}

export function SkillDetailSideSheet(props: SkillDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<SkillListItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [artifacts, setArtifacts] = useState<SkillArtifactDetail[]>([]);
  const [artifactDetailId, setArtifactDetailId] = useState<string | null>(null);
  const [importVisible, setImportVisible] = useState(false);

  const reload = useCallback(async () => {
    if (!props.skill) {
      setDetail(null);
      setArtifacts([]);
      return;
    }
    setLoading(true);
    try {
      const [loaded, page] = await Promise.all([
        getSkill(props.skill.id),
        listArtifacts(props.skill.id, { page: 1, page_size: 20 })
      ]);
      setDetail(loaded);
      setArtifacts(page.items);
    } catch {
      // 错误由 ApiClient 展示；详情保持空态
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, [props.skill]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!props.skill) {
    return null;
  }

  return (
    <>
      <DetailSideSheet
        visible
        title={props.skill.name}
        subtitle={props.skill.key}
        onCancel={props.onCancel}
        actions={
          <>
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
          {loading ? (
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
            </>
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="artifacts" tab={t('skill.detail.tabs.artifacts')}>
          {artifacts.length === 0 ? (
            <EmptyState title={t('common.empty')} description={t('skill.artifact.emptyHint')} />
          ) : (
            <ul style={{ listStyle: 'none', padding: 0 }} data-testid="artifact-list">
              {artifacts.map((artifact) => (
                <li key={artifact.artifact_id} style={{ padding: '8px 0', borderBottom: '1px solid var(--semi-color-border)' }}>
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
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="agents" tab={t('skill.detail.tabs.agents')}>
          <Banner
            type="info"
            closeIcon={null}
            description={t('skill.detail.agentCountNotice', { count: detail?.agent_count ?? 0 })}
          />
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="users" tab={t('skill.detail.tabs.users')}>
          <Banner
            type="info"
            closeIcon={null}
            description={t('skill.detail.userCountNotice', { count: detail?.user_count ?? 0 })}
          />
        </Tabs.TabPane>
      </DetailSideSheet>
      <SkillArtifactDetailModal
        visible={artifactDetailId !== null}
        skillId={props.skill.id}
        artifactId={artifactDetailId}
        onCancel={() => setArtifactDetailId(null)}
      />
      <SkillImportModal
        visible={importVisible}
        skillId={props.skill.id}
        currentVersion={detail?.current_version ?? null}
        onCancel={() => setImportVisible(false)}
        onSaved={() => {
          setImportVisible(false);
          void reload();
          props.onSkillMutated?.();
        }}
      />
    </>
  );
}
