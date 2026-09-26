/**
 * 概览页容器（设计 §3.3 CMP-01 / §3.6）：一次加载 overview data 并组装页面骨架。
 *
 * 三态按设计 §3.6 分流：loading 用 Skeleton 卡片；**首载失败**整页 `ErrorState` + 重试（不伪造 0）；
 * 成功后渲染 4 个 KPI。运行关系说明与两个运营列表由 TASK-007 追加到同一 `PageSection`。
 */

import { useTranslation } from 'react-i18next';

import { ErrorState } from '../../../components/common/ErrorState';
import { PageHeader, PageSection } from '../../../components/common/ConsolePage';
import { KpiCards } from '../components/KpiCards';
import { useOverview } from '../hooks/useOverview';
import type { OverviewKpis } from '../types';

const EMPTY_KPIS: OverviewKpis = {
  enabledAgents: 0,
  enabledSkills: 0,
  activeTasks: 0,
  activeSchedules: 0
};

export function OverviewPage() {
  const { t } = useTranslation();
  const { data, loading, error, reload } = useOverview();

  return (
    <>
      <PageHeader title={t('overview.title')} description={t('overview.subtitle')} />
      <PageSection>
        {error && data === null ? (
          <ErrorState description={t('overview.loadFailed')} onRetry={reload} />
        ) : (
          <KpiCards kpis={data?.kpis ?? EMPTY_KPIS} loading={loading && data === null} />
        )}
      </PageSection>
    </>
  );
}
