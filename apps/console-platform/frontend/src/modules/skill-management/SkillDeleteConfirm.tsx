import { Modal, Spin } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { SkillAgentItem, SkillListItem } from './services/skills';

export interface SkillDeleteConfirmProps {
  skill: SkillListItem;
  agents: SkillAgentItem[];
  visible: boolean;
  confirmLoading?: boolean;
  agentsLoading?: boolean;
  /** Agent 列表加载失败时禁止确认，避免看不到影响范围就删除；删除失败可重试。 */
  confirmDisabled?: boolean;
  error?: string | null;
  onConfirm(): void;
  onCancel(): void;
}

/**
 * 解绑 + 软删二次确认：展示完整影响范围（将解绑的 Agent 列表），确认按钮明确
 * “解绑并删除”。打开弹窗只读 Agent 列表，不触发删除 API（API-07 在 Page 确认后调用）。
 */
export function SkillDeleteConfirm(props: SkillDeleteConfirmProps) {
  const { t } = useTranslation();
  const loading = props.confirmLoading === true;
  return (
    <Modal
      visible={props.visible}
      title={t('skill.delete.title')}
      okText={loading ? t('skill.delete.confirmLoading') : t('skill.delete.unbindAndDelete')}
      cancelText={t('common.cancel')}
      confirmLoading={loading}
      okButtonProps={{
        type: 'danger',
        disabled: props.confirmDisabled === true || loading
      }}
      onOk={props.onConfirm}
      onCancel={props.onCancel}
      width={560}
    >
      <div data-testid="skill-delete-confirm">
        <p>
          {t('skill.delete.description', { name: props.skill.name, key: props.skill.key })}
        </p>
        <p>{t('skill.delete.impact', { count: props.agents.length })}</p>
        {props.agentsLoading ? (
          <div className="skill-delete-agents-loading" data-testid="skill-delete-agents-loading">
            <Spin size="small" /> {t('skill.delete.loadingAgents')}
          </div>
        ) : props.agents.length === 0 ? (
          <div className="detail-hint">{t('skill.delete.onlySoftDelete')}</div>
        ) : (
          <ul className="detail-list" data-testid="skill-delete-agent-list">
            {props.agents.map((agent) => (
              <li key={agent.agent_id}>
                {agent.name}（{agent.key}）
              </li>
            ))}
          </ul>
        )}
        {!props.agentsLoading && props.agents.length === 0 ? (
          <div className="detail-hint" data-testid="skill-delete-agents-empty">
            {t('skill.delete.agentsEmpty')}
          </div>
        ) : null}
        {props.error ? (
          <div className="skill-action-error" role="alert" data-testid="skill-delete-error">
            {props.error}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
