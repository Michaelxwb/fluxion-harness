/**
 * 待处理视图（design frontend §3.6、§3.3 CMP-06；TASK-028）。
 *
 * HUMAN 与 NEEDS_ATTENTION 是两类事实：前者要用户在 `input_schema` 上给出答案，后者只是
 * 任务卡在需要修复的依赖上（不生成表单）。两者同一次服务端分页，靠 `kind` 过滤。
 * 组件是展示层：不发请求、不读 URL；提交与刷新由页面注入。
 */

import { Banner, Button, Empty, Input, Skeleton, Typography } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { PaginationFooter } from '../../../components/common/PaginationFooter';
import type { AttentionPendingItem, HumanPendingItem, PendingItem } from '../types';
import type { PendingState } from './pendingState';

export interface PendingPanelProps {
  state: PendingState;
  onRespond(interactionId: string, response: Record<string, unknown>, idempotencyKey: string): void;
  onRefresh(): void;
  onPage(page: number): void;
  onResume(scheduleId: string): void;
  onOpenSchedule(scheduleId: string): void;
}

/** `input_schema` 只用来**生成表单**：真正的校验在服务端，这里只做必填提示。 */
type SchemaField = { name: string; required: boolean; options: string[] | null };

export function schemaFields(schema: Record<string, unknown> | null): SchemaField[] {
  if (!schema || typeof schema !== 'object') return [];
  const properties = (schema as { properties?: Record<string, unknown> }).properties;
  const required = new Set(
    Array.isArray((schema as { required?: unknown }).required)
      ? ((schema as { required: string[] }).required as string[])
      : []
  );
  if (!properties || typeof properties !== 'object') return [];
  return Object.entries(properties).map(([name, raw]) => {
    const spec = (raw ?? {}) as { enum?: unknown };
    const options = Array.isArray(spec.enum) ? spec.enum.map(String) : null;
    return { name, required: required.has(name), options };
  });
}

function HumanCard(props: {
  item: HumanPendingItem;
  submitting: boolean;
  errorCode: string | null;
  answered: boolean;
  onRespond: PendingPanelProps['onRespond'];
}) {
  const { t } = useTranslation();
  const fields = schemaFields(props.item.input_schema);
  const [values, setValues] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<string[]>([]);

  function submit(): void {
    const absent = fields.filter((field) => field.required && !values[field.name]?.trim());
    setMissing(absent.map((field) => field.name));
    if (absent.length > 0) return;
    props.onRespond(
      props.item.interaction_id,
      // 自由文本字段按字符串提交；schema 决定的强类型校验由服务端完成。
      Object.fromEntries(fields.map((field) => [field.name, values[field.name] ?? ''])),
      `respond-${props.item.interaction_id}`
    );
  }

  return (
    <li className="task-center-pending-item" data-testid={`pending-human-${props.item.interaction_id}`}>
      <div className="task-center-pending-head">
        <Button theme="borderless" onClick={() => undefined} className="task-center-pending-name">
          {props.item.schedule_name}
        </Button>
        <Typography.Text type="tertiary" size="small">
          {t('chat.taskCenter.pending.deadline')} <DateTimeText value={props.item.deadline_at} />
        </Typography.Text>
      </div>
      <Typography.Paragraph>{props.item.question}</Typography.Paragraph>
      {fields.map((field) => (
        <label key={field.name} className="task-center-pending-field">
          <span>
            {field.name}
            {field.required ? ' *' : ''}
          </span>
          <Input
            value={values[field.name] ?? ''}
            onChange={(value) => setValues((current) => ({ ...current, [field.name]: value }))}
            aria-invalid={missing.includes(field.name)}
            aria-describedby={missing.includes(field.name) ? `${field.name}-error` : undefined}
          />
          {missing.includes(field.name) ? (
            <span id={`${field.name}-error`} role="alert" className="task-center-pending-field-error">
              {t('chat.taskCenter.pending.required')}
            </span>
          ) : null}
        </label>
      ))}
      {props.errorCode ? (
        <Banner
          type="danger"
          description={t(`chat.taskCenter.pending.error.${props.errorCode}`, {
            defaultValue: t('chat.taskCenter.pending.error.unknown')
          })}
        />
      ) : null}
      {props.answered ? (
        <Typography.Text type="success">{t('chat.taskCenter.pending.answered')}</Typography.Text>
      ) : null}
      <Button
        theme="solid"
        type="primary"
        loading={props.submitting}
        onClick={submit}
        data-testid={`pending-submit-${props.item.interaction_id}`}
      >
        {t('chat.taskCenter.pending.submit')}
      </Button>
    </li>
  );
}

function AttentionCard(props: {
  item: AttentionPendingItem;
  onResume: PendingPanelProps['onResume'];
  onOpenSchedule: PendingPanelProps['onOpenSchedule'];
}) {
  const { t } = useTranslation();
  return (
    <li
      className="task-center-pending-item"
      data-testid={`pending-attention-${props.item.schedule_id}`}
    >
      <div className="task-center-pending-head">
        <Button theme="borderless" onClick={() => props.onOpenSchedule(props.item.schedule_id)}>
          {props.item.schedule_name}
        </Button>
      </div>
      <Typography.Text type="warning">{props.item.reason}</Typography.Text>
      <Typography.Paragraph type="tertiary">{props.item.action}</Typography.Paragraph>
      <Button onClick={() => props.onResume(props.item.schedule_id)}>
        {t('chat.taskCenter.actions.resume')}
      </Button>
    </li>
  );
}

export function PendingPanel(props: PendingPanelProps) {
  const { t } = useTranslation();
  const { state } = props;
  if (state.phase === 'LOADING' && state.items.length === 0) {
    return (
      <div className="task-center-pending">
        <Skeleton placeholder={<Skeleton.Paragraph rows={4} />} loading />
      </div>
    );
  }
  if (state.phase === 'ERROR' && state.items.length === 0) {
    return (
      <div className="task-center-pending">
        <Banner
          type="danger"
          description={t('chat.taskCenter.pending.loadFailed')}
        >
          <Button size="small" onClick={props.onRefresh}>
            {t('chat.taskCenter.refresh')}
          </Button>
        </Banner>
      </div>
    );
  }
  if (state.items.length === 0) {
    return (
      <div className="task-center-pending">
        <Empty description={t('chat.taskCenter.pending.empty')} />
      </div>
    );
  }
  return (
    <div className="task-center-pending">
      <ul className="task-center-pending-list">
        {state.items.map((item) =>
          item.kind === 'HUMAN' ? (
            <HumanCard
              key={`human-${item.interaction_id}`}
              item={item}
              submitting={state.submitting === item.interaction_id}
              errorCode={state.submitting === null ? state.submitErrorCode : null}
              answered={state.answered === item.interaction_id}
              onRespond={props.onRespond}
            />
          ) : (
            <AttentionCard
              key={`attention-${item.schedule_id}`}
              item={item}
              onResume={props.onResume}
              onOpenSchedule={props.onOpenSchedule}
            />
          )
        )}
      </ul>
      <PaginationFooter
        page={state.page}
        pageSize={state.pageSize}
        total={state.total}
        onPageChange={props.onPage}
      />
    </div>
  );
}
