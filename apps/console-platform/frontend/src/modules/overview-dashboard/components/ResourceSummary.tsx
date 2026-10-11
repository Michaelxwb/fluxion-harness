import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../../auth/AuthContext';
import type { OverviewResources } from '../types';
import { formatNumber } from '../formatters';
export function ResourceSummary({ resources }: { resources: OverviewResources }) {
  const { t, i18n } = useTranslation();
  const { account } = useAuth();
  const navigate = useNavigate();
  const entries = [
    { key: 'agents', label: t('overview.resources.agents'), count: resources.enabledAgents, to: '/agents' },
    { key: 'skills', label: t('overview.resources.skills'), count: resources.enabledSkills, to: '/skills' },
    { key: 'mcps', label: t('overview.resources.mcps'), count: resources.enabledMcps, to: '/mcp' },
    { key: 'models', label: t('overview.resources.models'), count: resources.enabledModels, to: '/models' },
    { key: 'users', label: t('overview.resources.users'), count: resources.users, to: '/users' },
    { key: 'schedules', label: t('overview.resources.schedules'), count: resources.activeSchedules, to: '/schedules' }
  ];
  return <div className="overview-resources" data-testid="overview-resources">
    <span className="overview-summary-label">{t('overview.resources.title')}</span>
    {entries.map((entry) => <span key={entry.key} data-testid={`overview-resource-${entry.key}`}>
      {entry.key === 'users' && account?.role !== 'ADMIN' ? <span>{entry.label}</span> :
        <Button theme="borderless" type="primary" size="small" onClick={() => navigate(entry.to)}>{entry.label}</Button>}
      <strong>{formatNumber(entry.count, i18n.language)}</strong>
    </span>)}
  </div>;
}
