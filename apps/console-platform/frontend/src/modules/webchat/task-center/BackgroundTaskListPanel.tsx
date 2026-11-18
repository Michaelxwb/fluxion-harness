/**
 * 后台任务列表（design frontend §3.3 CMP-01、§3.6；TASK-005）。
 *
 * 只展示任务记录摘要：Skill 名、任务类型、状态、部分失败计数、子任务进度、含产物与创建
 * 时间。结果正文不在这里——列表请求不带结果，打开详情才读取。行是原生 button，键盘可达。
 */

import { Banner, Button, Empty, Skeleton, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { PaginationFooter } from '../../../components/common/PaginationFooter';
import type { TaskCenterTaskItem } from '../types';
import type { BackgroundTaskListState } from './backgroundState';
import { PartialFailureTag, TaskTypeTag } from './backgroundTaskLabels';
import { ExecutionStatusTag } from './labels';
import type { AgentOption } from './ScheduleListPanel';
import { isNotFoundCode } from './taskCenterState';

const AGENT_ALL = '__ALL__';

export interface BackgroundTaskListPanelProps {
  state: BackgroundTaskListState;
  agents?: readonly AgentOption[];
  onOpen(taskId: string): void;
  onPage(page: number): void;
  onAgentChange(agentId: string | null): void;
  onRefresh(): void;
}

function agentName(agents: readonly AgentOption[] | undefined, agentId: string): string | null {
  return agents?.find((agent) => agent.agent_id === agentId)?.display_name ?? null;
}

function AgentFilter(props: {
  agents: readonly AgentOption[];
  agentId: string | null;
  onChange(agentId: string | null): void;
}) {
  const { t } = useTranslation();
  return (
    <label className="chat-task-center-agent-filter">
      <span>{t('chat.taskCenter.agentFilter.label')}</span>
      <select
        className="chat-task-center-agent-select"
        data-testid="chat-background-agent-filter"
        value={props.agentId ?? AGENT_ALL}
        onChange={(event) =>
          props.onChange(event.target.value === AGENT_ALL ? null : event.target.value)
        }
      >
        <option value={AGENT_ALL}>{t('chat.taskCenter.agentFilter.all')}</option>
        {props.agents.map((agent) => (
          <option key={agent.agent_id} value={agent.agent_id}>
            {agent.display_name}
          </option>
        ))}
      </select>
    </label>
  );
}

function TaskRow(props: {
  item: TaskCenterTaskItem;
  agents?: readonly AgentOption[];
  onOpen(taskId: string): void;
}) {
  const { t } = useTranslation();
  const { item } = props;
  const name = agentName(props.agents, item.agent_id);
  return (
    <li className="chat-schedule-row-item">
      <button
        type="button"
        className="chat-schedule-row"
        data-testid="chat-background-task-row"
        data-task-id={item.task_id}
        data-status={item.status}
        aria-label={t('chat.taskCenter.background.openAria', {
          name: item.skill_name,
          status: item.status
        })}
        onClick={() => props.onOpen(item.task_id)}
      >
        <span className="chat-schedule-row-title">
          <Typography.Text strong>{item.skill_name}</Typography.Text>
          <TaskTypeTag taskType={item.task_type} />
          <ExecutionStatusTag status={item.status} />
          {item.partial_failure ? <PartialFailureTag counts={item.counts} /> : null}
          {item.has_artifacts ? (
            <span className="chat-background-has-artifacts" data-testid="chat-background-has-artifacts">
              {t('chat.taskCenter.background.row.hasArtifacts')}
            </span>
          ) : null}
        </span>
        <span className="chat-schedule-row-meta">
          {name === null ? null : <span data-testid="chat-background-agent">{name}</span>}
          <span data-testid="chat-background-created-at">
            <DateTimeText value={item.create_time} />
          </span>
          {item.child_total > 0 ? (
            <span data-testid="chat-background-child-progress">
              {t('chat.taskCenter.background.row.children', {
                finished: item.child_finished,
                total: item.child_total
              })}
            </span>
          ) : null}
          {item.finished_at === null ? null : (
            <span data-testid="chat-background-finished-at">
              {t('chat.taskCenter.background.row.finishedAt')}
              <DateTimeText value={item.finished_at} />
            </span>
          )}
        </span>
      </button>
    </li>
  );
}

export function BackgroundTaskListPanel(props: BackgroundTaskListPanelProps) {
  const { t } = useTranslation();
  const { state } = props;
  const showError = state.errorCode !== null && !isNotFoundCode(state.errorCode);
  const notFound = isNotFoundCode(state.errorCode);
  const firstLoad = state.phase === 'LOADING' && state.items.length === 0;

  return (
    <section
      className="chat-task-center"
      data-testid="webchat-background-task-list"
      data-view="background-list"
      aria-label={t('chat.taskCenter.background.label')}
    >
      <header className="chat-task-center-header">
        <Typography.Title heading={5}>{t('chat.taskCenter.background.title')}</Typography.Title>
        {props.agents === undefined || props.agents.length === 0 ? (
          <span className="chat-task-center-agent-scope">{t('chat.taskCenter.agentFilter.all')}</span>
        ) : (
          <AgentFilter
            agents={props.agents}
            agentId={state.filters.agentId}
            onChange={props.onAgentChange}
          />
        )}
        <Button
          size="small"
          theme="borderless"
          data-testid="chat-background-refresh"
          aria-label={t('chat.taskCenter.list.refresh')}
          onClick={props.onRefresh}
        >
          {t('chat.taskCenter.list.refresh')}
        </Button>
      </header>

      {notFound ? (
        <div className="chat-task-center-state" data-testid="chat-task-center-not-found" role="alert">
          {t('chat.taskCenter.error.notFound')}
        </div>
      ) : null}
      {showError ? (
        <div className="chat-task-center-error" data-testid="chat-background-error">
          <Banner
            type="danger"
            closeIcon={null}
            description={t('chat.taskCenter.background.loadFailed')}
          />
          <Button size="small" data-testid="chat-background-retry" onClick={props.onRefresh}>
            {t('chat.taskCenter.list.retry')}
          </Button>
          {state.errorCode === null ? null : (
            <span className="chat-task-center-error-code">
              {t('chat.taskCenter.error.codeSuffix', { code: state.errorCode })}
            </span>
          )}
        </div>
      ) : null}

      {firstLoad ? (
        <div className="chat-task-center-loading" data-testid="chat-task-center-loading">
          <Skeleton placeholder={<Skeleton.Paragraph rows={3} />} loading />
        </div>
      ) : null}

      {!firstLoad && !notFound && state.items.length === 0 ? (
        <div className="chat-task-center-empty" data-testid="chat-background-empty">
          <Empty description={t('chat.taskCenter.background.empty')} />
          <p>{t('chat.taskCenter.background.emptyHint')}</p>
        </div>
      ) : null}

      {state.items.length > 0 ? (
        <ul className="chat-schedule-list" data-testid="chat-background-task-list" role="list">
          {state.items.map((item) => (
            <TaskRow
              key={item.task_id}
              item={item}
              agents={props.agents}
              onOpen={props.onOpen}
            />
          ))}
        </ul>
      ) : null}

      {state.items.length > 0 && state.pageSize > 0 && state.total > state.pageSize ? (
        <PaginationFooter
          page={state.page}
          pageSize={state.pageSize}
          total={state.total}
          onPageChange={props.onPage}
        />
      ) : null}
    </section>
  );
}
