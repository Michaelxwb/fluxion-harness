import { Table, Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { PageSection } from '../../../components/common/ConsolePage';
import { formatNumber } from '../formatters';
import type { OverviewData } from '../types';

export function EgressSummary({ egress: input }: { egress: OverviewData['egress'] }) {
  const { t, i18n } = useTranslation();
  const egress = input ?? { requestCount: 0, sentCount: 0, completedCount: 0, failedCount: 0, blockedCount: 0, averageLatencyMs: null, topTargets: [] };
  return <PageSection title={t('overview.egress.title')}>
    <div className="overview-egress-kpis">
      <span>{t('overview.egress.requests')}: <strong>{formatNumber(egress.requestCount, i18n.language)}</strong></span>
      <span>{t('overview.egress.sent')}: <strong>{formatNumber(egress.sentCount, i18n.language)}</strong></span>
      <span>{t('overview.egress.completed')}: <strong>{formatNumber(egress.completedCount, i18n.language)}</strong></span>
      <span>{t('overview.egress.failed')}: <strong>{formatNumber(egress.failedCount, i18n.language)}</strong></span>
      <span>{t('overview.egress.blocked')}: <strong className="overview-egress-danger">{formatNumber(egress.blockedCount, i18n.language)}</strong></span>
      <span>{t('overview.egress.averageLatency')}: <strong>{egress.averageLatencyMs === null ? '—' : `${Math.round(egress.averageLatencyMs)} ms`}</strong></span>
    </div>
    <Table dataSource={egress.topTargets} pagination={false} empty={t('overview.egress.empty')}>
      <Table.Column title={t('overview.egress.target')} dataIndex="target" />
      <Table.Column title={t('overview.egress.route')} dataIndex="routeId" />
      <Table.Column title={t('overview.egress.count')} dataIndex="requestCount" />
      <Table.Column title={t('overview.egress.blocked')} dataIndex="blockedCount" render={(value) => value > 0 ? <Tag color="red">{value}</Tag> : value} />
    </Table>
  </PageSection>;
}
