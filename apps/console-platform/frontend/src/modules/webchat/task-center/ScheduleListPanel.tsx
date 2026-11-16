/**
 * 任务列表（design frontend §3.3 CMP-03/CMP-04/CMP-05/CMP-19、§3.6；TASK-027）。
 *
 * 展示组件：只收 typed props 与事件，不发请求、不读 URL/Cookie。列表行的「最近一次」把
 * Fire 与 Execution 状态**分开**展示（跳过不等于执行失败）。过滤只有授权 Agent：缺省是
 * 全部授权 Agent；Agent 选项由页面提供（本组件不猜授权集合）。
 *
 * 可访问性：行是原生 button（Tab 可达、Enter/Space 可打开），aria-label 含任务名与状态；
 * 加载/空态/错误/404 都是显式区域，不显示假数据。
 */

import { Banner, Button, Empty, Skeleton, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { PaginationFooter } from '../../../components/common/PaginationFooter';
import { endReasonLabel, FireStatusTag, ExecutionStatusTag, ModeTag, ScheduleStatusTag } from './labels';
import { isNotFoundCode, type ScheduleListState } from './taskCenterState';
import type { TaskCenterBadges } from '../types';

/** 授权 Agent 的展示项（来自 WebChat 入口集合；页面只知道这些）。 */
export interface AgentOption {
  agent_id: string;
  display_name: string;
}

const AGENT_ALL = '__ALL__';

export interface ScheduleListPanelProps {
  /** 打开待处理视图（同一 Agent 范围）；徽标数字来自服务端。 */
  onOpenPending?(): void;
  pendingCount?: number;
  state: ScheduleListState;
  /** 授权 Agent 集合；为空时不渲染过滤控件（页面仍按「全部授权 Agent」读取）。 */
  agents?: readonly AgentOption[];
  onRefresh(): void;
  onOpen(scheduleId: string): void;
  onAgentChange(agentId: string | null): void;
  onPageChange(page: number): void;
}

function TaskCenterBadge(props: { badges: TaskCenterBadges | null; errorCode: string | null }) {
  const { t } = useTranslation();
  if (props.errorCode !== null) {
    return (
      <span className="chat-task-center-badge-error" data-testid="chat-task-center-badge-error">
        {t('chat.taskCenter.badge.unavailable')}
      </span>
    );
  }
  if (props.badges === null) return null;
  return (
    <span className="chat-task-center-badges" data-testid="chat-task-center-badges">
      <span data-testid="chat-task-center-badge-pending">
        {t('chat.taskCenter.badge.pending', { count: props.badges.pending_human })}
      </span>
      <span data-testid="chat-task-center-badge-attention">
        {t('chat.taskCenter.badge.attention', { count: props.badges.needs_attention })}
      </span>
    </span>
  );
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
        data-testid="chat-task-center-agent-filter"
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

function LastFire(props: { lastFire: NonNullable<ScheduleListState['items'][number]['last_fire']> }) {
  const { t } = useTranslation();
  const fire = props.lastFire;
  return (
    <span className="chat-schedule-row-last" data-testid="chat-schedule-row-last">
      <DateTimeText value={fire.scheduled_at} />
      <FireStatusTag status={fire.status} />
      {fire.execution_status === null ? null : (
        <ExecutionStatusTag status={fire.execution_status} />
      )}
      {fire.missed_count > 0 ? (
        <span className="chat-schedule-row-missed" data-testid="chat-schedule-row-missed">
          {t('chat.taskCenter.list.missedCount', { count: fire.missed_count })}
        </span>
      ) : null}
      {fire.dst_resolution === null ? null : (
        <span className="chat-schedule-row-dst" data-testid="chat-schedule-row-dst">
          {t('chat.taskCenter.list.dstResolution')}
        </span>
      )}
    </span>
  );
}

function ScheduleRow(props: {
  item: ScheduleListState['items'][number];
  onOpen(scheduleId: string): void;
}) {
  const { t } = useTranslation();
  const { item } = props;
  return (
    <li className="chat-schedule-row-item">
      <button
        type="button"
        className="chat-schedule-row"
        data-testid="chat-schedule-row"
        data-schedule-id={item.schedule_id}
        data-status={item.status}
        aria-label={t('chat.taskCenter.list.openAria', { name: item.name, status: item.status })}
        onClick={() => props.onOpen(item.schedule_id)}
      >
        <span className="chat-schedule-row-title">
          <Typography.Text strong>{item.name}</Typography.Text>
          <ModeTag mode={item.mode} />
          <ScheduleStatusTag status={item.status} />
          {item.needs_attention ? (
            <span className="chat-schedule-row-attention" data-testid="chat-schedule-row-attention">
              {t('chat.taskCenter.list.needsAttention')}
            </span>
          ) : null}
          {item.pending_human ? (
            <span className="chat-schedule-row-pending" data-testid="chat-schedule-row-pending">
              {t('chat.taskCenter.list.pendingHuman')}
            </span>
          ) : null}
        </span>
        <span className="chat-schedule-row-meta">
          <span>{item.timezone}</span>
          {item.next_fire_at === null ? (
            <span data-testid="chat-schedule-row-next-empty">
              {t('chat.taskCenter.list.noNextFire')}
            </span>
          ) : (
            <span data-testid="chat-schedule-row-next">
              {t('chat.taskCenter.list.nextFire')}
              <DateTimeText value={item.next_fire_at} />
            </span>
          )}
          {item.last_fire === null ? (
            <span data-testid="chat-schedule-row-never">
              {t('chat.taskCenter.list.neverFired')}
            </span>
          ) : (
            <LastFire lastFire={item.last_fire} />
          )}
        </span>
      </button>
    </li>
  );
}

export function ScheduleListPanel(props: ScheduleListPanelProps) {
  const { t } = useTranslation();
  const { state } = props;
  const showError = state.errorCode !== null && !isNotFoundCode(state.errorCode);
  const notFound = isNotFoundCode(state.errorCode);
  const firstLoad = state.phase === 'LOADING' && state.items.length === 0;

  return (
    <section
      className="chat-task-center"
      data-testid="webchat-task-center"
      data-view="list"
      aria-label={t('chat.taskCenter.label')}
    >
      <header className="chat-task-center-header">
        <Typography.Title heading={5}>{t('chat.taskCenter.title')}</Typography.Title>
        <TaskCenterBadge badges={state.badges} errorCode={state.badgesErrorCode} />
        {props.agents === undefined || props.agents.length === 0 ? (
          <span className="chat-task-center-agent-scope" data-testid="chat-task-center-agent-scope">
            {t('chat.taskCenter.agentFilter.all')}
          </span>
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
          data-testid="chat-task-center-refresh"
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
        <div className="chat-task-center-error" data-testid="chat-task-center-error">
          <Banner type="danger" closeIcon={null} description={t('chat.taskCenter.error.list')} />
          {props.onOpenPending ? (
            <Button onClick={props.onOpenPending} data-testid="task-center-pending-entry">
              {t('chat.taskCenter.pending.entry')}
              {props.pendingCount ? ` (${props.pendingCount})` : ''}
            </Button>
          ) : null}
          <Button size="small" data-testid="chat-task-center-retry" onClick={props.onRefresh}>
            {t('chat.taskCenter.list.retry')}
          </Button>
        </div>
      ) : null}
      {showError && state.errorCode !== null ? (
        <p className="chat-task-center-error-code" data-testid="chat-task-center-error-code">
          {t('chat.taskCenter.error.codeSuffix', { code: state.errorCode })}
        </p>
      ) : null}

      {firstLoad ? (
        <div className="chat-task-center-loading" data-testid="chat-task-center-loading">
          <Skeleton placeholder={<Skeleton.Paragraph rows={3} />} loading />
        </div>
      ) : null}

      {!firstLoad && !notFound && state.items.length === 0 ? (
        <div className="chat-task-center-empty" data-testid="chat-task-center-empty">
          <Empty description={t('chat.taskCenter.empty')} />
          <p>{t('chat.taskCenter.list.emptyHint')}</p>
        </div>
      ) : null}

      {state.items.length > 0 ? (
        <ul className="chat-schedule-list" data-testid="chat-schedule-list" role="list">
          {state.items.map((item) => (
            <ScheduleRow key={item.schedule_id} item={item} onOpen={props.onOpen} />
          ))}
        </ul>
      ) : null}

      {state.items.length > 0 && state.pageSize > 0 && state.total > state.pageSize ? (
        <PaginationFooter
          page={state.page}
          pageSize={state.pageSize}
          total={state.total}
          onPageChange={props.onPageChange}
        />
      ) : null}
    </section>
  );
}
