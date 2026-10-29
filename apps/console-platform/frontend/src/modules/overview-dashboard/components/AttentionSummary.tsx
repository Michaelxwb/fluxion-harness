import { Button, Tooltip } from '@douyinfe/semi-ui';
import { IconInfoCircle } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { formatNumber } from '../formatters';
import type { OverviewData } from '../types';
interface Counter { key: string; label: string; count: number; to: string; }
function Counters({ entries, prefix, danger = false }: {
  entries: Counter[]; prefix: string; danger?: boolean;
}) {
  const { i18n } = useTranslation();
  const navigate = useNavigate();
  return <div className={`overview-counters${danger ? ' overview-counters-danger' : ''}`}>
    {entries.map((entry) => <Button key={entry.key} type="primary" theme="borderless" size="small"
      data-testid={`overview-${prefix}-${entry.key}`} onClick={() => navigate(entry.to)}>
      <span>{entry.label}</span><strong>{formatNumber(entry.count, i18n.language)}</strong>
    </Button>)}
  </div>;
}
export function AttentionSummary({ data }: { data: OverviewData }) {
  const { t } = useTranslation();
  const a = data.activity;
  const current: Counter[] = [
    { key: 'active-runs', label: t('overview.activity.activeRuns'), count: a.activeRuns, to: '/audits' },
    { key: 'waiting-input', label: t('overview.activity.waitingInput'), count: a.waitingInputRuns, to: '/audits' },
    { key: 'waiting-tool', label: t('overview.activity.waitingTool'), count: a.waitingToolRuns, to: '/audits' },
    { key: 'queued-tasks', label: t('overview.activity.queuedTasks'), count: a.queuedTasks, to: '/tasks?status=QUEUED' },
    { key: 'running-tasks', label: t('overview.activity.runningTasks'), count: a.runningTasks, to: '/tasks?status=RUNNING' },
    { key: 'waiting-tasks', label: t('overview.activity.waitingTasks'), count: a.waitingTasks, to: '/tasks?status=WAITING' },
    { key: 'overdue-tasks', label: t('overview.activity.overdueTasks'), count: a.overdueTasks, to: '/tasks' }
  ];
  const failures: Counter[] = [
    { key: 'failed-runs', label: t('overview.attention.failedRuns'), count: data.attention.failedRuns, to: '/audits' },
    { key: 'failed-tasks', label: t('overview.attention.failedTasks'), count: data.attention.failedTasks, to: '/tasks?status=FAILED' },
    { key: 'failed-deliveries', label: t('overview.attention.failedDeliveries'), count: data.attention.failedDeliveries, to: '/tasks' }
  ];
  return <div className="overview-attention">
    <div className="overview-attention-row">
      <span className="overview-summary-label">
        {t('overview.activity.title')}
        <Tooltip content={t('overview.activity.hint')}>
          <span className="overview-info-icon" tabIndex={0} aria-label={t('overview.activity.hint')}>
            <IconInfoCircle size="small" />
          </span>
        </Tooltip>
      </span>
      <Counters entries={current} prefix="activity" />
    </div>
    <div className="overview-attention-row">
      <span className="overview-summary-label">{t('overview.attention.title')}</span>
      <Counters entries={failures} prefix="attention" danger />
    </div>
  </div>;
}
