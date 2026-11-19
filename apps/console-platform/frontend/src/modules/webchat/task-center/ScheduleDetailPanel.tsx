/**
 * 定时任务详情（改版：信息分层 + 等高两栏 + 运行联动）。
 *
 * 首屏只回答：下次何时运行、最近一次怎么样、结果发到哪里、运行了多少次。
 * 技术字段（时区/Revision/快照/依赖等）收在任务名旁的技术信息弹层，默认关闭。
 * 运行列表与详情共用同一份 Fire/Execution 事实；等待不是失败，跳过/准入失败不请求执行详情。
 */

import { IconInfoCircle } from '@douyinfe/semi-icons';
import { Banner, Button, Empty, Skeleton, Typography } from '@douyinfe/semi-ui';
import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { PaginationFooter } from '../../../components/common/PaginationFooter';
import { budgetEntries } from '../components/DraftCard';
import type {
  DstEvent,
  ExecutionDetail,
  FireItem,
  HistoryRetention,
  TaskArtifact,
  TaskArtifactDelivery
} from '../types';
import type { ArtifactDownloadState } from './artifactDownload';
import {
  endReasonLabel,
  RunStatusTag,
  runDisplayStatus,
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
import { formatBytes } from './TaskArtifactList';
import { formatResult } from './taskResult';

type Translate = (key: string, options?: Record<string, unknown>) => string;

const SKIP_REASON_KEYS: Record<string, string> = {
  OVERLAP: 'chat.taskCenter.fire.skipReason.OVERLAP',
  MISFIRE: 'chat.taskCenter.fire.skipReason.MISFIRE',
  QUOTA: 'chat.taskCenter.fire.skipReason.QUOTA',
  PAUSED_BY_UPDATE: 'chat.taskCenter.fire.skipReason.PAUSED_BY_UPDATE'
};

const DST_EVENT_KEYS: Record<string, string> = {
  DST_GAP_SKIPPED: 'chat.taskCenter.fire.dstEvent.DST_GAP_SKIPPED',
  DST_FOLD_SECOND_SKIPPED: 'chat.taskCenter.fire.dstEvent.DST_FOLD_SECOND_SKIPPED'
};

function skipReasonLabel(t: Translate, reason: string): string {
  const key = SKIP_REASON_KEYS[reason];
  return key === undefined ? t('chat.taskCenter.unknownCode', { code: reason }) : t(key);
}

function Fact(props: { label: string; testId: string; children: ReactNode }) {
  return (
    <div className="chat-task-fact">
      <dt>{props.label}</dt>
      <dd data-testid={props.testId}>{props.children}</dd>
    </div>
  );
}

function durationText(t: Translate, ms: number | null): string {
  if (ms === null) return t('chat.taskCenter.detail.noValue');
  if (ms < 1000) return t('chat.taskCenter.run.durationLessThanSecond');
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) {
    return t('chat.taskCenter.run.durationSeconds', { seconds: totalSeconds });
  }
  return t('chat.taskCenter.run.durationMinutes', {
    minutes: Math.floor(totalSeconds / 60),
    seconds: totalSeconds % 60
  });
}

function resultText(result: Record<string, unknown> | null): string {
  if (result === null) return '';
  const text = result.text;
  if (typeof text === 'string') return text;
  return formatResult(result);
}

type FileKind = 'spreadsheet' | 'csv' | 'json' | 'text' | 'other';

const FILE_KIND_KEYS: Record<FileKind, string> = {
  spreadsheet: 'chat.taskCenter.artifact.fileType.spreadsheet',
  csv: 'chat.taskCenter.artifact.fileType.csv',
  json: 'chat.taskCenter.artifact.fileType.json',
  text: 'chat.taskCenter.artifact.fileType.text',
  other: 'chat.taskCenter.artifact.fileType.other'
};

function fileKind(artifact: TaskArtifact): FileKind {
  const media = artifact.media_type.toLowerCase();
  if (media.includes('spreadsheetml')) return 'spreadsheet';
  if (media.includes('csv')) return 'csv';
  if (media.includes('json')) return 'json';
  if (media.startsWith('text/')) return 'text';
  return 'other';
}

type DeliveryAggregate = 'delivered' | 'failed' | 'partial' | 'pending' | null;

function deliveryAggregate(artifact: TaskArtifact): DeliveryAggregate {
  const deliveries = artifact.deliveries;
  if (deliveries === undefined || deliveries.length === 0) return null;
  if (deliveries.some((item) => item.status === 'FAILED')) return 'failed';
  if (deliveries.some((item) => item.status === 'DEGRADED')) return 'partial';
  if (deliveries.every((item) => item.status === 'DELIVERED')) return 'delivered';
  return 'pending';
}

function deliveryAggregateLabel(t: Translate, aggregate: DeliveryAggregate): string | null {
  if (aggregate === null) return null;
  if (aggregate === 'delivered') return t('chat.taskCenter.artifact.delivered');
  if (aggregate === 'failed') return t('chat.taskCenter.artifact.deliveryFailed');
  if (aggregate === 'partial') return t('chat.taskCenter.artifact.partiallyDelivered');
  return t('chat.taskCenter.artifact.pending');
}

function uniqueDeliveries(artifacts: readonly TaskArtifact[]): TaskArtifactDelivery[] {
  const map = new Map<string, TaskArtifactDelivery>();
  for (const artifact of artifacts) {
    for (const delivery of artifact.deliveries ?? []) {
      map.set(delivery.target_key, delivery);
    }
  }
  return [...map.values()];
}

function deliveryTargetLabel(delivery: TaskArtifactDelivery): string {
  return delivery.target_label ?? '—';
}

function RunFileList(props: {
  artifacts: readonly TaskArtifact[];
  downloads: ArtifactDownloadState;
  onDownload(artifact: TaskArtifact): void;
}) {
  const { t } = useTranslation();
  if (props.artifacts.length === 0) {
    return <p className="chat-task-artifacts-empty">{t('chat.taskCenter.artifact.empty')}</p>;
  }
  return (
    <ul className="chat-task-run-files" data-testid="chat-task-artifacts">
      {props.artifacts.map((artifact) => {
        const aggregate = deliveryAggregate(artifact);
        const aggregateLabel = deliveryAggregateLabel(t, aggregate);
        const entry = props.downloads.entries[artifact.artifact_id];
        const kind = t(FILE_KIND_KEYS[fileKind(artifact)]);
        return (
          <li
            key={`${artifact.artifact_id}:${artifact.version}`}
            className="chat-task-run-file"
            data-testid="chat-task-artifact"
            data-artifact-id={artifact.artifact_id}
            data-artifact-version={artifact.version}
            data-artifact-checksum={artifact.checksum}
          >
            <span className="chat-task-run-file-kind">{kind}</span>
            <div className="chat-task-run-file-main">
              <strong title={artifact.name}>{artifact.name}</strong>
              <span className="chat-task-run-file-meta">
                {kind} · {formatBytes(artifact.size)}
                {aggregateLabel === null ? null : (
                  <span
                    className={`chat-task-run-file-delivery is-${aggregate ?? 'none'}`}
                    data-testid="chat-task-artifact-delivery"
                  >
                    {aggregateLabel}
                  </span>
                )}
              </span>
              {/* 下载仍需精确版本与校验值；技术事实保留在 DOM，不在首屏展示。 */}
              <span hidden>
                {artifact.media_type} · v{artifact.version} · {artifact.checksum}
              </span>
            </div>
            <Button
              size="small"
              theme="borderless"
              data-testid="chat-task-artifact-download"
              aria-label={t('chat.taskCenter.artifact.download', { name: artifact.name })}
              loading={entry?.phase === 'DOWNLOADING'}
              onClick={() => props.onDownload(artifact)}
            >
              {t('chat.taskCenter.artifact.downloadShort')}
            </Button>
          </li>
        );
      })}
    </ul>
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

/** 旧详情里的 DST/保留提示不再占首屏；保留在隐藏区，避免丢失事实。 */
function DstEventList(props: { events: readonly DstEvent[]; retention: HistoryRetention | null }) {
  const { t } = useTranslation();
  return (
    <div className="chat-task-dst" data-testid="chat-task-center-dst-events" hidden>
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
          {t('chat.taskCenter.fire.retention', { from: props.retention.cleaned_before })}
        </p>
      )}
    </div>
  );
}

function ExecutionWait(props: { detail: ExecutionDetail }) {
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

function executionAsFire(execution: ExecutionState): FireItem | null {
  if (execution.detail === null || execution.executionId === null) return null;
  return {
    fire_id: execution.executionId,
    scheduled_at: execution.detail.started_at ?? execution.detail.finished_at ?? '',
    status: 'EXECUTION_CREATED',
    skip_reason: null,
    missed_count: 0,
    dst_resolution: null,
    admission_error_code: null,
    execution_id: execution.executionId,
    execution_status: execution.detail.status,
    duration_ms: null,
    artifact_count: execution.detail.result_artifacts.length
  };
}

function RunDetail(props: {
  fire: FireItem | null;
  execution: ExecutionState;
  downloads: ArtifactDownloadState;
  onDownloadArtifact(artifact: TaskArtifact): void;
  onRetry(): void;
}) {
  const { t } = useTranslation();
  const fire = props.fire ?? executionAsFire(props.execution);
  if (fire === null) {
    return <Empty description={t('chat.taskCenter.detail.selectRun')} />;
  }
  const execution = props.execution;
  const waiting = execution.detail;
  const result = waiting === null ? '' : resultText(waiting.result);
  return (
    <section className="chat-task-execution" data-testid="chat-task-execution">
      <header className="chat-task-run-detail-head">
        <div>
          <Typography.Title heading={6}>{t('chat.taskCenter.detail.runTitle')}</Typography.Title>
          <span className="chat-task-run-detail-meta">
            <DateTimeText value={fire.scheduled_at} /> · {durationText(t, fire.duration_ms)}
          </span>
        </div>
        <RunStatusTag fire={fire} />
      </header>

      {execution.phase === 'LOADING' ? (
        <Skeleton placeholder={<Skeleton.Paragraph rows={4} />} loading />
      ) : null}
      {execution.phase === 'ERROR' ? (
        <div className="chat-task-center-error" data-testid="chat-task-execution-load-error">
          <Banner
            type="danger"
            closeIcon={null}
            description={t('chat.taskCenter.execution.loadFailed')}
          />
          <Button size="small" data-testid="chat-task-execution-retry" onClick={props.onRetry}>
            {t('chat.taskCenter.list.retry')}
          </Button>
          <span className="chat-task-center-error-code">
            {t('chat.taskCenter.error.codeSuffix', { code: execution.errorCode })}
          </span>
        </div>
      ) : null}
      {execution.phase === 'READY' && waiting !== null ? (
        <>
          <div
            className={`chat-task-run-result ${runDisplayStatus(fire) === 'FAILED' ? 'is-failed' : ''}`}
          >
            <strong>{t('chat.taskCenter.detail.runResult')}</strong>
            <p>
              {result === ''
                ? t('chat.taskCenter.execution.noResult')
                : result.length > 280
                  ? `${result.slice(0, 280)}…`
                  : result}
            </p>
            <ExecutionWait detail={waiting} />
          </div>
          {waiting.error_code === null ? null : (
            <div className="chat-task-execution-error" data-testid="chat-task-execution-error" role="alert">
              {t('chat.taskCenter.execution.failure', {
                code: waiting.error_code,
                message: waiting.error_message ?? ''
              })}
              {waiting.failure_class === null ? null : (
                <span className="chat-task-execution-failure-class">
                  {t('chat.taskCenter.execution.failureClass', {
                    kind: waiting.failure_class
                  })}
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
          <h3 className="chat-task-run-section-title">{t('chat.taskCenter.detail.runFiles')}</h3>
          <RunFileList
            artifacts={waiting.result_artifacts}
            downloads={props.downloads}
            onDownload={props.onDownloadArtifact}
          />
          <RunDeliveryList artifacts={waiting.result_artifacts} />
        </>
      ) : null}
      {execution.phase === 'READY' && waiting === null && fire.execution_id === null ? (
        <div className="chat-task-run-result is-muted">
          <strong>
            {fire.status === 'SKIPPED'
              ? skipReasonLabel(t, fire.skip_reason ?? '')
              : t('chat.taskCenter.run.status.NOT_RUN')}
          </strong>
          <p>{t('chat.taskCenter.fire.noExecution')}</p>
        </div>
      ) : null}
      <footer className="chat-task-run-detail-foot">
        <Button
          size="small"
          theme="light"
          disabled={waiting === null || waiting.result_artifacts.length === 0}
          onClick={() => {
            for (const artifact of waiting?.result_artifacts ?? []) {
              props.onDownloadArtifact(artifact);
            }
          }}
        >
          {t('chat.taskCenter.detail.downloadAll')}
        </Button>
      </footer>
    </section>
  );
}

function RunDeliveryList(props: { artifacts: readonly TaskArtifact[] }) {
  const { t } = useTranslation();
  const deliveries = uniqueDeliveries(props.artifacts);
  if (deliveries.length === 0) return null;
  return (
    <section className="chat-task-run-delivery" data-testid="chat-task-run-delivery">
      <h3 className="chat-task-run-section-title">{t('chat.taskCenter.detail.runDelivery')}</h3>
      <ul>
        {deliveries.slice(0, 3).map((delivery) => (
          <li key={delivery.target_key}>
            <span>{deliveryTargetLabel(delivery)}</span>
            <span
              className={
                delivery.status === 'DELIVERED'
                  ? 'is-delivered'
                  : delivery.status === 'FAILED'
                    ? 'is-failed'
                    : 'is-pending'
              }
            >
              {delivery.status === 'DELIVERED'
                ? t('chat.taskCenter.artifact.delivered')
                : delivery.status === 'FAILED'
                  ? t('chat.taskCenter.artifact.deliveryFailed')
                  : delivery.status === 'DEGRADED'
                    ? t('chat.taskCenter.artifact.partiallyDelivered')
                    : t('chat.taskCenter.artifact.pending')}
            </span>
          </li>
        ))}
      </ul>
      {deliveries.length > 3 ? (
        <p className="chat-task-run-delivery-more">
          {t('chat.taskCenter.detail.moreTargets', { count: deliveries.length - 3 })}
        </p>
      ) : null}
    </section>
  );
}

function MetricCard(props: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <article className="chat-task-metric">
      <span className="chat-task-metric-label">{props.label}</span>
      <strong className="chat-task-metric-value">{props.value}</strong>
      {props.note === undefined ? null : <span className="chat-task-metric-note">{props.note}</span>}
    </article>
  );
}

function ScheduleMetrics(props: { state: ScheduleDetailState }) {
  const { t } = useTranslation();
  const detail = props.state.detail;
  if (detail === null) return null;
  const last = detail.last_fire;
  const execution = props.state.execution.detail;
  const deliveries = execution === null ? [] : uniqueDeliveries(execution.result_artifacts);
  const delivered = deliveries.filter((item) => item.status === 'DELIVERED').length;
  const deliveryValue =
    deliveries.length > 0
      ? `${deliveries[0].target_label ?? t('chat.taskCenter.detail.deliveryTargets')} · ${deliveries.length}`
      : detail.delivery_intent === 'PUSH'
        ? t('chat.taskCenter.detail.delivery.PUSH')
        : t('chat.taskCenter.detail.keepInTask');
  const deliveryNote =
    deliveries.length === 0
      ? null
      : delivered === deliveries.length
        ? t('chat.taskCenter.detail.allDelivered')
        : t('chat.taskCenter.detail.partialDelivered');
  const recent = props.state.fires.items;
  const abnormal = recent.filter((fire) => runDisplayStatus(fire) !== 'SUCCESS').length;
  const runNote =
    recent.length === 0
      ? t('chat.taskCenter.detail.noRuns')
      : abnormal === 0
        ? t('chat.taskCenter.detail.allSuccess', { count: recent.length })
        : t('chat.taskCenter.detail.someFailed', {
            count: recent.length,
            failed: abnormal
          });
  return (
    <section className="chat-task-metrics" data-testid="chat-task-detail-metrics">
      <MetricCard
        label={t('chat.taskCenter.detail.nextRun')}
        value={
          detail.next_fire_at === null ? (
            t('chat.taskCenter.list.noNextFire')
          ) : (
            <DateTimeText value={detail.next_fire_at} />
          )
        }
      />
      <MetricCard
        label={t('chat.taskCenter.detail.lastRun')}
        value={
          last === null ? (
            t('chat.taskCenter.detail.noRuns')
          ) : (
            <>
              <DateTimeText value={last.scheduled_at} /> <RunStatusTag fire={last} />
            </>
          )
        }
        note={
          last === null
            ? undefined
            : `${durationText(t, last.duration_ms)} · ${t('chat.taskCenter.run.files', {
                count: last.artifact_count
              })}`
        }
      />
      <MetricCard
        label={t('chat.taskCenter.detail.deliveryTargets')}
        value={deliveryValue}
        note={deliveryNote ?? undefined}
      />
      <MetricCard
        label={t('chat.taskCenter.detail.runCount')}
        value={`${props.state.fires.total}`}
        note={runNote}
      />
    </section>
  );
}

function TechInfo(props: { state: ScheduleDetailState; open: boolean }) {
  const { t } = useTranslation();
  const detail = props.state.detail;
  const execution = props.state.execution.detail;
  if (detail === null) return null;
  const budget = detail.budget === null ? [] : budgetEntries(detail.budget);
  return (
    <div
      className="chat-task-tech-popover"
      data-testid="chat-task-tech"
      hidden={!props.open}
      role="dialog"
      aria-label={t('chat.taskCenter.detail.techInfo')}
    >
      <div className="chat-task-tech-popover-head">
        <strong>{t('chat.taskCenter.detail.techInfo')}</strong>
        <span>{t('chat.taskCenter.detail.techInfoHint')}</span>
      </div>
      <dl className="chat-task-fact-list">
        <Fact label={t('chat.taskCenter.detail.timezone')} testId="chat-task-center-timezone">
          {detail.timezone}
        </Fact>
        <Fact
          label={t('chat.taskCenter.detail.configVersion')}
          testId="chat-task-center-config-version"
        >
          {detail.config_version}
        </Fact>
        <Fact label={t('chat.taskCenter.detail.revision')} testId="chat-task-center-revision">
          {detail.revision_no}
        </Fact>
        <Fact
          label={
            detail.mode === 'AGENTIC'
              ? t('chat.taskCenter.detail.allowlist')
              : t('chat.taskCenter.detail.dependencies')
          }
          testId="chat-task-center-dependencies"
        >
          {detail.dependencies.length === 0
            ? t('chat.taskCenter.detail.noDependencies')
            : detail.dependencies.join(', ')}
        </Fact>
        <Fact label={t('chat.taskCenter.detail.delivery')} testId="chat-task-center-delivery">
          {detail.delivery_intent === 'PUSH'
            ? t('chat.taskCenter.detail.delivery.PUSH')
            : t('chat.taskCenter.detail.delivery.NONE')}
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
        {execution === null ? null : (
          <>
            <Fact label={t('chat.taskCenter.execution.startedAt')} testId="chat-task-execution-started">
              {execution.started_at === null ? (
                t('chat.taskCenter.detail.noValue')
              ) : (
                <DateTimeText value={execution.started_at} />
              )}
            </Fact>
            <Fact label={t('chat.taskCenter.execution.finishedAt')} testId="chat-task-execution-finished">
              {execution.finished_at === null ? (
                t('chat.taskCenter.detail.noValue')
              ) : (
                <DateTimeText value={execution.finished_at} />
              )}
            </Fact>
            <Fact label={t('chat.taskCenter.execution.snapshotSchema')} testId="chat-task-execution-schema">
              {execution.snapshot_schema_version}
            </Fact>
            <Fact label={t('chat.taskCenter.execution.snapshotBytes')} testId="chat-task-execution-snapshot-size">
              {formatBytes(execution.snapshot_bytes)}
            </Fact>
          </>
        )}
      </dl>
    </div>
  );
}

function FireRow(props: {
  fire: FireItem;
  selected: boolean;
  onSelect(fire: FireItem): void;
}) {
  const { t } = useTranslation();
  const fire = props.fire;
  const status = runDisplayStatus(fire);
  const reason =
    fire.status === 'SKIPPED'
      ? fire.skip_reason === null
        ? t('chat.taskCenter.fire.skipReasonUnknown')
        : skipReasonLabel(t, fire.skip_reason)
      : fire.status === 'FAILED_ADMISSION'
        ? `${t('chat.taskCenter.fire.admissionRepair')}${
            fire.admission_error_code === null ? '' : ` (${fire.admission_error_code})`
          }`
        : null;
  return (
    <li>
      <button
        type="button"
        className={`chat-task-fire-row${props.selected ? ' is-selected' : ''}`}
        data-testid="chat-task-fire-row"
        data-status={fire.status}
        aria-current={props.selected ? 'true' : undefined}
        onClick={() => props.onSelect(fire)}
      >
        <span className="chat-task-fire-time">
          <DateTimeText value={fire.scheduled_at} />
        </span>
        <RunStatusTag fire={fire} />
        {reason === null ? (
          <span className="chat-task-fire-summary">
            {durationText(t, fire.duration_ms)} ·{' '}
            {t('chat.taskCenter.run.files', { count: fire.artifact_count })}
          </span>
        ) : (
          <span className="chat-task-fire-summary">
            <span
              className="chat-task-fire-skip"
              data-testid={
                fire.status === 'SKIPPED'
                  ? 'chat-task-fire-skip-reason'
                  : 'chat-task-fire-repair'
              }
            >
              {reason}
            </span>
            {fire.missed_count > 0 ? (
              <span data-testid="chat-task-fire-missed">
                {' · '}
                {t('chat.taskCenter.fire.missedCount', { count: fire.missed_count })}
              </span>
            ) : null}
            {fire.dst_resolution === null ? null : (
              <span data-testid="chat-task-fire-dst">
                {' · '}
                {t('chat.taskCenter.fire.dstResolution')}
              </span>
            )}
          </span>
        )}
        {fire.execution_id === null ? (
          <span
            className="chat-task-fire-no-execution"
            data-testid="chat-task-fire-no-execution"
          >
            {t('chat.taskCenter.fire.noExecution')}
          </span>
        ) : (
          <span className="chat-task-fire-execution-link" data-testid="chat-task-fire-execution-link">
            {t('chat.taskCenter.fire.openExecution')}
          </span>
        )}
      </button>
    </li>
  );
}

function FireHistory(props: {
  fires: FireHistoryState;
  selectedFireId: string | null;
  onSelect(fire: FireItem): void;
  onPage(page: number): void;
  onRetryFires(page: number): void;
}) {
  const { t } = useTranslation();
  const { fires } = props;
  return (
    <section className="chat-task-fires" data-testid="chat-task-center-fires">
      <header className="chat-task-section-header">
        <div>
          <Typography.Title heading={6}>{t('chat.taskCenter.detail.recentRuns')}</Typography.Title>
          <span className="chat-task-section-hint">
            {t('chat.taskCenter.detail.recentRunsHint')}
          </span>
        </div>
      </header>
      {fires.errorCode !== null ? (
        <div className="chat-task-center-error" data-testid="chat-task-fire-error">
          <Banner
            type="danger"
            closeIcon={null}
            description={t('chat.taskCenter.fire.loadFailed')}
          />
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
            <FireRow
              key={fire.fire_id}
              fire={fire}
              selected={fire.fire_id === props.selectedFireId}
              onSelect={props.onSelect}
            />
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
  const [selectedFireId, setSelectedFireId] = useState<string | null>(null);
  const [techOpen, setTechOpen] = useState(false);
  const notFound = isNotFoundCode(state.errorCode) && state.detail === null;
  const firstLoad = state.phase === 'LOADING' && state.detail === null;
  const selectedFire =
    state.fires.items.find((fire) => fire.fire_id === selectedFireId) ?? null;

  useEffect(() => {
    if (!techOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setTechOpen(false);
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest('.chat-task-tech-popover') || target?.closest('.chat-task-tech-button')) {
        return;
      }
      setTechOpen(false);
    };
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('mousedown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('mousedown', onPointerDown);
    };
  }, [techOpen]);

  useEffect(() => {
    const items = state.fires.items;
    if (items.length === 0) {
      if (selectedFireId !== null) setSelectedFireId(null);
      return;
    }
    if (selectedFireId !== null && items.some((fire) => fire.fire_id === selectedFireId)) {
      return;
    }
    const byExecution =
      state.execution.executionId === null
        ? undefined
        : items.find((fire) => fire.execution_id === state.execution.executionId);
    const next = byExecution ?? items[0];
    setSelectedFireId(next.fire_id);
    if (next.execution_id !== null && state.execution.executionId !== next.execution_id) {
      props.onOpenExecution(next.execution_id);
    } else if (next.execution_id === null && state.execution.executionId !== null) {
      props.onCloseExecution();
    }
  }, [
    state.fires.items,
    state.execution.executionId,
    selectedFireId,
    props.onOpenExecution,
    props.onCloseExecution
  ]);

  return (
    <section
      className="chat-task-center chat-task-center-detail chat-task-detail-v2"
      data-testid="webchat-task-center"
      data-view="detail"
      data-schedule-id={state.scheduleId ?? ''}
      aria-label={t('chat.taskCenter.detail.label')}
    >
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
          <section className="chat-task-summary" data-testid="chat-task-center-summary">
            <div className="chat-task-detail-hero">
              <div className="chat-task-detail-hero-main">
                <div className="chat-task-detail-title-line">
                  <Typography.Title heading={4}>{state.detail.name}</Typography.Title>
                  <button
                    type="button"
                    className="chat-task-tech-button"
                    aria-label={t('chat.taskCenter.detail.techInfo')}
                    aria-expanded={techOpen}
                    onClick={() => setTechOpen((open) => !open)}
                  >
                    <IconInfoCircle size="small" />
                  </button>
                  <ScheduleStatusTag status={state.detail.status} />
                  {state.detail.status === 'ENDED' && state.detail.end_reason !== null ? (
                    <span data-testid="chat-task-center-end-reason">
                      {endReasonLabel(t, state.detail.end_reason)}
                    </span>
                  ) : null}
                </div>
                <p className="chat-task-detail-subtitle">
                  {state.detail.status === 'PAUSED'
                    ? t('chat.taskCenter.status.PAUSED')
                    : state.detail.next_fire_at === null
                      ? t('chat.taskCenter.list.noNextFire')
                      : (
                          <>
                            {t('chat.taskCenter.detail.nextRun')}{' '}
                            <DateTimeText value={state.detail.next_fire_at} />
                          </>
                        )}
                </p>
                {state.detail.attention_reason === null ? null : (
                  <p className="chat-task-attention" data-testid="chat-task-center-attention" role="alert">
                    {t('chat.taskCenter.detail.attention', {
                      reason: state.detail.attention_reason
                    })}
                  </p>
                )}
                <TechInfo state={state} open={techOpen} />
              </div>
              {props.actions ? (
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
            </div>
            <ScheduleMetrics state={state} />
          </section>

          <div className="chat-task-detail-split">
            <FireHistory
              fires={state.fires}
              selectedFireId={selectedFireId}
              onSelect={(fire) => {
                setSelectedFireId(fire.fire_id);
                if (fire.execution_id === null) {
                  props.onCloseExecution();
                } else {
                  props.onOpenExecution(fire.execution_id);
                }
              }}
              onPage={props.onFirePage}
              onRetryFires={props.onFirePage}
            />
            <section className="chat-task-detail-run">
              {state.fires.phase === 'LOADING' && state.fires.items.length === 0 ? (
                <Skeleton placeholder={<Skeleton.Paragraph rows={6} />} loading />
              ) : (
                <RunDetail
                  fire={selectedFire}
                  execution={state.execution}
                  downloads={props.downloads}
                  onDownloadArtifact={props.onDownloadArtifact}
                  onRetry={props.onRefresh}
                />
              )}
            </section>
          </div>
        </>
      )}
    </section>
  );
}
