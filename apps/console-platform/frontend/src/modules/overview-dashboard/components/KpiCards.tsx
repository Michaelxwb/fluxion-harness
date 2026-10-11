import { Skeleton, Typography, Tooltip } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { MetricCards, type MetricCardItem } from '../../../components/common/MetricCards';
import { formatNumber, formatPercent, formatSeconds } from '../formatters';
import type { OverviewKpis } from '../types';
function MetricValue({ value, id }: { value: string; id: string }) {
  return <span data-testid={`kpi-value-${id}`} title={value}>{value}</span>;
}
export function KpiCards({ kpis }: { kpis: OverviewKpis }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const language = i18n.language;
  const label = (text: string, hint: string, to = '/audits') =>
    <Tooltip content={hint}><Typography.Text link onClick={() => navigate(to)}>{text}</Typography.Text></Tooltip>;
  const value = (number: string, id: string) => <MetricValue value={number} id={id} />;
  const items: MetricCardItem[] = [
    { label: label(t('overview.kpi.runs'), t('overview.hint.runs')),
      value: value(formatNumber(kpis.runCount, language), 'run-count') },
    { label: label(t('overview.kpi.successRate'), t('overview.hint.successRate')),
      value: value(formatPercent(kpis.runSuccessRate, language), 'success-rate') },
    { label: label(t('overview.kpi.averageDuration'), t('overview.hint.averageDuration')),
      value: value(kpis.averageRunDurationMs === null ? '—' : t('overview.seconds', {
        value: formatSeconds(kpis.averageRunDurationMs, language) }), 'average-duration') },
    { label: label(t('overview.kpi.activeUsers'), t('overview.hint.activeUsers')),
      value: value(formatNumber(kpis.activeUsers, language), 'active-users') },
    { label: label(t('overview.kpi.modelRequests'), t('overview.hint.modelRequests'), '/audits?auditType=MODEL'),
      value: value(formatNumber(kpis.modelRequestCount, language), 'model-requests') },
    { label: label(t('overview.kpi.tokens'), t('overview.hint.tokens'), '/audits?auditType=MODEL'),
      value: <Tooltip content={t('overview.tokens.details', {
        input: kpis.inputTokens === null ? t('overview.notRecorded') : formatNumber(kpis.inputTokens, language),
        output: kpis.outputTokens === null ? t('overview.notRecorded') : formatNumber(kpis.outputTokens, language) })}>
        <span>{value(kpis.totalTokens === null ? t('overview.notRecorded') : formatNumber(kpis.totalTokens, language, true), 'tokens')}</span>
      </Tooltip>,
      hint: kpis.modelRequestCount > 0 ? t('overview.tokens.coverage', {
        reported: kpis.tokenReportedRequestCount, total: kpis.modelRequestCount,
        coverage: formatPercent(kpis.tokenCoverage, language) }) : t('overview.tokens.noRequests') }
  ];
  return <div className="overview-kpis"><MetricCards items={items} /></div>;
}
export function OverviewSkeleton() {
  return <div data-testid="overview-loading"><Skeleton placeholder={<Skeleton.Paragraph rows={6} />} loading /></div>;
}
