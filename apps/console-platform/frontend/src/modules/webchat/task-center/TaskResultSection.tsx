/**
 * 后台任务结果摘要（改版）。
 *
 * 用户先看到「成功/失败/部分失败」的结论；有子任务时显示 4/5 环形图与失败项，
 * 无子任务时显示单状态。原始 JSON 不再占据首屏，收进任务名旁的技术信息弹层。
 */

import { Button } from '@douyinfe/semi-ui';
import type { CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';

import type { ExecutionDetail } from '../types';
import { resultText } from './taskResult';

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

export function TaskResultSection(props: {
  detail: ExecutionDetail;
  onLocateFailedChild?(): void;
}) {
  const { t } = useTranslation();
  const detail = props.detail;
  const counts = detail.counts;
  const errorMessage = (detail.error_message ?? '').trim();
  const active = ACTIVE_STATUSES.has(detail.status);
  const failed =
    detail.status === 'FAILED' || (detail.status === 'CANCELLED' && detail.error_code !== null);
  const hasChildren = detail.children.total > 0;
  const total = counts.total > 0 ? counts.total : detail.children.total;
  const succeeded = Math.max(0, Math.min(counts.succeeded, total));
  const failedCount = Math.max(0, counts.failed);
  const cancelled = Math.max(0, counts.cancelled);
  const progress = total === 0 ? 0 : Math.max(0, Math.min(100, (succeeded / total) * 100));
  const failedChild = detail.children.items.find((child) => child.status === 'FAILED');
  const result = resultText(detail.result);

  return (
    <section
      className="chat-background-summary-card"
      data-testid="chat-background-result"
    >
      {active ? (
        <p className="chat-background-running" data-testid="chat-background-running">
          {t('chat.taskCenter.background.result.running')}
        </p>
      ) : null}

      {!active && failed ? (
        errorMessage === '' ? (
          <p
            className="chat-background-error-summary"
            data-testid="chat-background-error-summary"
            role="alert"
          >
            {detail.error_code === null
              ? t('chat.taskCenter.background.result.failed')
              : t('chat.taskCenter.background.result.failedWithCode', { code: detail.error_code })}
          </p>
        ) : (
          <ErrorSection message={errorMessage} code={detail.error_code} />
        )
      ) : null}

      {!active && !failed ? (
        <div className="chat-background-summary-main">
          <div
            className={`chat-background-ring${hasChildren ? '' : ' is-single'}`}
            style={{ '--p': hasChildren ? progress : 100 } as CSSProperties}
            aria-label={
              hasChildren
                ? `${succeeded} / ${total} ${t('chat.taskCenter.background.counts.succeeded')}`
                : t('chat.taskCenter.background.single.completed')
            }
          >
            <div className="chat-background-ring-inner">
              {hasChildren ? (
                <>
                  <strong data-testid="chat-background-count-succeeded">{succeeded}</strong>
                  <span>
                    / <span data-testid="chat-background-count-total">{total}</span>{' '}
                    {t('chat.taskCenter.background.counts.succeeded')}
                  </span>
                </>
              ) : (
                <>
                  <strong>✓</strong>
                  <span>{t('chat.taskCenter.background.single.completed')}</span>
                </>
              )}
            </div>
          </div>
          <div className="chat-background-summary-copy">
            <h3>
              {hasChildren
                ? failedCount > 0
                  ? t('chat.taskCenter.background.partialFailureTitle', {
                      total,
                      failed: failedCount
                    })
                  : t('chat.taskCenter.background.summary.completedAll', { total })
                : t('chat.taskCenter.background.single.completed')}
            </h3>
            <p>
              {hasChildren
                ? failedChild === undefined
                  ? t('chat.taskCenter.background.summary.completedAllHint')
                  : t('chat.taskCenter.background.summary.failedChild', {
                      label: failedChild.label,
                      reason:
                        failedChild.error_message_summary ??
                        failedChild.error_code ??
                        t('chat.taskCenter.background.fact.unknown')
                    })
                : result === ''
                  ? t('chat.taskCenter.background.single.noResult')
                  : result.length > 220
                    ? `${result.slice(0, 220)}…`
                    : result}
            </p>
            {hasChildren && failedCount > 0 && props.onLocateFailedChild !== undefined ? (
              <div className="chat-background-summary-actions">
                <Button size="small" onClick={props.onLocateFailedChild}>
                  {t('chat.taskCenter.background.summary.locateFailed')}
                </Button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {hasChildren && !active && !failed ? (
        <>
          <div
            className="chat-background-progress"
            role="img"
            aria-label={`${succeeded} / ${total}`}
          >
            <span className="is-ok" style={{ width: `${progress}%` }} />
            {failedCount > 0 ? (
              <span
                className="is-failed"
                style={{ width: `${Math.min(100 - progress, (failedCount / total) * 100)}%` }}
              />
            ) : null}
          </div>
          <div className="chat-background-legend">
            <span className="is-ok">
              {t('chat.taskCenter.background.counts.succeeded')} {succeeded}
            </span>
            <span className="is-failed" data-testid="chat-background-count-failed">
              {t('chat.taskCenter.background.counts.failed')} {failedCount}
            </span>
            <span>
              {t('chat.taskCenter.background.counts.cancelled')} {cancelled}
            </span>
          </div>
        </>
      ) : null}

      {hasChildren && !active && !failed && total > detail.children.items.length ? (
        <p className="chat-background-muted">
          {t('chat.taskCenter.background.artifacts.truncated', {
            total,
            shown: detail.children.items.length
          })}
        </p>
      ) : null}
    </section>
  );
}
