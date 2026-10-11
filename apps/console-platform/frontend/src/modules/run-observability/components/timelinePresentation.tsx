import { IconActivity, IconBolt, IconComment, IconFile, IconLayers, IconWrench } from '@douyinfe/semi-icons';
import type { TFunction } from 'i18next';

export const EVENT_CATEGORIES = {
  run: { label: 'run.timeline.category.run', icon: <IconActivity /> },
  message: { label: 'run.timeline.category.message', icon: <IconComment /> },
  model: { label: 'run.timeline.category.model', icon: <IconBolt /> },
  tool: { label: 'run.timeline.category.tool', icon: <IconWrench /> },
  context: { label: 'run.timeline.category.context', icon: <IconLayers /> },
  artifact: { label: 'run.timeline.category.artifact', icon: <IconFile /> },
  other: { label: 'run.timeline.category.other', icon: <IconActivity /> }
} as const;

export function eventCategory(type: string): keyof typeof EVENT_CATEGORIES {
  switch (type) {
    case 'USER_MESSAGE':
    case 'ASSISTANT_MESSAGE':
    case 'ASSISTANT_TURN': return 'message';
    case 'MODEL_CALL_STARTED':
    case 'MODEL_CALL_COMPLETED': return 'model';
    case 'TOOL_CALL_STARTED':
    case 'TOOL_CALL':
    case 'SKILL_LOADED':
    case 'TASK_ACCEPTED':
    case 'TOOL_SUBMISSION_PENDING':
    case 'TOOL_SUBMISSION_FAILED':
    case 'TOOL_TASK_ACCEPTED':
    case 'TOOL_RESULT_RECEIVED':
    case 'BACKGROUND_RESULT':
    case 'BACKGROUND_RESULT_LATE': return 'tool';
    case 'CONTEXT_SUMMARY':
    case 'CONTEXT_COMPACTED': return 'context';
    case 'ARTIFACT_CREATED': return 'artifact';
    case 'RUN_CREATED':
    case 'INTERRUPT_REQUIRED':
    case 'CANCEL':
    case 'RUN_COMPLETED':
    case 'RUN_FAILED':
    case 'RUN_WAITING_TOOL':
    case 'RUN_RESUMED': return 'run';
    default: return 'other';
  }
}

export function eventTone(type: string): string {
  if (type === 'RUN_FAILED' || type === 'TOOL_SUBMISSION_FAILED') return 'error';
  if (['INTERRUPT_REQUIRED', 'CANCEL', 'RUN_WAITING_TOOL', 'BACKGROUND_RESULT_LATE'].includes(type)) return 'warning';
  if (['RUN_COMPLETED', 'MODEL_CALL_COMPLETED', 'TOOL_RESULT_RECEIVED', 'BACKGROUND_RESULT'].includes(type)) return 'success';
  return eventCategory(type);
}

/** Negative, absent or invalid timestamps cannot establish an elapsed duration. */
export function timeDifference(from: string | null, to: string): number | null {
  if (from === null) return null;
  const value = Date.parse(to) - Date.parse(from);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function formatDuration(milliseconds: number | null, t: TFunction): string {
  if (milliseconds === null) return '-';
  if (milliseconds > 0 && milliseconds < 1000) {
    return t('run.timeline.duration.milliseconds', { value: milliseconds });
  }
  const seconds = Number((milliseconds / 1000).toFixed(3));
  if (seconds < 60) return t('run.timeline.duration.seconds', { value: seconds });
  const wholeSeconds = Math.floor(milliseconds / 1000);
  const remainder = wholeSeconds % 60;
  if (wholeSeconds < 3600) {
    return t('run.timeline.duration.minutes', { minutes: Math.floor(wholeSeconds / 60), seconds: remainder });
  }
  return t('run.timeline.duration.hours', {
    hours: Math.floor(wholeSeconds / 3600), minutes: Math.floor(wholeSeconds / 60) % 60, seconds: remainder
  });
}
