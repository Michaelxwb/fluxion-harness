import { Tooltip } from '@douyinfe/semi-ui';
import { IconGlobe, IconUserGroup } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';

import type { SkillListItem } from './services/skills';

export function SkillDetailHeading({ name, userScope }: {
  name: string; userScope: SkillListItem['user_scope']
}) {
  const { t } = useTranslation();
  const hint = t(userScope === 'ALL' ? 'skill.detail.scopeAllNotice' : 'skill.detail.scopeSelectedNotice');
  return (
    <span className="skill-detail-heading">
      <span>{name}</span>
      <Tooltip content={hint}>
        <span className="skill-scope-icon" data-testid="skill-scope-icon"
          role="img" aria-label={hint} tabIndex={0}>
          {userScope === 'ALL' ? <IconGlobe /> : <IconUserGroup />}
        </span>
      </Tooltip>
    </span>
  );
}
