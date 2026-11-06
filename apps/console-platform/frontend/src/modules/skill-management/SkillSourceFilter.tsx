import { Select } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { SkillSource } from './services/skills';

export interface SkillSourceFilterProps {
  value: SkillSource | '';
  onChange(value: SkillSource | ''): void;
}

export function SkillSourceFilter({ value, onChange }: SkillSourceFilterProps) {
  const { t } = useTranslation();
  return (
    <Select
      data-testid="skill-source-filter"
      aria-label={t('skill.columns.source')}
      value={value || undefined}
      style={{ width: 140 }}
      showClear
      placeholder={t('skill.columns.source')}
      optionList={[
        { value: 'SYSTEM', label: t('skill.source.system') },
        { value: 'ADMIN', label: t('skill.source.admin') },
        { value: 'USER', label: t('skill.source.user') }
      ]}
      onChange={(next) => onChange((next as SkillSource | undefined) ?? '')}
    />
  );
}
