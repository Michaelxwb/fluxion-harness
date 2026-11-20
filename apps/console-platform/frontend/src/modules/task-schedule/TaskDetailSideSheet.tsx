import { Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EntityLink } from '../../components/common/EntityLink';
import { ErrorState } from '../../components/common/ErrorState';
import { StatusTag } from '../../components/common/StatusTag';
import { ExportExecutionButton } from '../execution-export/components/ExportExecutionButton';
import {
  taskStatusFallback,
  taskStatusOptions,
  taskTypeLabel,
  type Translate
} from './scheduleCatalog';
import { TaskBasicDetails } from './TaskBasicDetails';
import { TaskTimeline } from './TaskTimeline';
import { getTask, type TaskChild, type TaskDetail } from './services/tasks';

const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'CANCELLED'];
const REFRESH_INTERVAL_MS = 1500;
const MAX_REFRESHES = 12;
// 取消后等待 Worker 协作停止：须覆盖一个完整心跳周期（后端 task_heartbeat_sec=20s）。
const CANCEL_MAX_REFRESHES = 30;

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
  const [loadedDetail, setDetail] = useState<TaskDetail | null>(null);
  const detail = loadedDetail?.task_id === props.taskId ? loadedDetail : null;
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
      title={t('task.detail.title')}
      actions={detail ? <ExportExecutionButton kind="TASK" id={detail.task_id} /> : undefined}
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
        {loading && !detail ? <Spin /> : null}
        {detail ? (
          <TaskBasicDetails
            detail={detail}
            onOpenSchedule={props.onOpenSchedule}
            onOpenRun={props.onOpenRun}
          />
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
