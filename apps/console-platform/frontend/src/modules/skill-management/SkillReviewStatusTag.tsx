import { Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { SkillReviewStatus } from './services/skills';

const STATUS_COLORS: Record<SkillReviewStatus, 'orange' | 'green' | 'red' | 'grey'> = {
  PENDING: 'orange',
  APPROVED: 'green',
  REJECTED: 'red',
  SUPERSEDED: 'grey'
};

export interface SkillReviewStatusTagProps {
  status: SkillReviewStatus | null;
  /** 列表对 PENDING 使用显式待审文字；详情沿用同一状态文案。 */
  pendingBadge?: boolean;
}

export function SkillReviewStatusTag({ status, pendingBadge = false }: SkillReviewStatusTagProps) {
  const { t } = useTranslation();
  if (status === null) {
    return <span className="table-text">-</span>;
  }
  const label = t(`skill.review.status.${status.toLowerCase()}`);
  const text = pendingBadge && status === 'PENDING' ? t('skill.review.pendingBadge') : label;
  return (
    <span
      aria-label={`${t('skill.columns.reviewStatus')}: ${label}`}
      data-testid={`skill-review-status-${status.toLowerCase()}`}
    >
      <Tag color={STATUS_COLORS[status]}>{text}</Tag>
    </span>
  );
}
