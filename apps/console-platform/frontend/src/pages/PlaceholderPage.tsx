import { IconInbox } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../components/common/ConsolePage';
import { EmptyState } from '../components/common/EmptyState';

export function PlaceholderPage({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t(titleKey)} description={t('page.planned')} />
      <PageSection>
        <EmptyState
          icon={<IconInbox size="extra-large" />}
          title={t('page.placeholder')}
          description={t('page.plannedHint')}
        />
      </PageSection>
    </>
  );
}
