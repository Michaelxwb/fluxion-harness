/**
 * 后台任务详情（改版：结论优先 + 条件子任务布局）。
 *
 * 有子任务：环形图/进度条 + 文件双列 + 子任务卡；
 * 无子任务：单状态环 + 文件全宽，不渲染 0 项计数和空子任务卡。
 * 技术字段与原始 JSON 收在任务名旁的技术信息弹层。
 */

import { Banner, Button, Skeleton, Typography } from '@douyinfe/semi-ui';
import { useState, type ReactNode } from 'react';
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
import { CompactTaskArtifactGrid } from './TaskArtifactList';
import { TaskInfoButton, TaskInfoPopover } from './TaskInfoPopover';
import { TaskResultSection } from './TaskResultSection';
import { formatResult, resultText } from './taskResult';

export interface BackgroundTaskDetailPanelProps {
  state: BackgroundTaskDetailState;
  downloads: ArtifactDownloadState;
  agents?: readonly AgentOption[];
  onBack(): void;
  onBackToParent(parentId: string): void;
  onRefresh(): void;
  onChildPage(page: number): void;
  onSelectChild(taskId: string): void;
  onClearChild(): void;
  onRetryChild(): void;
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

function durationLabel(t: (key: string, options?: Record<string, unknown>) => string, detail: ExecutionDetail): string {
  if (detail.started_at === null || detail.finished_at === null) {
    return t('chat.taskCenter.background.fact.running');
  }
  const ms = new Date(detail.finished_at).getTime() - new Date(detail.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return t('chat.taskCenter.detail.noValue');
  if (ms < 1000) return t('chat.taskCenter.run.durationLessThanSecond');
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return t('chat.taskCenter.run.durationSeconds', { seconds });
  return t('chat.taskCenter.run.durationMinutes', {
    minutes: Math.floor(seconds / 60),
    seconds: seconds % 60
  });
}

function deliveryTargets(
  t: (key: string, options?: Record<string, unknown>) => string,
  detail: ExecutionDetail
): string {
  const labels = new Map<string, string | null>();
  for (const artifact of detail.result_artifacts) {
    for (const delivery of artifact.deliveries ?? []) {
      labels.set(delivery.target_key, delivery.target_label ?? null);
    }
  }
  if (labels.size === 0) return t('chat.taskCenter.detail.keepInTask');
  const head =
    [...labels.values()].find((label) => label !== null && label !== '') ??
    t('chat.taskCenter.detail.delivery.PUSH');
  return labels.size > 1 ? `${head} · ${labels.size}` : head;
}

function TechInfo(props: { detail: ExecutionDetail; agents?: readonly AgentOption[] }) {
  const { t } = useTranslation();
  const detail = props.detail;
  const raw = formatResult(detail.result);
  const name = agentName(props.agents, detail.agent_id);
  return (
    <>
      <dl className="chat-task-fact-list">
        <Fact label={t('chat.taskCenter.background.fact.agent')} testId="chat-background-fact-agent">
          {name ?? t('chat.taskCenter.background.fact.unknown')}
        </Fact>
        <Fact label={t('chat.taskCenter.background.fact.taskType')} testId="chat-background-fact-type">
          <TaskTypeTag taskType={detail.task_type} />
        </Fact>
        <Fact label={t('chat.taskCenter.background.fact.createdAt')} testId="chat-background-fact-created">
          {detail.started_at === null ? (
            t('chat.taskCenter.detail.noValue')
          ) : (
            <DateTimeText value={detail.started_at} />
          )}
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
        <Fact label={t('chat.taskCenter.background.fact.children')} testId="chat-background-fact-children">
          {detail.children.total > 0
            ? `${detail.children.total}`
            : t('chat.taskCenter.background.fact.none')}
        </Fact>
        <Fact label={t('chat.taskCenter.detail.deliveryTargets')} testId="chat-background-fact-delivery">
          {deliveryTargets(t, detail)}
        </Fact>
      </dl>
      <details className="chat-background-tech-raw">
        <summary>{t('chat.taskCenter.background.result.raw')}</summary>
        <pre data-testid="chat-background-result-body">{raw}</pre>
      </details>
    </>
  );
}

function Hero(props: {
  detail: ExecutionDetail;
  agents?: readonly AgentOption[];
  techOpen: boolean;
  onToggleTech(): void;
  onCloseTech(): void;
  onDownloadAll(): void;
}) {
  const { t } = useTranslation();
  const detail = props.detail;
  const artifactTotal = Math.max(detail.artifact_total, detail.result_artifacts.length);
  return (
    <section className="chat-background-detail-hero" data-testid="chat-background-summary">
      <div className="chat-background-detail-hero-main">
        <div className="chat-background-detail-title-line">
          <Typography.Title heading={4}>{detail.skill_name}</Typography.Title>
          <TaskInfoButton
            open={props.techOpen}
            label={t('chat.taskCenter.detail.techInfo')}
            onToggle={props.onToggleTech}
          />
        </div>
        <p className="chat-background-detail-subtitle">
          <TaskTypeTag taskType={detail.task_type} />
          {' · '}
          {detail.children.total > 0
            ? t('chat.taskCenter.background.children.total', { count: detail.children.total })
            : t('chat.taskCenter.background.single.label')}
          {' · '}
          {detail.started_at === null ? (
            t('chat.taskCenter.background.fact.running')
          ) : (
            <DateTimeText value={detail.started_at} />
          )}
          {' · '}
          {durationLabel(t, detail)}
        </p>
        {detail.parent_id === null ? null : (
          <span className="chat-background-child-marker" data-testid="chat-background-child-marker">
            {t('chat.taskCenter.background.children.marker')}
          </span>
        )}
        {artifactTotal > 0 ? (
          <span className="chat-background-has-artifacts" data-testid="chat-background-has-artifacts">
            {t('chat.taskCenter.background.row.hasArtifacts')}
          </span>
        ) : null}
        <TaskInfoPopover
          open={props.techOpen}
          title={t('chat.taskCenter.detail.techInfo')}
          hint={t('chat.taskCenter.detail.techInfoHint')}
          testId="chat-background-tech"
          className="chat-background-tech-popover"
          onClose={props.onCloseTech}
        >
          <TechInfo detail={detail} agents={props.agents} />
        </TaskInfoPopover>
      </div>
      <div className="chat-background-detail-actions">
        <Button
          size="small"
          theme="light"
          disabled={detail.result_artifacts.length === 0}
          data-testid="chat-background-download-all"
          onClick={props.onDownloadAll}
        >
          {t('chat.taskCenter.detail.downloadAll')}
        </Button>
        <ExecutionStatusTag status={detail.status} />
        {detail.partial_failure ? <PartialFailureTag counts={detail.counts} /> : null}
      </div>
    </section>
  );
}

function FilesPanel(props: {
  detail: ExecutionDetail;
  downloads: ArtifactDownloadState;
  onDownloadArtifact(artifact: TaskArtifact): void;
}) {
  const { t } = useTranslation();
  const total = Math.max(props.detail.artifact_total, props.detail.result_artifacts.length);
  return (
    <section className="chat-background-panel" data-testid="chat-background-artifacts">
      <header className="chat-background-panel-head">
        <div>
          <h3>{t('chat.taskCenter.detail.runFiles')}</h3>
          <span className="chat-background-panel-hint">
            {t('chat.taskCenter.background.artifacts.total', { count: total })}
          </span>
        </div>
      </header>
      {props.detail.artifact_total > props.detail.result_artifacts.length ? (
        <p
          className="chat-background-artifacts-truncated"
          data-testid="chat-background-artifacts-truncated"
        >
          {t('chat.taskCenter.background.artifacts.truncated', {
            total: props.detail.artifact_total,
            shown: props.detail.result_artifacts.length
          })}
        </p>
      ) : null}
      <CompactTaskArtifactGrid
        artifacts={props.detail.result_artifacts}
        state={props.downloads}
        onDownload={props.onDownloadArtifact}
      />
    </section>
  );
}

function ChildResultPanel(props: {
  state: BackgroundTaskDetailState;
  downloads: ArtifactDownloadState;
  onDownloadArtifact(artifact: TaskArtifact): void;
  onRetry(): void;
  onClear(): void;
}) {
  const { t } = useTranslation();
  const selected =
    props.state.detail?.children.items.find(
      (item) => item.task_id === props.state.selectedChildId
    ) ?? null;
  const detail = props.state.childDetail;
  const result = detail === null ? '' : resultText(detail.result);
  return (
    <section
      className="chat-background-panel chat-background-inline"
      data-testid="chat-background-inline"
    >
      <header className="chat-background-panel-head">
        <div>
          <h3>
            {selected === null
              ? t('chat.taskCenter.background.inline.title')
              : `${selected.label} · ${t('chat.taskCenter.background.inline.title')}`}
          </h3>
          {selected === null ? null : (
            <span className="chat-background-inline-status">
              <ExecutionStatusTag status={selected.status} />
            </span>
          )}
        </div>
        <Button
          size="small"
          theme="borderless"
          data-testid="chat-background-inline-clear"
          onClick={props.onClear}
        >
          {t('chat.taskCenter.background.inline.parentFiles')}
        </Button>
      </header>
      {props.state.childPhase === 'LOADING' ? (
        <div data-testid="chat-background-inline-loading">
          <Skeleton placeholder={<Skeleton.Paragraph rows={3} />} loading />
        </div>
      ) : null}
      {props.state.childPhase === 'ERROR' ? (
        <div className="chat-task-center-error" data-testid="chat-background-inline-error">
          <Banner
            type="danger"
            closeIcon={null}
            description={t('chat.taskCenter.background.detailLoadFailed')}
          />
          <Button size="small" data-testid="chat-background-inline-retry" onClick={props.onRetry}>
            {t('chat.taskCenter.list.retry')}
          </Button>
          {props.state.childErrorCode === null ? null : (
            <span className="chat-task-center-error-code">
              {t('chat.taskCenter.error.codeSuffix', { code: props.state.childErrorCode })}
            </span>
          )}
        </div>
      ) : null}
      {props.state.childPhase === 'READY' && detail !== null ? (
        <>
          <div
            className={`chat-background-inline-result${
              detail.status === 'FAILED' ? ' is-failed' : ''
            }`}
            data-testid="chat-background-inline-result"
          >
            <strong>{t('chat.taskCenter.detail.runResult')}</strong>
            {detail.error_code === null ? null : (
              <p className="chat-background-inline-failure" role="alert">
                {t('chat.taskCenter.execution.failure', {
                  code: detail.error_code,
                  message: detail.error_message ?? ''
                })}
              </p>
            )}
            <p>
              {result === ''
                ? t('chat.taskCenter.execution.noResult')
                : result.length > 600
                  ? `${result.slice(0, 600)}…`
                  : result}
            </p>
          </div>
          <h4 className="chat-background-inline-title">
            {t('chat.taskCenter.detail.runFiles')}
            <span className="chat-background-panel-hint">
              {t('chat.taskCenter.background.artifacts.total', {
                count: Math.max(detail.artifact_total, detail.result_artifacts.length)
              })}
            </span>
          </h4>
          <CompactTaskArtifactGrid
            artifacts={detail.result_artifacts}
            state={props.downloads}
            onDownload={props.onDownloadArtifact}
          />
        </>
      ) : null}
    </section>
  );
}

export function BackgroundTaskDetailPanel(props: BackgroundTaskDetailPanelProps) {
  const { t } = useTranslation();
  const { state } = props;
  const [techOpen, setTechOpen] = useState(false);
  const detail = state.detail;
  const notFound = isNotFoundCode(state.errorCode) && detail === null;
  const firstLoad = state.phase === 'LOADING' && detail === null;
  const showError = state.errorCode !== null && !notFound;
  const parentId = detail?.parent_id ?? null;
  const hasChildren = (detail?.children.total ?? 0) > 0 && detail?.parent_id === null;
  const hasArtifacts =
    (detail?.artifact_total ?? 0) > 0 || (detail?.result_artifacts.length ?? 0) > 0;

  const downloadAll = () => {
    for (const artifact of detail?.result_artifacts ?? []) {
      props.onDownloadArtifact(artifact);
    }
  };

  const locateFailedChild = () => {
    if (typeof document === 'undefined') return;
    const target =
      document.querySelector(
        '[data-testid="chat-background-children"] [data-testid="chat-background-child-row"][data-status="FAILED"]'
      ) ?? document.querySelector('[data-testid="chat-background-children"]');
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  return (
    <section
      className="chat-task-center chat-task-center-detail chat-background-detail-v2"
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
        <Typography.Title heading={5}>
          {t('chat.taskCenter.background.detailTitle')}
        </Typography.Title>
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
          <Hero
            detail={detail}
            agents={props.agents}
            techOpen={techOpen}
            onToggleTech={() => setTechOpen((open) => !open)}
            onCloseTech={() => setTechOpen(false)}
            onDownloadAll={downloadAll}
          />
          <TaskResultSection detail={detail} onLocateFailedChild={locateFailedChild} />
          {hasArtifacts || hasChildren ? (
            <div className={`chat-background-body-split${hasChildren ? '' : ' is-single'}`}>
              {hasChildren ? (
                <ChildTaskTable
                  items={detail.children.items}
                  page={detail.children.page}
                  pageSize={detail.children.page_size}
                  total={detail.children.total}
                  phase={state.childrenPhase}
                  errorCode={state.childrenErrorCode}
                  selectedChildId={state.selectedChildId}
                  onPage={props.onChildPage}
                  onSelectChild={props.onSelectChild}
                  onRetry={() => props.onChildPage(detail.children.page)}
                />
              ) : null}
              <div className="chat-background-result-pane" data-testid="chat-background-result-pane">
                {hasChildren && state.selectedChildId !== null ? (
                  <ChildResultPanel
                    state={state}
                    downloads={props.downloads}
                    onDownloadArtifact={props.onDownloadArtifact}
                    onRetry={props.onRetryChild}
                    onClear={props.onClearChild}
                  />
                ) : hasArtifacts ? (
                  <FilesPanel
                    detail={detail}
                    downloads={props.downloads}
                    onDownloadArtifact={props.onDownloadArtifact}
                  />
                ) : (
                  <p className="chat-background-inline-empty" data-testid="chat-background-inline-empty">
                    {t('chat.taskCenter.background.inline.select')}
                  </p>
                )}
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
