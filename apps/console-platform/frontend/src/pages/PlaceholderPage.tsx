import { Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { PageCard } from '../components/common/PageCard';

export function PlaceholderPage({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();
  return (
    <PageCard>
      <Typography.Title heading={5}>{t(titleKey)}</Typography.Title>
      <Typography.Paragraph type="tertiary">{t('page.placeholder')}</Typography.Paragraph>
    </PageCard>
  );
}
