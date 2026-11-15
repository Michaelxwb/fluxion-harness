import { Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EntityLink } from '../../components/common/EntityLink';
import { ErrorState } from '../../components/common/ErrorState';
import { StatusTag } from '../../components/common/StatusTag';
import { ExportExecutionButton } from '../execution-export/components/ExportExecutionButton';
import {
  humanSummary,
  snapshotSummary,
  taskStatusFallback,
  taskStatusOptions,
  taskTypeLabel,
  waitKindLabel,
  type Translate
} from './scheduleCatalog';
import { TaskTimeline } from './TaskTimeline';
import { getTask, type TaskChild, type TaskDetail } from './services/tasks';

const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'CANCELLED'];
const REFRESH_INTERVAL_MS = 1500;
const MAX_REFRESHES = 12;
// 取消后等待 Worker 协作停止：须覆盖一个完整心跳周期（后端 task_heartbeat_sec=20s）。
const CANCEL_MAX_REFRESHES = 30;

/** 来源 Run 与列表/审计详情同口径只展示前 8 位；完整值在运行详情标题的复制面。 */
function shortId(value: string): string {
  return value.slice(0, 8);
}

export interface TaskDetailSideSheetProps {
  taskId: string | null;
  onCancel(): void;
  onSelectTask?(taskId: string): void;
  reloadKey?: number;
  /** 定时触发来源的反向入口。展示组件不导航（与 `RecentTaskList` 同约定），由页面接线。 */
  onOpenSchedule?(scheduleId: string): void;
  /** 派生它的那次对话运行（`source_run_id`）。同一约定：回调而不导航。 */
  onOpenRun?(runId: string): void;
  /** ESC 关闭本面板；由页面只在它是当前最上层面板时开启。 */
  closeOnEsc?: boolean;
}

/**
 * Task 详情（design §3.10）：`WAITING_EXTERNAL` 显示 `wait_kind`，`WAITING_HUMAN` 显示
 * Human 摘要；快照只渲染后端白名单摘要（schema/mode/allowlist/memory_mode/budget）。
 */
export function TaskDetailSideSheet(props: TaskDetailSideSheetProps) {
  const { t } = useTranslation();
  const translate: Translate = (key) => t(key);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const [refreshCount, setRefreshCount] = useState(0);
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
    if (props.taskId) {
      void load();
    } else {
      setDetail(null);
      setFailed(false);
    }
  }, [props.taskId, props.reloadKey, load]);

  // 有界刷新：仅详情可见且任务非终态时轮询（每次一个详情请求，不按事件逐条请求）。
  useEffect(() => {
    if (!props.taskId || !detail) {
      return;
    }
    if (
      TERMINAL_STATUSES.includes(detail.status) ||
      refreshCount >= (detail.cancel_requested ? CANCEL_MAX_REFRESHES : MAX_REFRESHES)
    ) {
      return;
    }
    const timer = setTimeout(() => {
      setRefreshCount((count) => count + 1);
      void load();
    }, REFRESH_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [props.taskId, detail, refreshCount, load]);

  const statusOptions = useMemo(() => taskStatusOptions(translate), [t]);

  const children: TaskChild[] = detail?.children ?? [];

  return (
    <DetailSideSheet
      visible={props.taskId !== null}
      title={props.taskId ?? ''}
      subtitle={
        detail
          ? `${taskStatusFallback(translate, detail.status).label} · ${taskTypeLabel(
              translate,
              detail.task_type
            )}`
          : t('task.detail.basic')
      }
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onCancel}
      notice={failed ? <ErrorState onRetry={() => void load()} /> : undefined}
      closeOnEsc={props.closeOnEsc}
    >
      <Tabs.TabPane itemKey="basic" tab={t('task.detail.basic')}>
        {loading ? <Spin /> : null}
        {detail ? (
          <>
            <div className="detail-inline-toolbar" style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <ExportExecutionButton kind="TASK" id={props.taskId} />
            </div>
            <div className="detail-section-title">{t('task.detail.basic')}</div>
            <DetailGrid
              items={[
                { label: t('task.columns.taskId'), value: detail.task_id },
                {
                  label: t('task.columns.status'),
                  value: (
                    <>
                      <StatusTag
                        status={detail.status}
                        options={statusOptions}
                        fallback={taskStatusFallback(translate, detail.status)}
                      />
                      {detail.cancel_requested && !TERMINAL_STATUSES.includes(detail.status) ? (
                        <span data-testid="task-cancel-requested">{t('task.cancel.requested')}</span>
                      ) : null}
                    </>
                  )
                },
                { label: t('task.columns.taskType'), value: taskTypeLabel(translate, detail.task_type) },
                { label: t('task.columns.triggerType'), value: t(`task.trigger.${detail.trigger_type}`) },
                // 反向链接：只在定时触发的任务上出现，紧邻「触发方式」——两者是同一件事的
                // 类型与来源。`schedule_id` 已删时链接仍可点，由 schedule 详情侧渲染 404 态。
                ...(detail.trigger_type === 'SCHEDULED' && detail.schedule_id
                  ? [
                      {
                        label: t('task.columns.scheduleId'),
                        value: props.onOpenSchedule ? (
                          <EntityLink
                            testId="task-detail-schedule"
                            onClick={() => props.onOpenSchedule?.(detail.schedule_id as string)}
                          >
                            {detail.schedule_id}
                          </EntityLink>
                        ) : (
                          detail.schedule_id
                        )
                      }
                    ]
                  : []),
                ...(detail.scheduled_at
                  ? [
                      {
                        label: t('task.detail.scheduledAt'),
                        value: <DateTimeText value={detail.scheduled_at} />
                      }
                    ]
                  : []),
                // 同一条「来源」线的另一半：对话派生的任务回指它那次 Run。字段一直传到前端，
                // 只是此前没有地方渲染它。Run 已回收/删除时点进去同样是错误态。
                ...(detail.source_run_id
                  ? [
                      {
                        label: t('task.columns.sourceRun'),
                        value: props.onOpenRun ? (
                          <EntityLink
                            testId="task-detail-run"
                            onClick={() => props.onOpenRun?.(detail.source_run_id as string)}
                          >
                            {shortId(detail.source_run_id)}
                          </EntityLink>
                        ) : (
                          shortId(detail.source_run_id)
                        )
                      }
                    ]
                  : []),
                // 外部等待的细类只在真正等待外部结果时才有意义；其余状态下它是内部事实。
                ...(detail.status === 'WAITING_EXTERNAL'
                  ? [
                      {
                        label: t('task.detail.waitKind'),
                        value: (
                          <span data-testid="task-detail-wait-kind">
                            {waitKindLabel(translate, detail.wait_kind)}
                          </span>
                        )
                      }
                    ]
                  : []),
                ...(detail.human
                  ? [
                      {
                        fullWidth: true,
                        label: t('task.detail.human'),
                        value: (
                          <span data-testid="task-detail-human">
                            {humanSummary(translate, detail.human)}
                          </span>
                        )
                      }
                    ]
                  : []),
                {
                  label: t('task.columns.deadlineAt'),
                  value: <span data-testid="task-detail-deadline"><DateTimeText value={detail.deadline_at} /></span>
                },
                {
                  fullWidth: true,
                  label: t('task.detail.error'),
                  value: (
                    <span data-testid="task-detail-error">
                      {detail.error_code ? `${detail.error_code}: ` : ''}
                      {detail.error_message ?? '-'}
                    </span>
                  )
                },
                {
                  fullWidth: true,
                  label: t('task.detail.snapshot'),
                  value: (
                    <span data-testid="task-detail-snapshot">
                      {snapshotSummary(translate, detail.snapshot)}
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
          </>
        ) : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="timeline" tab={t('task.detail.timeline')}>
        {detail ? <TaskTimeline events={detail.timeline} /> : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="children" tab={t('task.detail.children')}>
        {detail ? (
          <ul className="detail-list" data-testid="task-detail-children">
            {children.map((child) => (
              <li key={child.task_id}>
                <EntityLink
                  testId={`task-child-${child.task_id}`}
                  onClick={() => props.onSelectTask?.(child.task_id)}
                >
                  {child.task_id}
                </EntityLink>
                {' · '}
                <StatusTag
                  status={child.status}
                  options={statusOptions}
                  fallback={taskStatusFallback(translate, child.status)}
                />
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
