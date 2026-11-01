import type { TFunction } from 'i18next';

import type { ActivationState } from './types';

type Translate = TFunction;

export function shortId(value: string, size = 8): string {
  return value.length > size ? value.slice(0, size) : value;
}

export function familyLabel(t: Translate, family: string | null): string {
  if (!family) return t('learning.enum.family.unknown');
  if (family.startsWith('skill:')) {
    return t('learning.enum.family.skill', { key: family.slice('skill:'.length) });
  }
  if (family.startsWith('agent:')) {
    return t('learning.enum.family.agent', { id: shortId(family.slice('agent:'.length)) });
  }
  return t('learning.enum.family.other', { value: family });
}

export function stateLabel(t: Translate, state: string): string {
  const key = `learning.enum.state.${state}`;
  const label = t(key);
  return label === key ? t('learning.enum.state.unknown', { value: state }) : label;
}

export function authorityLabel(t: Translate, authority: string): string {
  const key = `learning.enum.authority.${authority}`;
  const label = t(key);
  return label === key ? authority : label;
}

export function ownerLabel(t: Translate, owner: string): string {
  const key = `learning.enum.owner.${owner}`;
  const label = t(key);
  return label === key ? owner : label;
}

export function reportStatusLabel(t: Translate, status: string): string {
  const key = `learning.enum.reportStatus.${status}`;
  const label = t(key);
  return label === key ? status : label;
}

export function channelLabel(t: Translate, channel: string | null): string {
  if (!channel) return '-';
  const key = `learning.enum.channel.${channel}`;
  const label = t(key);
  return label === key ? channel : label;
}

export function skipReasonLabel(t: Translate, reason: string | null): string {
  if (!reason) return '-';
  const key = `learning.enum.skipReason.${reason}`;
  const label = t(key);
  return label === key ? reason : label;
}

export function actionLabel(t: Translate, action: string): string {
  const key = `learning.enum.action.${action}`;
  const label = t(key);
  return label === key ? action : label;
}

export function actorLabel(t: Translate, actorType: string): string {
  const key = `learning.enum.actor.${actorType}`;
  const label = t(key);
  return label === key ? actorType : label;
}

/** 审计行操作者：账号名优先，其次系统/自动或 after_json.operator，最后才回落到编号。 */
export function operatorLabel(t: Translate, row: Record<string, unknown>): string {
  const name = row.operator_name;
  if (typeof name === 'string' && name.trim()) return name;
  const operator = row.operator;
  if (operator === 'automatic') return t('learning.enum.actor.SYSTEM');
  if (typeof operator === 'string' && operator.trim() && !/^[0-9a-f-]{36}$/i.test(operator)) {
    return operator;
  }
  const actor = row.actor_user_id;
  return typeof actor === 'string' ? shortId(actor) : '-';
}

export function scoreLabel(score: number | null | undefined): string {
  if (score === null || score === undefined) return '-';
  return `${Math.round(score * 100)}%`;
}

export function improvementLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  const sign = value >= 0 ? '+' : '';
  return `${sign}${Math.round(value * 100)}%`;
}

export function stateTone(state: ActivationState | string): 'grey' | 'blue' | 'green' | 'orange' {
  if (state === 'ACTIVE') return 'green';
  if (state === 'TRIAL') return 'blue';
  if (state === 'RETIRED') return 'orange';
  return 'grey';
}
