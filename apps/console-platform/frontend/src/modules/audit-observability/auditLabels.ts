import type { TFunction } from 'i18next';

import { RESOURCE_TYPES } from './components/AuditFilterBar';
import type { AuditListItem } from './types';

type Translate = TFunction;
/** 审计 action 词条域：后端 action 是自由字符串，只有登记在这里的取值走 `audit.action.*`。 */
export const AUDIT_ACTION_DOMAIN = [
  'CREATE', 'UPDATE', 'DELETE', 'RESTORE', 'ENABLE', 'DISABLE', 'LOGIN', 'LOGOUT',
  'PAUSE', 'RESUME', 'ACTIVATE', 'RETIRE', 'TRIAL_START', 'TRIAL_PROMOTE', 'TRIAL_RETIRE',
  'REGENERATE', 'REVISE', 'CLEAR', 'IMPORT', 'BIND', 'UNBIND', 'UPLOAD', 'CANCEL',
  'SELECT', 'SEND'
] as const;

const AUDIT_ACTION_KEYS = new Set<string>(AUDIT_ACTION_DOMAIN);


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
  if (!AUDIT_ACTION_KEYS.has(action)) return action;
  const key = `audit.action.${action}`;
  const label = t(key);
  return label === key ? action : label;
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
