/**
 * Run 详情（只读）。两个入口共用：运行审计「关联 Run」与后台任务详情「来源运行」。
 *
 * **只读、且只展示结构**：运行的用户输入原文与事件负载都不在投影里（后端 `GET /api/v1/runs/{id}`
 * 与 `/operations` 就不返回它们），所以这里也不可能渲染出来。Console 至今零暴露对话原文，要不要
 * 开这个口是产品决定，不该由「闭环一条 issue」顺带定——先不加易、后撤难。
 *
 * 轮廓已剔除流式增量（`message.delta`，一次运行可达数千行），超上限时后端置
 * `timeline_truncated`，页面**如实说明还有更多**，不假装完整。
 *
 * 数据状态归 `hooks/useRunDetail` / `hooks/useRunOperations`（只经 service 层取数）；本组件是
 * 容器：页签、刷新、请求代次接线，以及 Run→Task 的点击意向上抛（**不自行导航**）。
 */

import { Banner, Button, Spin, Tabs } from '@douyinfe/semi-ui';
import { IconRefresh } from '@douyinfe/semi-icons';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { StatusTag } from '../../components/common/StatusTag';
import { RunOperationTable } from './components/RunOperationTable';
import { RunTimelineOutline } from './components/RunTimelineOutline';
import { useRunDetail } from './hooks/useRunDetail';
import { useRunOperations } from './hooks/useRunOperations';
import type { RunDetail, WaitReason } from './services/runs';
import { runStatusOptions } from './statusOptions';

export interface RunDetailSideSheetProps {
  runId: string | null;
  onCancel(): void;
  /**
   * 关联 Task 的点击意图（可选）：只有提供回调且 Task 已受理时才呈现详情链接；
   * 页面负责关闭本面板并打开 Task 详情，本组件不导航、不递归挂载另一面板。
   */
  onOpenTask?(taskId: string, sourceRunId: string): void;
}

const WAITING_STATUSES = ['WAITING_TOOL', 'WAITING_INPUT'];

function waitingReasonKey(reason: WaitReason): string {
  return `run.detail.waitingReason.${reason}`;
}

function buildBasicItems(detail: RunDetail, t: (key: string, options?: Record<string, unknown>) => string) {
  const statusOptions = runStatusOptions(t);
  const waiting = WAITING_STATUSES.includes(detail.status);
  return [
    {
      label: t('run.columns.status'),
      value: <StatusTag status={detail.status} options={statusOptions} />
    },
    { label: t('run.columns.agent'), value: detail.agent_name ?? detail.agent_id },
    { label: t('run.columns.actor'), value: detail.actor_name ?? detail.actor_user_id },
    { label: t('run.columns.trace'), value: detail.trace_id },
    {
      label: t('run.columns.startTime'),
      value: detail.start_time ? <DateTimeText value={detail.start_time} /> : '-'
    },
    {
      label: t('run.columns.endTime'),
      value: detail.end_time ? <DateTimeText value={detail.end_time} /> : '-'
    },
    ...(waiting
      ? [
          {
            label: t('run.detail.waitingSince'),
            value: detail.waiting_since ? <DateTimeText value={detail.waiting_since} /> : '-'
          },
          {
            label: t('run.detail.deadline'),
            value: detail.deadline_at ? <DateTimeText value={detail.deadline_at} /> : '-'
          },
          {
            label: t('run.detail.pendingJoin'),
            value: detail.pending_join_count
          },
          {
            label: t('run.detail.pendingSubmission'),
            value: detail.pending_submission_count
          },
          {
            label: t('run.detail.continuations'),
            value: detail.continuation_count
          }
        ]
      : []),
    {
      fullWidth: true,
      label: t('run.detail.error'),
      value: (
        <span data-testid="run-detail-error">
          {detail.error_code ? `${detail.error_code}: ` : ''}
          {detail.error_message ?? '-'}
        </span>
      )
    }
  ];
}

export function RunDetailSideSheet(props: RunDetailSideSheetProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('basic');
  const detailState = useRunDetail(props.runId);
  const operationsState = useRunOperations(props.runId, activeTab === 'operations');
  const { detail, loading, failed, notFound, reload } = detailState;
  const {
    page: operations,
    loading: operationsLoading,
    failed: operationsFailed,
    reload: reloadOperations,
    changePage,
    changePageSize
  } = operationsState;

  const handleRefresh = useCallback(() => {
    reload();
    // 只对**已加载**的关联页签重取；未打开过就不凭空发分页请求。
    reloadOperations();
  }, [reload, reloadOperations]);

  const handlePageChange = useCallback(
    (page: number) => {
      // 局部 loading 期间禁用重复翻页（响应代次只保留最后一次）。
      if (!operationsLoading) {
        changePage(page);
      }
    },
    [operationsLoading, changePage]
  );

  const handlePageSizeChange = useCallback(
    (pageSize: number) => {
      if (!operationsLoading) {
        changePageSize(pageSize);
      }
    },
    [operationsLoading, changePageSize]
  );

  const handleOpenTask = useCallback(
    (taskId: string) => {
      if (props.runId && props.onOpenTask) {
        props.onOpenTask(taskId, props.runId);
      }
    },
    [props.runId, props.onOpenTask]
  );

  const notice = failed ? (
    <ErrorState
      description={notFound ? t('run.detail.unavailable') : undefined}
      onRetry={reload}
    />
  ) : undefined;

  return (
    <DetailSideSheet
      visible={props.runId !== null}
      title={props.runId ?? ''}
      subtitle={detail ? `${t(`run.status.${detail.status}`)} · ${detail.trace_id}` : t('run.detail.basic')}
      actions={
        <Button
          icon={<IconRefresh />}
          data-testid="run-detail-refresh"
          onClick={handleRefresh}
        >
          {t('run.detail.refresh')}
        </Button>
      }
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onCancel}
      notice={notice}
    >
      <Tabs.TabPane itemKey="basic" tab={t('run.detail.basic')}>
        {loading && !detail ? <Spin /> : null}
        {detail ? (
          <>
            {detail.status === 'WAITING_TOOL' && detail.waiting_reason ? (
              <div data-testid="run-detail-waiting-banner">
                <Banner
                  type="warning"
                  closeIcon={null}
                  description={t(waitingReasonKey(detail.waiting_reason))}
                />
              </div>
            ) : null}
            <div className="detail-section-title">{t('run.detail.basic')}</div>
            <DetailGrid items={buildBasicItems(detail, t)} />
          </>
        ) : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="operations" tab={t('run.detail.operations')}>
        {operationsLoading ? <Spin /> : null}
        {operationsFailed ? (
          <ErrorState onRetry={reloadOperations} />
        ) : operations ? (
          operations.total === 0 ? (
            <EmptyState title={t('run.detail.operationsEmpty')} />
          ) : (
            <>
              <div className="detail-section-title">{t('run.detail.operations')}</div>
              <RunOperationTable rows={operations.items} onOpenTask={props.onOpenTask ? handleOpenTask : undefined} />
              <PaginationFooter
                page={operations.page}
                pageSize={operationsState.pageSize}
                total={operations.total}
                onPageChange={handlePageChange}
                onPageSizeChange={handlePageSizeChange}
              />
            </>
          )
        ) : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="timeline" tab={t('run.detail.timeline')}>
        {detail ? (
          <RunTimelineOutline events={detail.timeline} truncated={detail.timeline_truncated} />
        ) : null}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
