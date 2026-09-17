import { Card, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

export function PlaceholderPage({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();
  return (
    <Card>
      <Typography.Title heading={4}>{t(titleKey)}</Typography.Title>
      <Typography.Text type="tertiary">{t('page.placeholder')}</Typography.Text>
    </Card>
  );
}
