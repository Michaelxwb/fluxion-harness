import type { TFunction } from 'i18next';

import { RESOURCE_TYPES } from './components/AuditFilterBar';
import type { AuditListItem } from './types';

type Translate = TFunction;

function shortId(value: string, size = 8): string {
  return value.length > size ? value.slice(0, size) : value;
}

export function auditActorLabel(t: Translate, record: AuditListItem): string {
  if (record.actorName) return record.actorName;
  if (record.auditType === 'CONFIG') return t('audit.actor.system');
  return record.actorUserId ? shortId(record.actorUserId) : '-';
}

export function auditActionLabel(t: Translate, record: AuditListItem): string {
  const action = record.action;
  if (record.auditType === 'MODEL' && action.includes('/')) {
    return t('audit.action.modelCall', { model: action.split('/').pop() });
  }
  if (record.auditType === 'TOOL') {
    return t('audit.action.toolCall', { tool: action });
  }
  if (record.auditType === 'EGRESS') {
    return t('audit.action.egressCall', { operation: action });
  }
  const labels: Record<string, string> = {
    CREATE: t('audit.action.CREATE'),
    UPDATE: t('audit.action.UPDATE'),
    DELETE: t('audit.action.DELETE'),
    RESTORE: t('audit.action.RESTORE'),
    ENABLE: t('audit.action.ENABLE'),
    DISABLE: t('audit.action.DISABLE'),
    LOGIN: t('audit.action.LOGIN'),
    LOGOUT: t('audit.action.LOGOUT'),
    PAUSE: t('audit.action.PAUSE'),
    RESUME: t('audit.action.RESUME'),
    ACTIVATE: t('audit.action.ACTIVATE'),
    RETIRE: t('audit.action.RETIRE'),
    TRIAL_START: t('audit.action.TRIAL_START'),
    TRIAL_PROMOTE: t('audit.action.TRIAL_PROMOTE'),
    TRIAL_RETIRE: t('audit.action.TRIAL_RETIRE'),
    REGENERATE: t('audit.action.REGENERATE'),
    REVISE: t('audit.action.REVISE'),
    CLEAR: t('audit.action.CLEAR'),
    IMPORT: t('audit.action.IMPORT'),
    BIND: t('audit.action.BIND'),
    UNBIND: t('audit.action.UNBIND'),
    UPLOAD: t('audit.action.UPLOAD'),
    CANCEL: t('audit.action.CANCEL'),
    SELECT: t('audit.action.SELECT'),
    SEND: t('audit.action.SEND'),
  };
  return labels[action] ?? action;
}

export function auditResourceTypeLabel(t: Translate, value: string): string {
  return new Set<string>(RESOURCE_TYPES).has(value) ? t(`audit.resourceType.${value}`) : value;
}

export function auditTargetLabel(t: Translate, target: string | null | undefined): string {
  if (!target) return '-';
  const [type, ...rest] = target.split('/');
  const value = rest.join('/');
  if (type === 'MEMORY_EXPERIENCE') {
    const [key, userId] = value.split(' / ');
    return userId
      ? t('audit.target.experienceWithUser', { key, user: shortId(userId) })
      : t('audit.target.experience', { key });
  }
  if (type === 'LEARNING_FAMILY') {
    return t('audit.target.family', { family: value });
  }
  return `${auditResourceTypeLabel(t, type)} ${shortId(value)}`;
}
