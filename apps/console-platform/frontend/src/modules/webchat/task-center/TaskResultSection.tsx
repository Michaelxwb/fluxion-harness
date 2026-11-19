/**
 * 后台任务结果区（design frontend §3.3 CMP-03、§3.6；TASK-005）。
 *
 * 成功：结果正文 + 逻辑计数；失败：稳定错误码 + 原始正文。长错误默认折叠在 `<details>`
 * 内，可展开查看并复制；正文已由服务端脱敏，本组件不再二次加工内容。
 *
 * 运行中/等待不是失败：只显示状态说明，不渲染空的结果或错误区。
 */

import { Button, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { ExecutionDetail } from '../types';
import { formatResult } from './taskResult';

const ACTIVE_STATUSES: ReadonlySet<string> = new Set([
  'QUEUED',
  'RUNNING',
  'WAITING_EXTERNAL',
  'WAITING_HUMAN'
]);

/** 子任务摘要只取首行；完整正文仍在同一条详情事实里。 */
export function errorSummaryLine(message: string | null): string {
  const first = (message ?? '').split('\n')[0]?.trim() ?? '';
  return first.length > 200 ? `${first.slice(0, 200)}…` : first;
}

function ResultCounts(props: { detail: ExecutionDetail }) {
  const { t } = useTranslation();
  const counts = props.detail.counts;
  if (counts.total <= 0) return null;
  return (
    <dl className="chat-background-counts" data-testid="chat-background-counts">
      <div>
        <dt>{t('chat.taskCenter.background.counts.total')}</dt>
        <dd data-testid="chat-background-count-total">{counts.total}</dd>
      </div>
      <div>
        <dt>{t('chat.taskCenter.background.counts.succeeded')}</dt>
        <dd data-testid="chat-background-count-succeeded">{counts.succeeded}</dd>
      </div>
      <div className="is-failed">
        <dt>{t('chat.taskCenter.background.counts.failed')}</dt>
        <dd data-testid="chat-background-count-failed">{counts.failed}</dd>
      </div>
      <div>
        <dt>{t('chat.taskCenter.background.counts.cancelled')}</dt>
        <dd data-testid="chat-background-count-cancelled">{counts.cancelled}</dd>
      </div>
    </dl>
  );
}

function ErrorSection(props: { message: string; code: string | null }) {
  const { t } = useTranslation();
  const summary = errorSummaryLine(props.message);
  const copy = () => {
    if (typeof navigator === 'undefined' || navigator.clipboard === undefined) return;
    void navigator.clipboard.writeText(props.message).catch(() => undefined);
  };
  return (
    <div className="chat-background-error" data-testid="chat-background-error">
      <p className="chat-background-error-summary" data-testid="chat-background-error-summary" role="alert">
        {props.code === null
          ? t('chat.taskCenter.background.result.failed')
          : t('chat.taskCenter.background.result.failedWithCode', { code: props.code })}
        {summary === '' ? '' : ` · ${summary}`}
      </p>
      <details className="chat-background-error-details" data-testid="chat-background-error-full">
        <summary>{t('chat.taskCenter.background.result.showFullError')}</summary>
        <pre className="chat-background-error-text" data-testid="chat-background-error-text">
          {props.message}
        </pre>
      </details>
      <div className="chat-background-error-actions">
        <Button
          size="small"
          theme="borderless"
          data-testid="chat-background-error-copy"
          onClick={copy}
        >
          {t('chat.taskCenter.background.result.copyError')}
        </Button>
        <span className="chat-background-error-hint">
          {t('chat.taskCenter.background.result.redactedHint')}
        </span>
      </div>
    </div>
  );
}

export function TaskResultSection(props: { detail: ExecutionDetail }) {
  const { t } = useTranslation();
  const detail = props.detail;
  const errorMessage = (detail.error_message ?? '').trim();
  const failed =
    detail.status === 'FAILED' || (detail.status === 'CANCELLED' && detail.error_code !== null);
  const active = ACTIVE_STATUSES.has(detail.status);
  const result = formatResult(detail.result);
  return (
    <section className="chat-background-section" data-testid="chat-background-result">
      <header className="chat-background-section-header">
        <Typography.Title heading={6}>{t('chat.taskCenter.background.result.title')}</Typography.Title>
      </header>
      {active ? (
        <p className="chat-background-running" data-testid="chat-background-running">
          {t('chat.taskCenter.background.result.running')}
        </p>
      ) : null}
      {!active && failed && errorMessage !== '' ? (
        <ErrorSection message={errorMessage} code={detail.error_code} />
      ) : null}
      {!active && failed && errorMessage === '' ? (
        <p className="chat-background-error-summary" data-testid="chat-background-error-summary" role="alert">
          {detail.error_code === null
            ? t('chat.taskCenter.background.result.failed')
            : t('chat.taskCenter.background.result.failedWithCode', { code: detail.error_code })}
        </p>
      ) : null}
      {!active && !failed ? (
        result === '' ? (
          <p className="chat-background-muted" data-testid="chat-background-no-result">
            {t('chat.taskCenter.execution.noResult')}
          </p>
        ) : (
          <pre className="chat-background-result-body" data-testid="chat-background-result-body">
            {result}
          </pre>
        )
      ) : null}
      <ResultCounts detail={detail} />
    </section>
  );
}
