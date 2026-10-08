import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { EmptyState } from '../../../components/common/EmptyState';
import type { RunTimelineEvent } from '../services/runs';

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
  'RUN_RESUMED'
] as const;

const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set(RUN_EVENT_TYPES);

export interface RunTimelineOutlineProps {
  events: readonly RunTimelineEvent[];
  truncated: boolean;
}

/**
 * 时间线轮廓（设计 §3.3 CMP-03）：只本地化 `event_type` 标签，保留 seq、时间与有无制品标记；
 * 不读取原事件数据。未知事件用「事件（原值）」兜底——不假装认识，也不渲染未知负载。
 */
export function RunTimelineOutline(props: RunTimelineOutlineProps) {
  const { t } = useTranslation();

  const labelFor = (eventType: string): string =>
    KNOWN_EVENT_TYPES.has(eventType)
      ? t(`run.event.${eventType}`)
      : t('run.event.unknown', { type: eventType });

  return (
    <div data-testid="run-detail-timeline">
      {props.events.length === 0 ? (
        <EmptyState title={t('run.detail.timelineEmpty')} />
      ) : (
        <ul className="detail-list">
          {props.events.map((event) => (
            <li key={event.seq} data-event-type={event.event_type}>
              <span className="mono">#{event.seq}</span>
              {' · '}
              {labelFor(event.event_type)}
              {' · '}
              <DateTimeText value={event.create_time} />
              {event.has_artifact ? ` · ${t('run.detail.hasArtifact')}` : ''}
            </li>
          ))}
        </ul>
      )}
      {props.truncated ? (
        <p data-testid="run-detail-truncated" className="detail-hint">
          {t('run.detail.truncated')}
        </p>
      ) : null}
    </div>
  );
}
