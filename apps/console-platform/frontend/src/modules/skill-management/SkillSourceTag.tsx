import { Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { SkillSource } from './services/skills';

const SOURCE_COLORS: Record<SkillSource, 'green' | 'blue' | 'grey'> = {
  SYSTEM: 'green',
  ADMIN: 'blue',
  USER: 'grey'
};

export function SkillSourceTag(props: { source: SkillSource }) {
  const { t } = useTranslation();
  const label = t(`skill.source.${props.source.toLowerCase()}`);
  return (
    <span
      aria-label={`${t('skill.columns.source')}: ${label}`}
      data-testid={`skill-source-${props.source.toLowerCase()}`}
    >
      <Tag color={SOURCE_COLORS[props.source]}>{label}</Tag>
    </span>
  );
}
