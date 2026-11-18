/**
 * 后台任务详情（design frontend §3.3 CMP-02、§3.6；TASK-005）。
 *
 * 三段事实：结果（成功正文/失败原文/计数）→ 产物（可下载 + 交付状态）→ 子任务表。
 * 详情复用共享 `ExecutionDetail`：同一执行详情路由既服务后台根任务，也服务子任务。
 *
 * 子任务页只替换子任务段，不重读列表；本组件不发请求、不读 URL。
 */

import { Banner, Button, Skeleton, Typography } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import type { ExecutionDetail, TaskArtifact } from '../types';
import type { ArtifactDownloadState } from './artifactDownload';
import type { BackgroundTaskDetailState } from './backgroundState';
import { PartialFailureTag, TaskTypeTag } from './backgroundTaskLabels';
import { ChildTaskTable } from './ChildTaskTable';
import { ExecutionStatusTag } from './labels';
import type { AgentOption } from './ScheduleListPanel';
import { isNotFoundCode } from './taskCenterState';
import { TaskArtifactList } from './TaskArtifactList';
import { TaskResultSection } from './TaskResultSection';

export interface BackgroundTaskDetailPanelProps {
  state: BackgroundTaskDetailState;
  downloads: ArtifactDownloadState;
  agents?: readonly AgentOption[];
  onBack(): void;
  onBackToParent(parentId: string): void;
  onRefresh(): void;
  onChildPage(page: number): void;
  onOpenChild(taskId: string): void;
  onDownloadArtifact(artifact: TaskArtifact): void;
}

function agentName(agents: readonly AgentOption[] | undefined, agentId: string | null): string | null {
  if (agentId === null) return null;
  return agents?.find((agent) => agent.agent_id === agentId)?.display_name ?? null;
}

function Fact(props: { label: string; testId: string; children: ReactNode }) {
  return (
    <div className="chat-task-fact">
      <dt>{props.label}</dt>
      <dd data-testid={props.testId}>{props.children}</dd>
    </div>
  );
}

function Summary(props: { detail: ExecutionDetail; agents?: readonly AgentOption[] }) {
  const { t } = useTranslation();
  const detail = props.detail;
  const name = agentName(props.agents, detail.agent_id);
  const artifactTotal = Math.max(detail.artifact_total, detail.result_artifacts.length);
  return (
    <section className="chat-background-section" data-testid="chat-background-summary">
      <header className="chat-background-summary-header">
        <Typography.Title heading={6}>{detail.skill_name}</Typography.Title>
        <TaskTypeTag taskType={detail.task_type} />
        <ExecutionStatusTag status={detail.status} />
        {detail.parent_id === null ? null : (
          <span className="chat-background-child-marker" data-testid="chat-background-child-marker">
            {t('chat.taskCenter.background.children.marker')}
          </span>
        )}
        {detail.partial_failure ? <PartialFailureTag counts={detail.counts} /> : null}
        {artifactTotal > 0 ? (
          <span className="chat-background-has-artifacts" data-testid="chat-background-has-artifacts">
            {t('chat.taskCenter.background.row.hasArtifacts')}
          </span>
        ) : null}
      </header>
      <dl className="chat-task-fact-list">
        <Fact label={t('chat.taskCenter.background.fact.agent')} testId="chat-background-fact-agent">
          {name ?? detail.agent_id ?? t('chat.taskCenter.background.fact.unknown')}
        </Fact>
        <Fact label={t('chat.taskCenter.background.fact.taskType')} testId="chat-background-fact-type">
          <TaskTypeTag taskType={detail.task_type} />
        </Fact>
        <Fact label={t('chat.taskCenter.background.fact.createdAt')} testId="chat-background-fact-created">
          {detail.started_at === null ? '-' : <DateTimeText value={detail.started_at} />}
        </Fact>
        <Fact label={t('chat.taskCenter.background.fact.finishedAt')} testId="chat-background-fact-finished">
          {detail.finished_at === null ? (
            t('chat.taskCenter.background.fact.running')
          ) : (
            <DateTimeText value={detail.finished_at} />
          )}
        </Fact>
        {detail.item_key === null ? null : (
          <Fact label={t('chat.taskCenter.background.fact.itemKey')} testId="chat-background-fact-item-key">
            <code>{detail.item_key}</code>
          </Fact>
        )}
        {detail.parent_id === null ? (
          <Fact label={t('chat.taskCenter.background.fact.children')} testId="chat-background-fact-children">
            {detail.children.total > 0
              ? `${detail.children.total}`
              : t('chat.taskCenter.background.fact.none')}
          </Fact>
        ) : null}
      </dl>
    </section>
  );
}

export function BackgroundTaskDetailPanel(props: BackgroundTaskDetailPanelProps) {
  const { t } = useTranslation();
  const { state } = props;
  const detail = state.detail;
  const notFound = isNotFoundCode(state.errorCode) && detail === null;
  const firstLoad = state.phase === 'LOADING' && detail === null;
  const showError = state.errorCode !== null && !notFound;
  const parentId = detail?.parent_id ?? null;

  return (
    <section
      className="chat-task-center chat-task-center-detail"
      data-testid="webchat-background-task-detail"
      data-view="background-detail"
      data-task-id={state.taskId ?? ''}
      aria-label={t('chat.taskCenter.background.detailLabel')}
    >
      <header className="chat-task-center-header">
        <Button
          size="small"
          theme="borderless"
          data-testid="chat-background-back"
          aria-label={t('chat.taskCenter.background.back')}
          onClick={props.onBack}
        >
          {t('chat.taskCenter.background.back')}
        </Button>
        {parentId === null ? null : (
          <Button
            size="small"
            theme="borderless"
            data-testid="chat-background-back-parent"
            onClick={() => props.onBackToParent(parentId)}
          >
            {t('chat.taskCenter.background.backToParent')}
          </Button>
        )}
        <Typography.Title heading={5}>{t('chat.taskCenter.background.detailTitle')}</Typography.Title>
        <Button
          size="small"
          theme="borderless"
          data-testid="chat-background-detail-refresh"
          aria-label={t('chat.taskCenter.list.refresh')}
          onClick={props.onRefresh}
        >
          {t('chat.taskCenter.list.refresh')}
        </Button>
      </header>

      {notFound ? (
        <div className="chat-task-center-state" data-testid="chat-task-center-not-found" role="alert">
          {t('chat.taskCenter.error.notFound')}
        </div>
      ) : null}
      {showError ? (
        <div className="chat-task-center-error" data-testid="chat-background-error">
          <Banner
            type="danger"
            closeIcon={null}
            description={t('chat.taskCenter.background.detailLoadFailed')}
          />
          <Button size="small" data-testid="chat-background-retry" onClick={props.onRefresh}>
            {t('chat.taskCenter.list.retry')}
          </Button>
          {state.errorCode === null ? null : (
            <span className="chat-task-center-error-code">
              {t('chat.taskCenter.error.codeSuffix', { code: state.errorCode })}
            </span>
          )}
        </div>
      ) : null}
      {firstLoad ? (
        <div className="chat-task-center-loading" data-testid="chat-task-center-loading">
          <Skeleton placeholder={<Skeleton.Paragraph rows={3} />} loading />
        </div>
      ) : null}

      {detail === null ? null : (
        <>
          <Summary detail={detail} agents={props.agents} />
          <TaskResultSection detail={detail} />
          <section className="chat-background-section" data-testid="chat-background-artifacts">
            <header className="chat-background-section-header">
              <Typography.Title heading={6}>{t('chat.taskCenter.artifact.title')}</Typography.Title>
              <span className="chat-background-section-hint">
                {t('chat.taskCenter.background.artifacts.total', {
                  count: Math.max(detail.artifact_total, detail.result_artifacts.length)
                })}
              </span>
            </header>
            {detail.artifact_total > detail.result_artifacts.length ? (
              <p className="chat-background-artifacts-truncated" data-testid="chat-background-artifacts-truncated">
                {t('chat.taskCenter.background.artifacts.truncated', {
                  total: detail.artifact_total,
                  shown: detail.result_artifacts.length
                })}
              </p>
            ) : null}
            <TaskArtifactList
              artifacts={detail.result_artifacts}
              state={props.downloads}
              onDownload={props.onDownloadArtifact}
            />
          </section>
          {detail.parent_id !== null ? null : (
            <ChildTaskTable
              items={detail.children.items}
              page={detail.children.page}
              pageSize={detail.children.page_size}
              total={detail.children.total}
              phase={state.childrenPhase}
              errorCode={state.childrenErrorCode}
              onPage={props.onChildPage}
              onOpenChild={props.onOpenChild}
              onRetry={() => props.onChildPage(detail.children.page)}
            />
          )}
        </>
      )}
    </section>
  );
}
