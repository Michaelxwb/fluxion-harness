/**
 * 概览页容器（设计 §3.3 CMP-01 / §3.6）：一次加载 overview data 并组装页面骨架与四块内容。
 *
 * 三态按设计 §3.6 分流：loading 用 Skeleton；**首载失败**整页 `ErrorState` + 重试（不伪造 0）；
 * 成功后渲染 KPI、运行关系说明与两个运营列表。
 *
 * 跳转接线（设计 §3.3.1 / §3.5「跳转 = 路由」）：列表块只上抛回调，导航集中在本容器；
 * Task 打开详情走 `/tasks?taskId=`、Schedule 走 `/schedules?scheduleId=`（与审计模块的关联跳转同款），
 * 「查看全部」进对应列表页。
 */

import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ErrorState } from '../../../components/common/ErrorState';
import { PageHeader, PageSection } from '../../../components/common/ConsolePage';
import { KpiCards } from '../components/KpiCards';
import { NextScheduleList } from '../components/NextScheduleList';
import { RecentTaskList } from '../components/RecentTaskList';
import { RuntimeRelationCard } from '../components/RuntimeRelationCard';
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
  const navigate = useNavigate();
  const { data, loading, error, reload } = useOverview();

  const openTask = useCallback(
    (taskId: string) => navigate(`/tasks?taskId=${encodeURIComponent(taskId)}`),
    [navigate]
  );
  const openSchedule = useCallback(
    (scheduleId: string) => navigate(`/schedules?scheduleId=${encodeURIComponent(scheduleId)}`),
    [navigate]
  );
  const openAllTasks = useCallback(() => navigate('/tasks'), [navigate]);
  const openAllSchedules = useCallback(() => navigate('/schedules'), [navigate]);

  const firstLoadFailed = error && data === null;
  const showSkeleton = loading && data === null;

  return (
    <>
      <PageHeader title={t('overview.title')} description={t('overview.subtitle')} />
      <PageSection>
        {firstLoadFailed ? (
          <ErrorState description={t('overview.loadFailed')} onRetry={reload} />
        ) : (
          <>
            <KpiCards kpis={data?.kpis ?? EMPTY_KPIS} loading={showSkeleton} />
            <RuntimeRelationCard />
            <RecentTaskList
              items={data?.recentTasks ?? []}
              loading={showSkeleton}
              onOpenTask={openTask}
              onViewAll={openAllTasks}
            />
            <NextScheduleList
              items={data?.nextSchedules ?? []}
              loading={showSkeleton}
              onOpenSchedule={openSchedule}
              onViewAll={openAllSchedules}
            />
          </>
        )}
      </PageSection>
    </>
  );
}
