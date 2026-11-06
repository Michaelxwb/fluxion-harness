import { useTranslation } from 'react-i18next';

import { TableText } from '../../components/common/TableText';

export function SkillPlatforms(props: { platforms: string[] }) {
  const { t } = useTranslation();
  return (
    <TableText>
      {props.platforms.length > 0 ? props.platforms.join(', ') : t('skill.platforms.undeclared')}
    </TableText>
  );
}
