import { Banner, Button } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorState } from '../../../components/common/ErrorState';
import { PageSection } from '../../../components/common/ConsolePage';
import { EgressSummary } from '../components/EgressSummary';
import { OverviewSkeleton } from '../components/KpiCards';
import { OverviewToolbar } from '../components/OverviewToolbar';
import { useOverview } from '../hooks/useOverview';

export function EgressMonitor() {
  const { t } = useTranslation();
  const [days, setDays] = useState(7);
  const overview = useOverview(days);
  const firstLoadFailed = overview.error && overview.data === null;
  return (
    <div className="overview-dashboard egress-monitoring-page" data-testid="egress-monitoring-page">
      <OverviewToolbar days={days} onDaysChange={setDays} loading={overview.loading}
        data={overview.data} onRefresh={overview.reload} />
      {overview.error && overview.data !== null ? (
        <Banner type="warning" description={<>{t('overview.refreshFailed')}{' '}
          <Button type="primary" theme="borderless" onClick={overview.reload}>{t('common.retry')}</Button>
        </>} />
      ) : null}
      {firstLoadFailed ? (
        <PageSection><ErrorState description={t('overview.loadFailed')} onRetry={overview.reload} /></PageSection>
      ) : overview.data === null ? (
        <PageSection><OverviewSkeleton /></PageSection>
      ) : (
        <EgressSummary egress={overview.data.egress} />
      )}
    </div>
  );
}
