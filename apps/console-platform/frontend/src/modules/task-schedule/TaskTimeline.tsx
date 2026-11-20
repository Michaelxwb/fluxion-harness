import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { waitKindLabel } from './scheduleCatalog';
import type { TaskTimelineEvent, WaitKind } from './services/tasks';
import './taskDetail.css';

export interface TaskTimelineProps {
  events: TaskTimelineEvent[];
}

function eventTone(type: string): string {
  if (type === 'COMPLETED' || type === 'SENT') return 'success';
  if (['FAILED', 'DEADLINE_EXCEEDED', 'TIMED_OUT'].includes(type)) return 'danger';
  if (['RECLAIMED', 'RETRY', 'WAITING_EXTERNAL', 'WAITING_HUMAN', 'WAITING'].includes(type)) return 'warning';
  if (['CLAIMED', 'RESUMED'].includes(type)) return 'active';
  return 'default';
}

function TimelineEvent({ event }: { event: TaskTimelineEvent }) {
  const { t } = useTranslation();
  const payload = Object.fromEntries(Object.entries(event.payload ?? {}).filter(([, value]) => value != null));
  const { scheduled_at, attempt, error_code, wait_kind, tool_name } = payload;
  return (
    <li className="task-timeline-event" data-tone={eventTone(event.event_type)}
      data-testid={`timeline-event-${event.seq}`} data-event-type={event.event_type}>
      <span className="task-timeline-marker" aria-hidden />
      <div className="task-timeline-card">
        <div className="task-timeline-heading">
          <strong>{t(`task.timeline.event.${event.event_type}`, { defaultValue: event.event_type })}</strong>
          <time dateTime={event.create_time}><DateTimeText value={event.create_time} /></time>
        </div>
        <div className="task-timeline-facts">
          {typeof scheduled_at === 'string' ? (
            <span>{t('task.detail.scheduledAt')} · <DateTimeText value={scheduled_at} /></span>
          ) : null}
          {typeof attempt === 'number' && attempt > 0 ? <span>{t('task.timeline.attempt', { count: attempt })}</span> : null}
          {typeof wait_kind === 'string' ? <span>{waitKindLabel(t, wait_kind as WaitKind)}</span> : null}
          {typeof tool_name === 'string' && tool_name ? <code>{tool_name}</code> : null}
          {typeof error_code === 'string' && error_code ? <code className="task-timeline-error">{error_code}</code> : null}
        </div>
        {Object.keys(payload).length ? (
          <details className="task-timeline-diagnostics">
            <summary>{t('task.timeline.diagnostics')}</summary>
            <pre>{JSON.stringify(payload, null, 2)}</pre>
          </details>
        ) : null}
      </div>
    </li>
  );
}

export function TaskTimeline({ events }: TaskTimelineProps) {
  const { t } = useTranslation();
  if (!events.length) {
    return (
      <div data-testid="task-timeline-empty">
        <EmptyState title={t('common.empty')} description={t('task.detail.timelineEmpty')} />
      </div>
    );
  }
  const ordered = [...events].sort((left, right) => left.seq - right.seq);
  return (
    <ol className="task-timeline" data-testid="task-timeline">
      {ordered.map((event) => <TimelineEvent key={event.seq} event={event} />)}
    </ol>
  );
}
