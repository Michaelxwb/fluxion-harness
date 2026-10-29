import { Banner, Button } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PageHeader, PageSection } from '../../../components/common/ConsolePage';
import { ErrorState } from '../../../components/common/ErrorState';
import { AttentionSummary } from '../components/AttentionSummary';
import { KpiCards, OverviewSkeleton } from '../components/KpiCards';
import { OverviewCharts } from '../components/OverviewCharts';
import { OverviewToolbar } from '../components/OverviewToolbar';
import { ResourceSummary } from '../components/ResourceSummary';
import { useOverview } from '../hooks/useOverview';
import type { OverviewData } from '../types';

function OverviewContent({ data }: { data: OverviewData }) {
  return (
    <div className="overview-body">
      <div className="overview-status">
        <PageSection>
          <ResourceSummary resources={data.resources} />
          <AttentionSummary data={data} />
        </PageSection>
      </div>
      <KpiCards kpis={data.kpis} />
      <OverviewCharts data={data} />
    </div>
  );
}

export function OverviewPage() {
  const { t } = useTranslation();
  const [days, setDays] = useState(7);
  const overview = useOverview(days);
  const firstLoadFailed = overview.error && overview.data === null;
  return (
    <div className="overview-dashboard" data-testid="overview-dashboard">
      <PageHeader
        title={t('overview.title')}
        description={t('overview.subtitle')}
        extra={
          <OverviewToolbar
            days={days}
            onDaysChange={setDays}
            loading={overview.loading}
            data={overview.data}
            onRefresh={overview.reload}
          />
        }
      />
      {overview.error && overview.data !== null ? (
        <Banner
          type="warning"
          data-testid="overview-refresh-error"
          description={
            <>
              {t('overview.refreshFailed')}{' '}
              <Button
                type="primary"
                theme="borderless"
                data-testid="overview-refresh-retry"
                onClick={overview.reload}
              >
                {t('common.retry')}
              </Button>
            </>
          }
        />
      ) : null}
      {firstLoadFailed ? (
        <PageSection>
          <ErrorState description={t('overview.loadFailed')} onRetry={overview.reload} />
        </PageSection>
      ) : overview.data === null ? (
        <PageSection>
          <OverviewSkeleton />
        </PageSection>
      ) : (
        <OverviewContent data={overview.data} />
      )}
    </div>
  );
}
