import { Banner, Tabs } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { Spin } from '@douyinfe/semi-ui';
import { getSkill, type SkillDetail, type SkillListItem } from './services/skills';

export interface SkillDetailSideSheetProps {
  skill: SkillListItem | null;
  onCancel(): void;
  children?: (detail: SkillDetail | null) => React.ReactNode;
}

export function SkillDetailSideSheet(props: SkillDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!props.skill) {
      setDetail(null);
      return;
    }
    setLoading(true);
    getSkill(props.skill.id)
      .then(setDetail)
      .catch(() => {
        // 错误由 ApiClient 展示；详情保持空态
        setDetail(null);
      })
      .finally(() => setLoading(false));
  }, [props.skill]);

  if (!props.skill) {
    return null;
  }

  return (
    <DetailSideSheet
      visible={props.skill !== null}
      title={props.skill.name}
      subtitle={props.skill.key}
      onCancel={props.onCancel}
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
                { label: t('skill.columns.userScope'), value: t(`skill.scope.${detail.user_scope.toLowerCase()}`) },
                { label: t('skill.columns.enabled'), value: t(`common.status.${detail.enabled ? 'enabled' : 'disabled'}`) },
                { label: t('skill.detail.agentCount'), value: detail.agent_count },
                { label: t('skill.detail.userCount'), value: detail.user_count }
              ]}
            />
          </>
        )}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
