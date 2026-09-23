import { Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EntityLink } from '../../components/common/EntityLink';
import { ErrorState } from '../../components/common/ErrorState';
import { StatusTag, type StatusTagOption } from '../../components/common/StatusTag';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { TaskTimeline } from './TaskTimeline';
import { useTaskActions } from './useTaskActions';
import { getTask, type TaskChild, type TaskDetail } from './services/tasks';

const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'CANCELLED'];
const CANCELLABLE_STATUSES = ['QUEUED', 'RUNNING', 'WAITING'];
const REFRESH_INTERVAL_MS = 1500;
const MAX_REFRESHES = 12;
// 取消后等待 Worker 协作停止：须覆盖一个完整心跳周期（后端 task_heartbeat_sec=20s）。
const CANCEL_MAX_REFRESHES = 30;

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  QUEUED: 'grey',
  RUNNING: 'blue',
  WAITING: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

export interface TaskDetailSideSheetProps {
  taskId: string | null;
  onCancel(): void;
  onSelectTask?(taskId: string): void;
  onMutated?(taskId: string): void;
}

function snapshotSummary(snapshot: Record<string, unknown>): string {
  const agent = (snapshot.agent ?? {}) as Record<string, unknown>;
  const model = (snapshot.model ?? {}) as Record<string, unknown>;
  const skills = (snapshot.skills ?? []) as Array<Record<string, unknown>>;
  return [
    `schema=${String(snapshot.schema_version ?? '-')}`,
    `agent=${String(agent.key ?? '-')}@${String(agent.revision ?? '-')}`,
    `model=${String(model.model ?? model.id ?? '-')}@${String(model.revision ?? '-')}`,
    `skills=${skills.map((skill) => String(skill.key ?? '-')).join(',') || '-'}`,
    `prompt=${String(snapshot.prompt_template_version ?? '-')}`
  ].join(' · ');
}

export function TaskDetailSideSheet(props: TaskDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const [refreshCount, setRefreshCount] = useState(0);
  const [refreshBudget, setRefreshBudget] = useState(MAX_REFRESHES);
  const [confirming, setConfirming] = useState(false);
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    if (!props.taskId) {
      return;
    }
    const current = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const value = await getTask(props.taskId);
      if (current === requestSeq.current) {
        setDetail(value);
      }
    } catch {
      if (current === requestSeq.current) {
        setDetail(null);
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [props.taskId]);

  useEffect(() => {
    setActiveTab('basic');
    setRefreshCount(0);
    setRefreshBudget(MAX_REFRESHES);
    if (props.taskId) {
      void load();
    } else {
      setDetail(null);
      setFailed(false);
    }
  }, [props.taskId, load]);

  // 有界刷新：仅详情可见且任务非终态时轮询（每次一个详情请求，不按事件逐条请求）。
  useEffect(() => {
    if (!props.taskId || !detail) {
      return;
    }
    if (
      confirming ||
      TERMINAL_STATUSES.includes(detail.status) ||
      refreshCount >= refreshBudget
    ) {
      return;
    }
    const timer = setTimeout(() => {
      setRefreshCount((count) => count + 1);
      void load();
    }, REFRESH_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [props.taskId, detail, refreshCount, refreshBudget, confirming, load]);

  const statusOptions = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(STATUS_COLORS).map(([status, color]) => [
          status,
          { color, label: t(`task.status.${status}`) }
        ])
      ),
    [t]
  );

  const children: TaskChild[] = detail?.children ?? [];

  const taskActions = useTaskActions({
    onCancelled: (taskId) => {
      // 取消成功后重开刷新预算，直到 Worker 真正落终态（RUNNING 协作取消需一个心跳周期）。
      setRefreshCount(0);
      setRefreshBudget(CANCEL_MAX_REFRESHES);
      void load();
      props.onMutated?.(taskId);
    }
  });

  // Header actions 记忆化：有界刷新产生的新 detail 对象不应重建 Popconfirm 触发节点。
  const actionsNode = useMemo(
    () =>
      detail ? (
        <div
          data-testid="task-detail-actions"
          style={{ display: 'flex', gap: 8, alignItems: 'center' }}
        >
          <StatusTag status={detail.status} options={statusOptions} />
          {detail.cancel_requested && !TERMINAL_STATUSES.includes(detail.status) ? (
            <span data-testid="task-cancel-requested">{t('task.cancel.requested')}</span>
          ) : null}
          {CANCELLABLE_STATUSES.includes(detail.status) && !detail.cancel_requested ? (
            <ConfirmAction
              danger
              title={t('task.cancel.confirm')}
              testId="task-cancel"
              onOpenChange={setConfirming}
              onConfirm={() => void taskActions.cancel(detail.task_id)}
            >
              {taskActions.cancelling ? t('common.loading') : t('task.cancel.action')}
            </ConfirmAction>
          ) : null}

        </div>
      ) : undefined,
    [
      detail?.status,
      detail?.task_id,
      detail?.cancel_requested,
      taskActions.cancelling,
      statusOptions,
      t,
      taskActions.cancel
    ]
  );

  return (
    <DetailSideSheet
      visible={props.taskId !== null}
      title={props.taskId ?? ''}
      subtitle={
        detail
          ? `${t(`task.status.${detail.status}`)} · ${detail.intent_key}`
          : t('task.detail.basic')
      }
      actions={actionsNode}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onCancel}
      notice={failed ? <ErrorState onRetry={() => void load()} /> : undefined}
    >
      <Tabs.TabPane itemKey="basic" tab={t('task.detail.basic')}>
        {loading ? <Spin /> : null}
        {detail ? (
          <DetailGrid
            items={[
              { label: t('task.columns.taskId'), value: detail.task_id },
              {
                label: t('task.columns.status'),
                value: <StatusTag status={detail.status} options={statusOptions} />
              },
              { label: t('task.columns.intent'), value: detail.intent_key },
              { label: t('task.columns.triggerType'), value: t(`task.trigger.${detail.trigger_type}`) },
              { label: t('task.columns.deliveryStatus'), value: t(`task.delivery.${detail.delivery_status}`) },
              {
                label: t('task.columns.deadlineAt'),
                value: <span data-testid="task-detail-deadline"><DateTimeText value={detail.deadline_at} /></span>
              },
              {
                label: t('task.detail.error'),
                value: (
                  <span data-testid="task-detail-error">
                    {detail.error_code ? `${detail.error_code}: ` : ''}
                    {detail.error_message ?? '-'}
                  </span>
                )
              },
              {
                label: t('task.detail.snapshot'),
                value: (
                  <span data-testid="task-detail-snapshot">
                    {snapshotSummary(detail.execution_snapshot)}
                  </span>
                )
              },
              {
                label: t('task.columns.createTime'),
                value: <DateTimeText value={detail.create_time} />
              },
              {
                label: t('task.columns.updateTime'),
                value: <DateTimeText value={detail.update_time} />
              }
            ]}
          />
        ) : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="timeline" tab={t('task.detail.timeline')}>
        {detail ? <TaskTimeline events={detail.timeline} /> : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="children" tab={t('task.detail.children')}>
        {detail ? (
          <ul data-testid="task-detail-children">
            {children.map((child) => (
              <li key={child.task_id}>
                <EntityLink
                  testId={`task-child-${child.task_id}`}
                  onClick={() => props.onSelectTask?.(child.task_id)}
                >
                  {child.task_id}
                </EntityLink>
                {' · '}
                <StatusTag status={child.status} options={statusOptions} />
                {child.item_key ? ` · ${child.item_key}` : ''}
              </li>
            ))}
            {children.length === 0 ? <li>-</li> : null}
          </ul>
        ) : null}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
