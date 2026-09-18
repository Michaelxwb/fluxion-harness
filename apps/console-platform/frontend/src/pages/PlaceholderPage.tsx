import { useTranslation } from 'react-i18next';

import { PageCard } from '../components/common/PageCard';

export function PlaceholderPage({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();
  return (
    <PageCard title={t(titleKey)} subtitle={t('page.placeholder')}>
      <div />
    </PageCard>
  );
}
