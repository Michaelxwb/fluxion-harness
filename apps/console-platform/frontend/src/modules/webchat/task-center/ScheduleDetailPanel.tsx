/**
 * 任务详情（design frontend §3.3 CMP-07/08/09/10/16、§3.6；TASK-027）。
 *
 * 三个事实分区，互不冒充：
 *
 * - **Schedule 摘要**：模式/状态/结束原因/时区/下次触发/配置版本与 Revision/依赖（AGENTIC 即
 *   工具 allowlist）/投递意图/预算/只读记忆。**不展示** `instruction`/`normalized_goal`/`context`
 *   ——那是 Prompt 与内部参数，不是页面事实。
 * - **Fire 历史**：真实触发台账；`SKIPPED` 显示跳过原因与 misses，`FAILED_ADMISSION` 显示
 *   可执行的修复动作；DST 只作为**独立事件**展示，绝不伪造一条 Fire。
 * - **Execution**：状态/三类 wait_kind/结果/错误/产物；等待不是失败。
 *
 * 时间统一经 `DateTimeText`；未知状态码保留原始 code 并给通用文案。
 */

import { Banner, Button, Empty, Skeleton, Typography } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { PaginationFooter } from '../../../components/common/PaginationFooter';
import { budgetEntries } from '../components/DraftCard';
import type {
  DstEvent,
  FireItem,
  HistoryRetention,
  ScheduleExecutionDetail,
  TaskArtifact
} from '../types';
import type { ArtifactDownloadState } from './artifactDownload';
import {
  endReasonLabel,
  ExecutionStatusTag,
  FireStatusTag,
  ModeTag,
  ScheduleStatusTag,
  waitKindLabel
} from './labels';
import {
  isNotFoundCode,
  type ExecutionState,
  type FireHistoryState,
  type ScheduleDetailState
} from './taskCenterState';
import { ScheduleActionsBar } from './ScheduleActionsBar';
import { formatBytes, TaskArtifactList } from './TaskArtifactList';

type Translate = (key: string, options?: Record<string, unknown>) => string;

const SKIP_REASON_KEYS: Record<string, string> = {
  OVERLAP: 'chat.taskCenter.fire.skipReason.OVERLAP',
  MISFIRE: 'chat.taskCenter.fire.skipReason.MISFIRE',
  QUOTA: 'chat.taskCenter.fire.skipReason.QUOTA',
  PAUSED_BY_UPDATE: 'chat.taskCenter.fire.skipReason.PAUSED_BY_UPDATE'
};

const DELIVERY_KEYS: Record<string, string> = {
  PUSH: 'chat.taskCenter.detail.delivery.PUSH',
  NONE: 'chat.taskCenter.detail.delivery.NONE'
};

const DST_EVENT_KEYS: Record<string, string> = {
  DST_GAP_SKIPPED: 'chat.taskCenter.fire.dstEvent.DST_GAP_SKIPPED',
  DST_FOLD_SECOND_SKIPPED: 'chat.taskCenter.fire.dstEvent.DST_FOLD_SECOND_SKIPPED'
};

/** 结果只做文本化：对象 JSON 化，`null` 显示占位，不解析语义。 */
export function formatResult(result: Record<string, unknown> | null): string {
  if (result === null) return '';
  try {
    return JSON.stringify(result, null, 2);
  } catch (error) {
    return String(result);
  }
}

function skipReasonLabel(t: Translate, reason: string): string {
  const key = SKIP_REASON_KEYS[reason];
  return key === undefined ? t('chat.taskCenter.unknownCode', { code: reason }) : t(key);
}

function deliveryLabel(t: Translate, delivery: string): string {
  const key = DELIVERY_KEYS[delivery];
  return key === undefined ? t('chat.taskCenter.unknownCode', { code: delivery }) : t(key);
}

function Fact(props: { label: string; testId: string; children: ReactNode }) {
  return (
    <div className="chat-task-fact">
      <dt>{props.label}</dt>
      <dd data-testid={props.testId}>{props.children}</dd>
    </div>
  );
}

function ScheduleSummary(props: { state: ScheduleDetailState }) {
  const { t } = useTranslation();
  const detail = props.state.detail;
  if (detail === null) return null;
  const budget = detail.budget === null ? [] : budgetEntries(detail.budget);
  const dependenciesLabel =
    detail.mode === 'AGENTIC'
      ? t('chat.taskCenter.detail.allowlist')
      : t('chat.taskCenter.detail.dependencies');
  return (
    <section className="chat-task-summary" data-testid="chat-task-center-summary">
      <header className="chat-task-summary-header">
        <Typography.Title heading={6}>{detail.name}</Typography.Title>
        <ModeTag mode={detail.mode} />
        <ScheduleStatusTag status={detail.status} />
        {detail.status === 'ENDED' && detail.end_reason !== null ? (
          <span data-testid="chat-task-center-end-reason">
            {endReasonLabel(t, detail.end_reason)}
          </span>
        ) : null}
      </header>
      {detail.attention_reason === null ? null : (
        <p className="chat-task-attention" data-testid="chat-task-center-attention" role="alert">
          {t('chat.taskCenter.detail.attention', { reason: detail.attention_reason })}
        </p>
      )}
      <dl className="chat-task-fact-list">
        <Fact label={t('chat.taskCenter.detail.timezone')} testId="chat-task-center-timezone">
          {detail.timezone}
        </Fact>
        <Fact label={t('chat.taskCenter.detail.nextFire')} testId="chat-task-center-next-fire">
          {detail.next_fire_at === null ? (
            t('chat.taskCenter.list.noNextFire')
          ) : (
            <DateTimeText value={detail.next_fire_at} />
          )}
        </Fact>
        <Fact label={t('chat.taskCenter.detail.configVersion')} testId="chat-task-center-config-version">
          {detail.config_version}
        </Fact>
        <Fact label={t('chat.taskCenter.detail.revision')} testId="chat-task-center-revision">
          {detail.revision_no}
        </Fact>
        <Fact label={dependenciesLabel} testId="chat-task-center-dependencies">
          {detail.dependencies.length === 0 ? (
            t('chat.taskCenter.detail.noDependencies')
          ) : (
            detail.dependencies.join(', ')
          )}
        </Fact>
        <Fact label={t('chat.taskCenter.detail.delivery')} testId="chat-task-center-delivery">
          {deliveryLabel(t, detail.delivery_intent)}
        </Fact>
        {budget.length === 0 ? null : (
          <Fact label={t('chat.taskCenter.detail.budget')} testId="chat-task-center-budget">
            {budget.map((item) => `${item.key}: ${item.value}`).join(' · ')}
          </Fact>
        )}
        {detail.memory_mode === null ? null : (
          <Fact label={t('chat.taskCenter.detail.memory')} testId="chat-task-center-memory">
            {t('chat.taskCenter.detail.memoryReadOnly')}
          </Fact>
        )}
      </dl>
    </section>
  );
}

function DstEventRow(props: { event: DstEvent }) {
  const { t } = useTranslation();
  const key = DST_EVENT_KEYS[props.event.event_type];
  return (
    <li className="chat-task-dst-event" data-testid="chat-task-dst-event">
      {key === undefined
        ? t('chat.taskCenter.unknownCode', { code: props.event.event_type })
        : t(key)}{' '}
      · {props.event.local_date} {props.event.local_time} {props.event.timezone} ·{' '}
      <DateTimeText value={props.event.occurred_at} />
    </li>
  );
}

function FireRow(props: { fire: FireItem; onOpenExecution(executionId: string): void }) {
  const { t } = useTranslation();
  const fire = props.fire;
  const executionId = fire.execution_id;
  return (
    <li className="chat-task-fire-row" data-testid="chat-task-fire-row" data-status={fire.status}>
      <span className="chat-task-fire-time">
        <DateTimeText value={fire.scheduled_at} />
      </span>
      <FireStatusTag status={fire.status} />
      {fire.status === 'SKIPPED' ? (
        <span className="chat-task-fire-skip" data-testid="chat-task-fire-skip-reason">
          {fire.skip_reason === null
            ? t('chat.taskCenter.fire.skipReasonUnknown')
            : skipReasonLabel(t, fire.skip_reason)}
        </span>
      ) : null}
      {fire.status === 'FAILED_ADMISSION' ? (
        <span className="chat-task-fire-repair" data-testid="chat-task-fire-repair" role="alert">
          {t('chat.taskCenter.fire.admissionRepair')}
          {fire.admission_error_code === null ? null : ` (${fire.admission_error_code})`}
        </span>
      ) : null}
      {fire.missed_count > 0 ? (
        <span className="chat-task-fire-missed" data-testid="chat-task-fire-missed">
          {t('chat.taskCenter.fire.missedCount', { count: fire.missed_count })}
        </span>
      ) : null}
      {fire.dst_resolution === null ? null : (
        <span className="chat-task-fire-dst" data-testid="chat-task-fire-dst">
          {t('chat.taskCenter.fire.dstResolution')}
        </span>
      )}
      {executionId === null ? (
        <span className="chat-task-fire-no-execution" data-testid="chat-task-fire-no-execution">
          {t('chat.taskCenter.fire.noExecution')}
        </span>
      ) : (
        <span className="chat-task-fire-execution">
          {fire.execution_status === null ? null : (
            <ExecutionStatusTag status={fire.execution_status} />
          )}
          <Button
            size="small"
            theme="borderless"
            data-testid="chat-task-fire-execution-link"
            aria-label={t('chat.taskCenter.fire.openExecution')}
            onClick={() => props.onOpenExecution(executionId)}
          >
            {t('chat.taskCenter.fire.openExecution')}
          </Button>
        </span>
      )}
    </li>
  );
}

function FireHistory(props: {
  fires: FireHistoryState;
  onPage(page: number): void;
  onOpenExecution(executionId: string): void;
  onRetryFires(page: number): void;
}) {
  const { t } = useTranslation();
  const { fires } = props;
  return (
    <section className="chat-task-fires" data-testid="chat-task-center-fires">
      <header className="chat-task-section-header">
        <Typography.Title heading={6}>{t('chat.taskCenter.fire.title')}</Typography.Title>
      </header>
      {fires.errorCode !== null ? (
        <div className="chat-task-center-error" data-testid="chat-task-fire-error">
          <Banner type="danger" closeIcon={null} description={t('chat.taskCenter.fire.loadFailed')} />
          <Button
            size="small"
            data-testid="chat-task-fire-retry"
            onClick={() => props.onRetryFires(fires.page)}
          >
            {t('chat.taskCenter.list.retry')}
          </Button>
          <span className="chat-task-center-error-code">
            {t('chat.taskCenter.error.codeSuffix', { code: fires.errorCode })}
          </span>
        </div>
      ) : null}
      {fires.items.length === 0 && fires.phase === 'READY' ? (
        <Empty description={t('chat.taskCenter.fire.empty')} />
      ) : null}
      {fires.items.length > 0 ? (
        <ul className="chat-task-fire-list" data-testid="chat-task-fire-list" role="list">
          {fires.items.map((fire) => (
            <FireRow key={fire.fire_id} fire={fire} onOpenExecution={props.onOpenExecution} />
          ))}
        </ul>
      ) : null}
      {fires.items.length > 0 && fires.pageSize > 0 && fires.total > fires.pageSize ? (
        <PaginationFooter
          page={fires.page}
          pageSize={fires.pageSize}
          total={fires.total}
          onPageChange={props.onPage}
        />
      ) : null}
      <DstEventList events={fires.dstEvents} retention={fires.retention} />
    </section>
  );
}

function DstEventList(props: { events: readonly DstEvent[]; retention: HistoryRetention | null }) {
  const { t } = useTranslation();
  return (
    <div className="chat-task-dst" data-testid="chat-task-center-dst-events">
      <span className="chat-task-section-label">{t('chat.taskCenter.fire.dstEvents')}</span>
      {props.events.length === 0 ? (
        <p data-testid="chat-task-dst-empty">{t('chat.taskCenter.fire.dstEmpty')}</p>
      ) : (
        <ul>
          {props.events.map((event) => (
            <DstEventRow key={event.event_id} event={event} />
          ))}
        </ul>
      )}
      {props.retention === null ? null : (
        <p className="chat-task-retention" data-testid="chat-task-retention">
          {t('chat.taskCenter.fire.retention', {
            from: props.retention.cleaned_before
          })}
        </p>
      )}
    </div>
  );
}

/** 等待是状态而不是失败：三类 wait_kind 各有明确文案，WAITING_HUMAN 也不显示为失败。 */
function ExecutionWait(props: { detail: ScheduleExecutionDetail }) {
  const { t } = useTranslation();
  const detail = props.detail;
  if (detail.status === 'WAITING_HUMAN') {
    return (
      <p className="chat-task-execution-wait" data-testid="chat-task-execution-wait">
        {t('chat.taskCenter.execution.waitingHuman')}
      </p>
    );
  }
  if (detail.status !== 'WAITING_EXTERNAL') return null;
  return (
    <p className="chat-task-execution-wait" data-testid="chat-task-execution-wait">
      {detail.wait_kind === null
        ? t('chat.taskCenter.execution.waitingExternal')
        : t('chat.taskCenter.execution.waitingKind', {
            kind: waitKindLabel(t, detail.wait_kind)
          })}
    </p>
  );
}

function ExecutionBody(props: {
  detail: ScheduleExecutionDetail;
  downloads: ArtifactDownloadState;
  onDownloadArtifact(artifact: TaskArtifact): void;
}) {
  const { t } = useTranslation();
  const detail = props.detail;
  const result = formatResult(detail.result);
  return (
    <div className="chat-task-execution-body" data-testid="chat-task-execution-body">
      <div className="chat-task-execution-status">
        <ExecutionStatusTag status={detail.status} />
        <ExecutionWait detail={detail} />
      </div>
      <dl className="chat-task-fact-list">
        <Fact label={t('chat.taskCenter.execution.startedAt')} testId="chat-task-execution-started">
          {detail.started_at === null ? '-' : <DateTimeText value={detail.started_at} />}
        </Fact>
        <Fact label={t('chat.taskCenter.execution.finishedAt')} testId="chat-task-execution-finished">
          {detail.finished_at === null ? '-' : <DateTimeText value={detail.finished_at} />}
        </Fact>
        <Fact label={t('chat.taskCenter.execution.snapshotSchema')} testId="chat-task-execution-schema">
          {detail.snapshot_schema_version}
        </Fact>
        <Fact label={t('chat.taskCenter.execution.snapshotBytes')} testId="chat-task-execution-snapshot-size">
          {formatBytes(detail.snapshot_bytes)}
        </Fact>
      </dl>
      {detail.error_code === null ? null : (
        <div className="chat-task-execution-error" data-testid="chat-task-execution-error" role="alert">
          {t('chat.taskCenter.execution.failure', {
            code: detail.error_code,
            message: detail.error_message ?? ''
          })}
          {detail.failure_class === null ? null : (
            <span className="chat-task-execution-failure-class">
              {t('chat.taskCenter.execution.failureClass', { kind: detail.failure_class })}
            </span>
          )}
        </div>
      )}
      <details className="chat-task-execution-result" data-testid="chat-task-execution-result">
        <summary>{t('chat.taskCenter.execution.result')}</summary>
        {result === '' ? (
          <p>{t('chat.taskCenter.execution.noResult')}</p>
        ) : (
          <pre className="chat-task-execution-result-body">{result}</pre>
        )}
      </details>
      <section className="chat-task-execution-artifacts" data-testid="chat-task-execution-artifacts">
        <Typography.Title heading={6}>{t('chat.taskCenter.artifact.title')}</Typography.Title>
        <TaskArtifactList
          artifacts={detail.result_artifacts}
          state={props.downloads}
          onDownload={props.onDownloadArtifact}
        />
      </section>
    </div>
  );
}

function ExecutionPanel(props: {
  execution: ExecutionState;
  downloads: ArtifactDownloadState;
  onRetry(): void;
  onClose(): void;
  onDownloadArtifact(artifact: TaskArtifact): void;
}) {
  const { t } = useTranslation();
  if (props.execution.phase === 'IDLE') return null;
  return (
    <section className="chat-task-execution" data-testid="chat-task-execution">
      <header className="chat-task-section-header">
        <Typography.Title heading={6}>{t('chat.taskCenter.execution.title')}</Typography.Title>
        <Button
          size="small"
          theme="borderless"
          data-testid="chat-task-execution-close"
          aria-label={t('chat.taskCenter.execution.close')}
          onClick={props.onClose}
        >
          {t('chat.taskCenter.execution.close')}
        </Button>
      </header>
      {props.execution.phase === 'LOADING' ? (
        <Skeleton placeholder={<Skeleton.Paragraph rows={3} />} loading />
      ) : null}
      {props.execution.phase === 'ERROR' ? (
        <div className="chat-task-center-error" data-testid="chat-task-execution-load-error">
          <Banner type="danger" closeIcon={null} description={t('chat.taskCenter.execution.loadFailed')} />
          <Button size="small" data-testid="chat-task-execution-retry" onClick={props.onRetry}>
            {t('chat.taskCenter.list.retry')}
          </Button>
          <span className="chat-task-center-error-code">
            {t('chat.taskCenter.error.codeSuffix', { code: props.execution.errorCode })}
          </span>
        </div>
      ) : null}
      {props.execution.phase === 'READY' && props.execution.detail !== null ? (
        <ExecutionBody
          detail={props.execution.detail}
          downloads={props.downloads}
          onDownloadArtifact={props.onDownloadArtifact}
        />
      ) : null}
    </section>
  );
}

export interface ScheduleDetailPanelProps {
  state: ScheduleDetailState;
  downloads: ArtifactDownloadState;
  onBack(): void;
  onRefresh(): void;
  onFirePage(page: number): void;
  onOpenExecution(executionId: string): void;
  onCloseExecution(): void;
  onDownloadArtifact(artifact: TaskArtifact): void;
  /** 生命周期动作条（TASK-028）：状态与回调都由页面注入。 */
  actions?: {
    state: { submitting: 'pause' | 'resume' | 'cancel' | null; errorCode: string | null };
    onAction(action: 'pause' | 'resume' | 'cancel', scope?: 'FUTURE_ONLY' | 'ALL_ACTIVE'): void;
    onRecreate(): void;
    onEdit(): void;
  };
  /** 从详情跳到待处理视图（同一 Agent 范围）。 */
  onOpenPending?(): void;
}

export function ScheduleDetailPanel(props: ScheduleDetailPanelProps) {
  const { t } = useTranslation();
  const { state } = props;
  const notFound = isNotFoundCode(state.errorCode) && state.detail === null;
  const firstLoad = state.phase === 'LOADING' && state.detail === null;
  return (
    <section
      className="chat-task-center chat-task-center-detail"
      data-testid="webchat-task-center"
      data-view="detail"
      data-schedule-id={state.scheduleId ?? ''}
      aria-label={t('chat.taskCenter.detail.label')}
    >
      {props.actions && state.detail ? (
        <ScheduleActionsBar
          status={state.detail.status}
          mode={state.detail.mode}
          submitting={props.actions.state.submitting}
          errorCode={props.actions.state.errorCode}
          onAction={props.actions.onAction}
          onRecreate={props.actions.onRecreate}
          onEdit={props.actions.onEdit}
        />
      ) : null}
      <header className="chat-task-center-header">
        <Button
          size="small"
          theme="borderless"
          data-testid="chat-task-center-back"
          aria-label={t('chat.taskCenter.detail.back')}
          onClick={props.onBack}
        >
          {t('chat.taskCenter.detail.back')}
        </Button>
        <Typography.Title heading={5}>{t('chat.taskCenter.detail.title')}</Typography.Title>
        <Button
          size="small"
          theme="borderless"
          data-testid="chat-task-center-detail-refresh"
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
      {state.errorCode !== null && !notFound ? (
        <div className="chat-task-center-error" data-testid="chat-task-center-error">
          <Banner type="danger" closeIcon={null} description={t('chat.taskCenter.error.detail')} />
          <Button size="small" data-testid="chat-task-center-retry" onClick={props.onRefresh}>
            {t('chat.taskCenter.list.retry')}
          </Button>
          <span className="chat-task-center-error-code">
            {t('chat.taskCenter.error.codeSuffix', { code: state.errorCode })}
          </span>
        </div>
      ) : null}
      {firstLoad ? (
        <div className="chat-task-center-loading" data-testid="chat-task-center-loading">
          <Skeleton placeholder={<Skeleton.Paragraph rows={4} />} loading />
        </div>
      ) : null}
      {state.detail === null ? null : (
        <>
          <ScheduleSummary state={state} />
          <FireHistory
            fires={state.fires}
            onPage={props.onFirePage}
            onOpenExecution={props.onOpenExecution}
            onRetryFires={props.onFirePage}
          />
          <ExecutionPanel
            execution={state.execution}
            downloads={props.downloads}
            onRetry={props.onRefresh}
            onClose={props.onCloseExecution}
            onDownloadArtifact={props.onDownloadArtifact}
          />
        </>
      )}
    </section>
  );
}
