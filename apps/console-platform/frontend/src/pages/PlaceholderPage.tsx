import { Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../components/common/ConsolePage';

export function PlaceholderPage({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t(titleKey)} description={t('page.placeholder')} />
      <PageSection>
        <Typography.Text type="tertiary">{t('page.placeholder')}</Typography.Text>
      </PageSection>
    </>
  );
}
