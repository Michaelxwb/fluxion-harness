import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import type { TaskTimelineEvent } from './services/tasks';

export interface TaskTimelineProps {
  events: TaskTimelineEvent[];
}

function payloadSummary(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload ?? {});
  if (entries.length === 0) {
    return '';
  }
  return entries
    .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join(' · ');
}

export function TaskTimeline({ events }: TaskTimelineProps) {
  const { t } = useTranslation();
  if (events.length === 0) {
    return (
      <div data-testid="task-timeline-empty">
        <EmptyState title={t('common.empty')} description={t('task.detail.timelineEmpty')} />
      </div>
    );
  }
  const ordered = [...events].sort((left, right) => left.seq - right.seq);
  return (
    <ul data-testid="task-timeline" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
      {ordered.map((event) => (
        <li
          key={event.seq}
          data-testid={`timeline-event-${event.seq}`}
          data-event-type={event.event_type}
          style={{ padding: '8px 0', borderBottom: '1px solid var(--semi-color-border)' }}
        >
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <span style={{ color: 'var(--semi-color-text-2)' }}>#{event.seq}</span>
            <strong>{event.event_type}</strong>
            <span style={{ marginLeft: 'auto', color: 'var(--semi-color-text-2)' }}>
              <DateTimeText value={event.create_time} />
            </span>
          </div>
          {payloadSummary(event.payload) ? (
            <div style={{ fontSize: 12, color: 'var(--semi-color-text-2)', wordBreak: 'break-all' }}>
              {payloadSummary(event.payload)}
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
