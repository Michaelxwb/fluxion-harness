import { Banner, Button } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface ErrorStateProps {
  description?: ReactNode;
  onRetry?(): void;
}

export function ErrorState({ description, onRetry }: ErrorStateProps) {
  const { t } = useTranslation();
  return (
    <div className="app-error" data-testid="error-state">
      <Banner type="danger" closeIcon={null} description={description ?? t('common.loadFailed')} />
      {onRetry ? (
        <Button
          theme="borderless"
          type="tertiary"
          data-testid="error-retry"
          onClick={onRetry}
          style={{ marginTop: 8 }}
        >
          {t('common.retry')}
        </Button>
      ) : null}
    </div>
  );
}
