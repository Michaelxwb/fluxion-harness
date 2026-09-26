/**
 * 4 个 KPI 指标卡（设计 §3.3 CMP-02 / §3.3.1）。
 *
 * 复用公共 `MetricCards`（不另造卡片壳），卡片标题即跳转入口：启用 Agent → `/agents`、
 * 启用 Skill → `/skills`、后台执行中 → `/tasks`、启用定时任务 → `/schedules`（设计 §3.3.1 均为
 * 无二次确认的 `Typography.Text link`）。loading 用 `Skeleton` 占位，不显示伪造的 0。
 */

import { Skeleton, Typography } from '@douyinfe/semi-ui';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { MetricCards, type MetricCardItem } from '../../../components/common/MetricCards';
import type { KpiCardsProps } from '../types';

interface KpiLinkProps {
  to: string;
  text: string;
  testId: string;
}

function KpiLink({ to, text, testId }: KpiLinkProps) {
  const navigate = useNavigate();
  return (
    <Typography.Text link onClick={() => navigate(to)} data-testid={testId}>
      {text}
    </Typography.Text>
  );
}

export function KpiCards({ kpis, loading }: KpiCardsProps) {
  const { t } = useTranslation();

  const value = (raw: number) =>
    loading ? <Skeleton.Title style={{ width: 56 }} /> : raw;

  const items: MetricCardItem[] = [
    {
      label: <KpiLink to="/agents" text={t('overview.kpi.enabledAgents')} testId="kpi-agents" />,
      value: value(kpis.enabledAgents)
    },
    {
      label: <KpiLink to="/skills" text={t('overview.kpi.enabledSkills')} testId="kpi-skills" />,
      value: value(kpis.enabledSkills)
    },
    {
      label: <KpiLink to="/tasks" text={t('overview.kpi.activeTasks')} testId="kpi-tasks" />,
      value: value(kpis.activeTasks)
    },
    {
      label: (
        <KpiLink to="/schedules" text={t('overview.kpi.activeSchedules')} testId="kpi-schedules" />
      ),
      value: value(kpis.activeSchedules)
    }
  ];

  return <MetricCards items={items} />;
}
