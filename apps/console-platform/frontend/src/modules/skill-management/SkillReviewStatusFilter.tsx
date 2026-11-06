import { Select } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { SkillReviewStatus } from './services/skills';

export interface SkillReviewStatusFilterProps {
  value: SkillReviewStatus | '';
  onChange(value: SkillReviewStatus | ''): void;
}

export function SkillReviewStatusFilter({ value, onChange }: SkillReviewStatusFilterProps) {
  const { t } = useTranslation();
  return (
    <Select
      data-testid="skill-review-status-filter"
      aria-label={t('skill.columns.reviewStatus')}
      value={value || undefined}
      style={{ width: 150 }}
      showClear
      placeholder={t('skill.review.filter.all')}
      optionList={[
        { value: 'PENDING', label: t('skill.review.status.pending') },
        { value: 'APPROVED', label: t('skill.review.status.approved') },
        { value: 'REJECTED', label: t('skill.review.status.rejected') },
        { value: 'SUPERSEDED', label: t('skill.review.status.superseded') }
      ]}
      onChange={(next) => onChange((next as SkillReviewStatus | undefined) ?? '')}
    />
  );
}
