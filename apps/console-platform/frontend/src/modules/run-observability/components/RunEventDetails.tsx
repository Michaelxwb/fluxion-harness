import { Button, Spin } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { CopyButton } from '../../../components/common/CopyButton';
import { CopyableText } from '../../../components/common/CopyableText';
import { DetailGrid } from '../../../components/common/DetailGrid';
import { ErrorState } from '../../../components/common/ErrorState';
import { useRunEventDetail } from '../hooks/useRunEventDetail';
import { MODEL_SUMMARY_FIELDS, type RunEventDetail } from '../services/runs';
import { formatDuration } from './timelinePresentation';

export const EVENT_DETAIL_STATUSES = ['OK', 'ERROR', 'RETRY', 'SUCCESS', 'COMPLETED', 'FAILED', 'CANCELLED', 'CREATED', 'RUNNING', 'WAITING_TOOL', 'WAITING_INPUT', 'QUEUED', 'WAITING', 'SUBMIT_PENDING', 'SUBMITTED', 'TASK_ACCEPTED', 'RESULT_RECEIVED', 'MATERIALIZED', 'LATE'] as const;
const STATUSES: ReadonlySet<string> = new Set(EVENT_DETAIL_STATUSES);
export const COMPACTION_LAYERS = ['micro', 'snip', 'summary'] as const;
const LAYERS: ReadonlySet<string> = new Set(COMPACTION_LAYERS);
export const MODEL_RETRY_REASONS = ['RATE_LIMITED', 'UNAVAILABLE'] as const;
const RETRY_REASONS: ReadonlySet<string> = new Set(MODEL_RETRY_REASONS);

function EventFields({ detail, onOpenTask }: { detail: RunEventDetail; onOpenTask?: (id: string) => void }) {
  const { t } = useTranslation();
  const items = detail.fields.map(({ key, value }) => {
    let display = String(value);
    if (['status', 'task_status', 'operation_status'].includes(key) && STATUSES.has(display)) display = t(`run.eventDetail.status.${display}`);
    if (key === 'completion_mode' && (value === 'JOIN' || value === 'DETACH')) display = t(`run.operation.mode.${value}`);
    if (key === 'duration_ms' && typeof value === 'number') display = formatDuration(value, t);
    if (key === 'attempt' && typeof value === 'number') display = String(value + 1);
    if (key === 'retry_reason' && RETRY_REASONS.has(display)) display = t(`run.eventDetail.retry.${display}`);
    if (key === 'protocol' && value === 'OPENAI') display = t('run.eventDetail.protocolOpenai');
    const isId = ['tool_call_id', 'operation_id', 'task_id'].includes(key);
    const label = t(`run.eventDetail.field.${key}`);
    return { label, value: key === 'task_id' && onOpenTask ? (
      <span className="run-event-task-link"><Button theme="borderless" onClick={() => onOpenTask(String(value))}>{display.slice(0, 8)}</Button><CopyButton value={String(value)} /></span>
    ) : isId ? <CopyableText display={display} full={String(value)} /> : <span title={String(value)}>{display}</span> };
  });
  return items.length ? <DetailGrid items={items} /> : null;
}

function EventContent({ detail }: { detail: RunEventDetail }) {
  const { t } = useTranslation();
  const label = t(detail.event_type === 'USER_MESSAGE' ? 'run.eventDetail.question' : detail.event_type.startsWith('ASSISTANT_') ? 'run.eventDetail.reply' : 'run.eventDetail.content');
  return detail.text ? (
    <section className="run-event-text-section">
      <div className="run-event-section-heading"><span>{label}</span><CopyButton value={detail.text} label={t('run.eventDetail.copyContent')} /></div>
      <pre className="run-event-text">{detail.text}</pre>
      {detail.text_truncated ? <p className="detail-hint">{t('run.eventDetail.textTruncated')}</p> : null}
    </section>
  ) : null;
}

function ModelContext({ detail }: { detail: RunEventDetail }) {
  const { t } = useTranslation();
  if (!detail.model_state || detail.model_state === 'LINKED') return null;
  const summary = detail.model_summary;
  const items = summary ? MODEL_SUMMARY_FIELDS.map((key) => {
    const value = summary[key];
    return {
      label: t(`run.eventDetail.summary.${key}`),
      value: value === null ? t('run.eventDetail.notRecorded') : key === 'duration_ms' ? formatDuration(value, t) : String(value)
    };
  }) : [];
  return <section className="run-event-model-context">
    <p className="detail-hint">{t(detail.model_state === 'PENDING' ? 'run.eventDetail.modelPending' : 'run.eventDetail.modelUnlinked')}</p>
    {summary ? <div data-testid="run-event-model-summary">
      <div className="run-event-section-heading">{t('run.eventDetail.modelSummary')}</div>
      <DetailGrid items={items} />
      <p className="detail-hint">{t('run.eventDetail.modelSummaryHint')}</p>
    </div> : <p className="detail-hint">{t('run.eventDetail.modelNoAudit')}</p>}
  </section>;
}

function EventResources({ detail }: { detail: RunEventDetail }) {
  const { t } = useTranslation();
  return <>
    {detail.arguments.length ? <section><div className="run-event-section-heading">{t('run.eventDetail.arguments')}</div><dl className="run-event-arguments">{detail.arguments.map((argument, index) => <div key={index}><dt>{argument.name}</dt><dd>{argument.value}</dd></div>)}</dl></section> : null}
    {detail.attachments.length ? <section><div className="run-event-section-heading">{t('run.eventDetail.attachments')}</div>{detail.attachments.map((file, index) => <div className="run-event-file" key={index}>
      <span>{file.filename ?? file.media_type ?? t('run.eventDetail.artifact')}</span>
      {file.size !== null ? <span>{t('run.eventDetail.bytes', { count: file.size })}</span> : null}
      {file.id ? <CopyableText display={file.id.slice(0, 8)} full={file.id} /> : null}
    </div>)}</section> : null}
    {detail.compaction.map((layer) => <div className="run-event-compaction" key={layer.layer}>
      <span>{LAYERS.has(layer.layer) ? t(`run.eventDetail.layer.${layer.layer}`) : layer.layer}</span>
      {layer.groups !== null ? <span>{t('run.eventDetail.groups', { count: layer.groups })}</span> : null}
      {layer.bytes_saved !== null ? <span>{t('run.eventDetail.saved', { count: layer.bytes_saved })}</span> : null}
    </div>)}
  </>;
}

export function RunEventDetails(props: { runId: string; seq: number; expanded: boolean; onOpenTask?: (id: string) => void }) {
  const { t } = useTranslation();
  const { detail, loading, failed, notFound, reload } = useRunEventDetail(props.runId, props.seq, props.expanded);
  if (!props.expanded) return null;
  if (loading) return <div role="status" className="run-event-loading"><Spin size="small" />{t('run.eventDetail.loading')}</div>;
  if (failed) return <ErrorState onRetry={reload} description={t(notFound ? 'run.eventDetail.unavailable' : 'run.eventDetail.failed')} />;
  if (!detail) return null;
  const empty = !detail.model_state && !detail.text && !detail.fields.length && !detail.arguments.length && !detail.attachments.length && !detail.compaction.length;
  return <div className="run-event-details" data-testid={`run-event-details-${props.seq}`}>
    <EventFields detail={detail} onOpenTask={props.onOpenTask} />
    <ModelContext detail={detail} />
    <EventContent detail={detail} />
    <EventResources detail={detail} />
    {empty ? <p className="detail-hint">{t('run.eventDetail.empty')}</p> : null}
  </div>;
}
