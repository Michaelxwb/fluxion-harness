import { Button, Spin, Tabs } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { getAgent, type AgentDetail } from './services/agents';

export interface AgentDetailSideSheetProps {
  agentId: string;
  reloadKey?: number;
  onClose(): void;
  onEdit(): void;
  onMutated?(): void;
  onRefreshDetail(): Promise<void>;
}

export function AgentDetailSideSheet(props: AgentDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    getAgent(props.agentId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [props.agentId, props.reloadKey]);

  return (
    <DetailSideSheet
      visible
      title={detail?.name ?? ''}
      subtitle={detail?.key ?? ''}
      onCancel={props.onClose}
      actions={
        <Button data-testid="edit-agent" theme="solid" onClick={() => props.onEdit()}>
          {t('agent.actions.edit')}
        </Button>
      }
    >
      <Tabs.TabPane itemKey="basic" tab={t('agent.detail.tabs.basic')}>
        {loading || detail === null ? (
          <Spin />
        ) : (
          <DetailGrid
            items={[
              { label: t('agent.form.key'), value: detail.key },
              { label: t('agent.form.name'), value: detail.name },
              { label: t('agent.form.model'), value: detail.model_name },
              { label: t('agent.detail.revision'), value: detail.revision },
              {
                label: t('agent.columns.enabled'),
                value: t(`common.status.${detail.enabled ? 'enabled' : 'disabled'}`)
              },
              { label: t('agent.detail.skillCount'), value: detail.skill_count },
              { label: t('agent.detail.mcpCount'), value: detail.mcp_count },
              { label: t('agent.detail.channelCount'), value: detail.channel_count },
              { label: t('agent.detail.userCount'), value: detail.user_count },
              { label: t('agent.form.instructions'), value: detail.instructions }
            ]}
          />
        )}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
