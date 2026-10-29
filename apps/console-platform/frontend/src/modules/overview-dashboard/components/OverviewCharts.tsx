import { Button, Tooltip } from '@douyinfe/semi-ui';
import { IconInfoCircle } from '@douyinfe/semi-icons';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { PageSection } from '../../../components/common/ConsolePage';
import { AgentRankingChart } from './AgentRankingChart';
import { TaskTrendChart } from './TaskTrendChart';
import { TaskStatusDonut } from './TaskStatusDonut';
import { TokenTrendChart } from './TokenTrendChart';
import type { OverviewData, OverviewScope } from '../types';
function ScopeSwitch({ scope, onChange, id }: { scope: OverviewScope; onChange(scope: OverviewScope): void; id: string }) {
  const { t } = useTranslation();
  return <div className="overview-scope-switch" role="group" aria-label={t('overview.charts.scope')}>
    <Button type="primary" size="small" theme={scope === 'runs' ? 'solid' : 'borderless'}
      aria-pressed={scope === 'runs'} data-testid={`overview-${id}-switch-runs`} onClick={() => onChange('runs')}>{t('overview.scope.runs')}</Button>
    <Button type="primary" size="small" theme={scope === 'tasks' ? 'solid' : 'borderless'}
      aria-pressed={scope === 'tasks'} data-testid={`overview-${id}-switch-tasks`} onClick={() => onChange('tasks')}>{t('overview.scope.tasks')}</Button>
  </div>;
}
function ChartCard(props: { id: string; title: string; hint: string; extra?: ReactNode; children: ReactNode }) {
  return <div data-testid={`overview-${props.id}-card`}>
    <PageSection
      title={props.title}
      extra={
        <div className="overview-chart-extra">
          <Tooltip content={props.hint}>
            <span className="overview-info-icon" tabIndex={0} aria-label={props.hint}>
              <IconInfoCircle size="small" />
            </span>
          </Tooltip>
          {props.extra}
        </div>
      }
    >
      <div className="overview-chart-canvas">{props.children}</div>
    </PageSection>
  </div>;
}
export function OverviewCharts({ data }: { data: OverviewData }) {
  const { t } = useTranslation();
  const [trendScope, setTrendScope] = useState<OverviewScope>('runs');
  const [statusScope, setStatusScope] = useState<OverviewScope>('runs');
  return <div className="overview-charts">
    <ChartCard id="trend" title={t('overview.charts.trend')} hint={t('overview.charts.trendHint')}
      extra={<ScopeSwitch scope={trendScope} onChange={setTrendScope} id="trend" />}>
      <TaskTrendChart trend={data.trend[trendScope]} />
    </ChartCard>
    <ChartCard id="status" title={t('overview.charts.status')} hint={t('overview.charts.statusHint', { days: data.days })}
      extra={<ScopeSwitch scope={statusScope} onChange={setStatusScope} id="status" />}>
      <TaskStatusDonut scope={statusScope} status={data.status[statusScope]} />
    </ChartCard>
    <ChartCard id="token" title={t('overview.charts.tokens')} hint={t('overview.hint.tokens')}>
      <TokenTrendChart trend={data.trend.tokens} />
    </ChartCard>
    <ChartCard id="agent" title={t('overview.charts.agents')} hint={t('overview.charts.rankingHint')}>
      <AgentRankingChart ranking={data.agentRanking} />
    </ChartCard>
  </div>;
}
