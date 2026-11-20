import { IconChevronDown, IconChevronUp, IconFile } from '@douyinfe/semi-icons';
import { Button, Collapsible, Tag, Timeline } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { CopyButton } from '../../../components/common/CopyButton';
import { EmptyState } from '../../../components/common/EmptyState';
import type { RunTimelineEvent } from '../services/runs';
import { EVENT_CATEGORIES, eventCategory, eventTone, formatDuration, timeDifference } from './timelinePresentation';
import { RunEventDetails } from './RunEventDetails';

function EgressDetails({ event }: { event: RunTimelineEvent }) {
  const { t } = useTranslation();
  const egress = event.egress;
  if (!egress) return null;
  const { method, operation, error_code: errorCode } = egress;
  const methodPrefix = method ? `${method} ` : '';
  const path = methodPrefix && operation.startsWith(methodPrefix)
    ? operation.slice(methodPrefix.length) : operation;
  const reason = errorCode === 'LIMIT_WINDOW' ? t('run.egress.windowLimit') : null;
  return (
    <dl className="run-event-details run-egress-details" data-testid={`run-egress-details-${event.seq}`}>
      <div>
        <dt>{t('run.egress.result')}</dt>
        <dd>
          <span title={egress.result_status}>
            <Tag size="small" color={egress.result_status === 'COMPLETED' ? 'green' : 'red'} type="light">
              {egress.result_status === 'COMPLETED' ? t('audit.resultStatus.SUCCESS') :
                t(`audit.resultStatus.${egress.result_status}`, { defaultValue: egress.result_status })}
            </Tag>
          </span>
        </dd>
      </div>
      <div>
        <dt>{t('run.egress.target')}</dt>
        <dd><code>{egress.target}</code></dd>
      </div>
      <div>
        <dt>{t('run.egress.operation')}</dt>
        <dd>
          {method && <Tag size="small" type="light">{method}</Tag>}
          <code>{path}</code>
        </dd>
      </div>
      {errorCode && <div>
        <dt>{t(reason ? 'run.egress.reason' : 'run.egress.error')}</dt>
        <dd>{reason && <span>{reason}</span>}<code className="run-egress-code">{errorCode}</code></dd>
      </div>}
    </dl>
  );
}

function EgressRequestPreview({ egress }: { egress: NonNullable<RunTimelineEvent['egress']> }) {
  const { t } = useTranslation();
  if (!egress.business_trace_id) return null;
  return (
    <div className="run-egress-preview">
      <div className="run-egress-preview-request">
        <code>{egress.operation} · {egress.target}</code>
        {egress.status_code != null && <Tag size="small" color={egress.status_code >= 400 ? 'red' : 'green'}>
          HTTP {egress.status_code}
        </Tag>}
        {egress.latency_ms != null && <span>{formatDuration(egress.latency_ms, t)}</span>}
      </div>
      <div className="run-egress-preview-trace">
        <span>{t('run.egress.businessTraceId')}</span>
        <code>{egress.business_trace_id}</code>
        <CopyButton value={egress.business_trace_id} />
      </div>
    </div>
  );
}

/**
 * 时间线轮廓的**事件类型枚举**（设计 §3.5 I18N-01「动态键必须枚举」）。
 *
 * 口径取后端 `canonical_event.event_type` 的落库业务名（`run_service.STREAM_BUSINESS_TYPES`
 * 与 async-tools 事件写入点）；`ASSISTANT_DELTA` 被后端轮廓剔除，不登记。
 * 新增事件类型时：本数组与两侧词条 `run.event.*` 同步；未登记的取值走安全兜底，
 * 不读取也不渲染事件负载。
 */
export const RUN_EVENT_TYPES = [
  'RUN_CREATED',
  'USER_MESSAGE',
  'ASSISTANT_MESSAGE',
  'ASSISTANT_TURN',
  'MODEL_CALL_STARTED',
  'MODEL_CALL_COMPLETED',
  'TOOL_CALL_STARTED',
  'TOOL_CALL',
  'SKILL_LOADED',
  'ARTIFACT_CREATED',
  'INTERRUPT_REQUIRED',
  'TASK_ACCEPTED',
  'CANCEL',
  'RUN_COMPLETED',
  'RUN_FAILED',
  'CONTEXT_SUMMARY',
  'CONTEXT_COMPACTED',
  'TOOL_SUBMISSION_PENDING',
  'TOOL_SUBMISSION_FAILED',
  'TOOL_TASK_ACCEPTED',
  'TOOL_RESULT_RECEIVED',
  'BACKGROUND_RESULT',
  'BACKGROUND_RESULT_LATE',
  'RUN_WAITING_TOOL',
  'RUN_RESUMED',
  'EGRESS_BLOCKED',
  'EGRESS_REQUEST'
] as const;

const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set(RUN_EVENT_TYPES);

export interface RunTimelineOutlineProps {
  runId: string;
  generation: number;
  onOpenTask?: (taskId: string) => void;
  events: readonly RunTimelineEvent[];
  truncated: boolean;
  startTime: string | null;
}

function TimelineSummary({ events }: Pick<RunTimelineOutlineProps, 'events'>) {
  const { t } = useTranslation();
  const metrics = [
    { label: t('run.timeline.events'), value: events.length },
    { label: t('run.timeline.modelCalls'), value: events.filter((event) => event.event_type === 'MODEL_CALL_STARTED').length },
    { label: t('run.timeline.toolCalls'), value: events.filter((event) => event.event_type === 'TOOL_CALL_STARTED').length },
    { label: t('run.timeline.span'), value: formatDuration(timeDifference(events[0].create_time, events[events.length - 1].create_time), t) }
  ];
  return (
    <div className="run-timeline-summary" aria-label={t('run.timeline.summary')}>
      {metrics.map((metric) => (
        <div className="run-timeline-metric" key={metric.label}>
          <span>{metric.label}</span><strong>{metric.value}</strong>
        </div>
      ))}
    </div>
  );
}

function EventTiming(props: { event: RunTimelineEvent; previous?: RunTimelineEvent; origin: string | null; fromRunStart: boolean }) {
  const { t } = useTranslation();
  const elapsed = timeDifference(props.origin, props.event.create_time);
  const interval = props.previous ? timeDifference(props.previous.create_time, props.event.create_time) : null;
  return (
    <div className="run-timeline-timing">
      <DateTimeText value={props.event.create_time} />
      {elapsed !== null ? (
        <span data-testid="event-elapsed">{t(props.fromRunStart ? 'run.timeline.sinceStart' : 'run.timeline.sinceFirst', { duration: formatDuration(elapsed, t) })}</span>
      ) : null}
      {interval !== null && interval > 0 ? (
        <span data-testid="event-interval">{t('run.timeline.sincePrevious', { duration: formatDuration(interval, t) })}</span>
      ) : null}
    </div>
  );
}

function TimelineEvent(props: { runId: string; generation: number; onOpenTask?: (taskId: string) => void; event: RunTimelineEvent; index: number; previous?: RunTimelineEvent; origin: string | null; fromRunStart: boolean; className?: string }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const { event, index } = props;
  const category = EVENT_CATEGORIES[eventCategory(event.event_type)];
  const label = KNOWN_EVENT_TYPES.has(event.event_type) ? t(`run.event.${event.event_type}`) : t('run.event.unknown', { type: event.event_type });
  return (
    <Timeline.Item dot={category.icon} className={`${props.className ?? ''} run-timeline-event run-timeline-tone-${event.egress?.result_status === 'FAILED' ? 'error' : eventTone(event.event_type)}`} data-event-type={event.event_type}>
      <Button className="run-timeline-toggle" theme="borderless" aria-expanded={expanded} aria-controls={`run-event-${event.seq}`} onClick={() => setExpanded((open) => !open)} data-testid={`run-event-toggle-${event.seq}`}>
      <span className="run-timeline-event-heading">
        <span className="run-timeline-step mono" title={t('run.timeline.originalSequence', { seq: event.seq })}>{String(index + 1).padStart(2, '0')}</span>
        <strong title={event.event_type}>{label}</strong>
        <span className="run-timeline-category">{t(category.label)}</span>
        {event.has_artifact ? <span className="run-timeline-artifact"><IconFile size="small" />{t('run.detail.hasArtifact')}</span> : null}
        <span className="run-timeline-expand">{expanded ? <IconChevronUp size="small" /> : <IconChevronDown size="small" />}</span>
      </span>
      </Button>
      <EventTiming {...props} />
      {event.egress && <EgressRequestPreview egress={event.egress} />}
      <Collapsible id={`run-event-${event.seq}`} isOpen={expanded} motion={false} keepDOM lazyRender>
        {event.egress ? <EgressDetails event={event} /> : <RunEventDetails key={props.generation} runId={props.runId} seq={event.seq} expanded={expanded} onOpenTask={props.onOpenTask} />}
      </Collapsible>
    </Timeline.Item>
  );
}

/** Structural fields only; counts and time span describe the displayed outline, including truncated results. */
export function RunTimelineOutline(props: RunTimelineOutlineProps) {
  const { t } = useTranslation();
  const fromRunStart = props.startTime !== null && Number.isFinite(Date.parse(props.startTime));
  const origin = fromRunStart ? props.startTime : props.events[0]?.create_time ?? null;
  return (
    <div data-testid="run-detail-timeline">
      {props.events.length === 0 ? (
        <EmptyState title={t('run.detail.timelineEmpty')} />
      ) : (
        <>
          <TimelineSummary events={props.events} />
          <p className="run-timeline-caption">{t('run.timeline.scope')}</p>
          <Timeline className="run-timeline" aria-label={t('run.detail.timeline')}>
            {props.events.map((event, index) => (
              <TimelineEvent key={event.seq} runId={props.runId} generation={props.generation} onOpenTask={props.onOpenTask} event={event} index={index} previous={props.events[index - 1]} origin={origin} fromRunStart={fromRunStart} />
            ))}
          </Timeline>
        </>
      )}
      {props.truncated ? (
        <p data-testid="run-detail-truncated" className="detail-hint">
          {t('run.detail.truncated')}
        </p>
      ) : null}
    </div>
  );
}
