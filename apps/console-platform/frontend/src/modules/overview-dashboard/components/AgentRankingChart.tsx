import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { EmptyState } from '../../../components/common/EmptyState';
import { formatNumber } from '../formatters';
import type { OverviewAgentRanking } from '../types';
export function AgentRankingChart({ ranking }: { ranking: OverviewAgentRanking[] }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  if (ranking.length === 0) return <EmptyState title={t('overview.charts.rankingEmpty')} />;
  const maximum = Math.max(ranking[0].runCount, 1);
  return <ol className="overview-ranking" data-testid="overview-agent-ranking">
    {ranking.map((agent, index) => <li key={agent.agentId}>
      <div className="overview-ranking-heading">
        <span className="overview-ranking-position">{index + 1}</span>
        <Button type="primary" theme="borderless" size="small" title={agent.agentName ?? agent.agentId}
          data-testid={`overview-ranking-${agent.agentId}`}
          onClick={() => navigate('/audits?agentId=' + encodeURIComponent(agent.agentId))}>
          {agent.agentName ?? 'Agent ' + agent.agentId.slice(0, 8)}</Button>
        <strong>{formatNumber(agent.runCount, i18n.language)}</strong>
      </div>
      <div className="overview-ranking-track"><div className="overview-ranking-bar"
        style={{ width: String(agent.runCount / maximum * 100) + '%' }} /></div>
    </li>)}
  </ol>;
}
