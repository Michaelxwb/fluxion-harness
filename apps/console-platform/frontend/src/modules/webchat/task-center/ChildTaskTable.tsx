/**
 * 子任务表（design frontend §3.3 CMP-03、§3.6；TASK-005）。
 *
 * 分页事实由服务端给；本组件只渲染当前页。失败行置红并显示首行摘要（≤200 字符），
 * 完整原文要打开该子任务的执行详情——表格不伪造不存在的字段。
 */

import { Banner, Button, Empty, Skeleton } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { PaginationFooter } from '../../../components/common/PaginationFooter';
import type { TaskChildItem } from '../types';
import { ExecutionStatusTag } from './labels';
import type { AsyncPhase } from './taskCenterState';

export interface ChildTaskTableProps {
  items: readonly TaskChildItem[];
  page: number;
  pageSize: number;
  total: number;
  phase: AsyncPhase;
  errorCode: string | null;
  selectedChildId: string | null;
  onPage(page: number): void;
  onSelectChild(taskId: string): void;
  onRetry(): void;
}

function summary(item: TaskChildItem): string {
  const value = (item.error_message_summary ?? '').trim();
  return value.length > 200 ? `${value.slice(0, 200)}…` : value;
}

export function ChildTaskTable(props: ChildTaskTableProps) {
  const { t } = useTranslation();
  const loading = props.phase === 'LOADING';
  return (
    <section className="chat-background-section" data-testid="chat-background-children">
      <header className="chat-background-section-header">
        <h3>{t('chat.taskCenter.background.children.title')}</h3>
        {props.total > 0 ? (
          <span className="chat-background-section-hint" data-testid="chat-background-children-total">
            {t('chat.taskCenter.background.children.total', { count: props.total })}
          </span>
        ) : null}
      </header>
      {props.errorCode !== null ? (
        <div className="chat-task-center-error" data-testid="chat-background-children-error">
          <Banner
            type="danger"
            closeIcon={null}
            description={t('chat.taskCenter.background.children.loadFailed')}
          />
          <Button
            size="small"
            data-testid="chat-background-children-retry"
            onClick={props.onRetry}
          >
            {t('chat.taskCenter.list.retry')}
          </Button>
          <span className="chat-task-center-error-code">
            {t('chat.taskCenter.error.codeSuffix', { code: props.errorCode })}
          </span>
        </div>
      ) : null}
      {loading ? (
        <div data-testid="chat-background-children-loading">
          <Skeleton placeholder={<Skeleton.Paragraph rows={2} />} loading />
        </div>
      ) : null}
      {!loading && props.items.length === 0 ? (
        <Empty description={t('chat.taskCenter.background.children.empty')} />
      ) : null}
      {props.items.length > 0 ? (
        <div className="chat-background-table-scroll">
          <table className="chat-background-child-table" data-testid="chat-background-child-table">
            <thead>
              <tr>
                <th>{t('chat.taskCenter.background.children.headerLabel')}</th>
                <th>{t('chat.taskCenter.background.children.headerStatus')}</th>
                <th>{t('chat.taskCenter.background.children.headerError')}</th>
                <th>{t('chat.taskCenter.background.children.headerArtifacts')}</th>
                <th>{t('chat.taskCenter.background.children.headerFinished')}</th>
              </tr>
            </thead>
            <tbody>
              {props.items.map((item) => {
                const detail = summary(item);
                return (
                  <tr
                    key={item.task_id}
                    data-testid="chat-background-child-row"
                    data-task-id={item.task_id}
                    data-status={item.status}
                    aria-selected={item.task_id === props.selectedChildId}
                    className={[
                      item.status === 'FAILED' ? 'is-failed' : '',
                      item.task_id === props.selectedChildId ? 'is-selected' : ''
                    ]
                      .filter(Boolean)
                      .join(' ') || undefined}
                  >
                    <td>
                      <button
                        type="button"
                        className="chat-background-child-select"
                        data-testid="chat-background-child-select"
                        aria-pressed={item.task_id === props.selectedChildId}
                        aria-label={t('chat.taskCenter.background.children.openAria', {
                          label: item.label,
                          status: item.status
                        })}
                        onClick={() => props.onSelectChild(item.task_id)}
                      >
                        <span className="chat-background-child-label">{item.label}</span>
                        {item.item_key === null ? null : (
                          <span className="chat-background-child-key">{item.item_key}</span>
                        )}
                      </button>
                    </td>
                    <td>
                      <ExecutionStatusTag status={item.status} />
                    </td>
                    <td>
                      <span
                        className={detail === '' ? 'chat-background-muted' : 'chat-background-child-error'}
                        data-testid="chat-background-child-error"
                      >
                        {detail === '' ? '—' : detail}
                      </span>
                    </td>
                    <td>
                      <span data-testid="chat-background-child-artifacts">
                        {item.artifact_count > 0
                          ? t('chat.taskCenter.background.children.artifactCount', {
                              count: item.artifact_count
                            })
                          : '—'}
                      </span>
                    </td>
                    <td>
                      <span data-testid="chat-background-child-finished">
                        {item.finished_at === null ? '—' : <DateTimeText value={item.finished_at} />}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {props.items.length > 0 && props.pageSize > 0 && props.total > props.pageSize ? (
        <PaginationFooter
          page={props.page}
          pageSize={props.pageSize}
          total={props.total}
          onPageChange={props.onPage}
        />
      ) : null}
    </section>
  );
}
