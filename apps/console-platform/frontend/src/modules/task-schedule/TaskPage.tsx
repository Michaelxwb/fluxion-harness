import { Button, DatePicker, Modal, Select, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { ListActionButton } from '../../components/common/ListActionButton';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { StatusTag, type StatusTagOption } from '../../components/common/StatusTag';
import { EntityLink } from '../../components/common/EntityLink';
import { listSkills } from '../skill-management/services/skills';
import { listAgentsForPicker, listUsers } from '../user-identity/services/users';
import {
  RelatedDetailController,
  useRelatedDetail
} from '../run-observability/RelatedDetailController';
import { ScheduleDetailSideSheet } from './ScheduleDetailSideSheet';
import { TaskDetailSideSheet } from './TaskDetailSideSheet';
import { useTaskActions } from './useTaskActions';
import { listTasks, type TaskListItem, type TaskListParams } from './services/tasks';

const DEFAULT_PARAMS: TaskListParams = { page: 1, page_size: 15 };

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  QUEUED: 'grey',
  RUNNING: 'blue',
  WAITING: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

const TRIGGER_TYPES = ['IMMEDIATE', 'SCHEDULED'] as const;
const TASK_STATUSES = ['QUEUED', 'RUNNING', 'WAITING', 'COMPLETED', 'FAILED', 'CANCELLED'] as const;
const CANCELLABLE_STATUSES = ['QUEUED', 'RUNNING', 'WAITING'];
/** 下拉可选项一次取够（后端 page_size 上限 100），超出部分无法在此选择。 */
const PICKER_PAGE_SIZE = 100;

interface PickerOption {
  value: string;
  label: string;
}

function toIsoDate(value: Date | null | undefined): string | undefined {
  return value ? value.toISOString() : undefined;
}

/** dateRange 的结束日是当天 00:00；时间筛选「起止含端点」须包含结束日整天。 */
function toIsoEndOfDay(value: Date | null | undefined): string | undefined {
  if (!value) {
    return undefined;
  }
  const end = new Date(value);
  end.setHours(23, 59, 59, 999);
  return end.toISOString();
}

/**
 * 筛选下拉的可选项**首次展开时才取**：这三个端点与任务列表无关，进页面就抓会白白多发三个
 * 请求；端点不可用时还会在用户什么都没做的情况下连弹三条错误。
 *
 * 失败不在这里兜底成错误态——共享 ApiClient 拦截器已经 Toast 过；这里只保证下拉留空、
 * 下次展开可重试，绝不让一个便利筛选器的故障升级成页面级失败。
 */
function usePickerOptions(load: () => Promise<PickerOption[]>): {
  options: PickerOption[];
  onDropdownVisibleChange(visible: boolean): void;
} {
  const [options, setOptions] = useState<PickerOption[]>([]);
  const loaded = useRef(false);
  const loadRef = useRef(load);
  loadRef.current = load;

  const onDropdownVisibleChange = useCallback((visible: boolean) => {
    if (!visible || loaded.current) {
      return;
    }
    loaded.current = true;
    void loadRef.current().then(setOptions, () => {
      loaded.current = false;
    });
  }, []);

  return { options, onDropdownVisibleChange };
}

export function TaskPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState<TaskListParams>(DEFAULT_PARAMS);
  const [items, setItems] = useState<TaskListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [helpVisible, setHelpVisible] = useState(false);
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null);
  const [detailScheduleId, setDetailScheduleId] = useState<string | null>(null);
  const related = useRelatedDetail();
  const requestSeq = useRef(0);

  const [searchParams] = useSearchParams();
  const deepLinkTaskId = searchParams.get('taskId');
  const deepLinkScheduleId = searchParams.get('scheduleId');

  // 深链（审计「关联 Task」、定时任务「历史查看全部」）= 一次性初值，不回写 URL：
  // 写回会让关闭详情/清除筛选与地址栏互相拉扯，而刷新本就该重新定位同一记录。
  useEffect(() => {
    if (deepLinkTaskId) {
      setDetailTaskId(deepLinkTaskId);
    }
  }, [deepLinkTaskId]);

  useEffect(() => {
    if (!deepLinkScheduleId) {
      return;
    }
    setParams((prev) =>
      prev.schedule_id === deepLinkScheduleId
        ? prev
        : { ...prev, schedule_id: deepLinkScheduleId, page: 1 }
    );
  }, [deepLinkScheduleId]);

  // 三个筛选下拉的可选项：首次展开时各自按需加载。
  const agentPicker = usePickerOptions(
    useCallback(
      () =>
        listAgentsForPicker().then((page) =>
          page.items.map((agent) => ({ value: agent.id, label: `${agent.name} (${agent.key})` }))
        ),
      []
    )
  );
  const actorPicker = usePickerOptions(
    useCallback(
      () =>
        listUsers({ page: 1, page_size: PICKER_PAGE_SIZE }).then((page) =>
          page.items.map((user) => ({
            value: user.id,
            label: `${user.display_name} (${user.user_code})`
          }))
        ),
      []
    )
  );
  const skillPicker = usePickerOptions(
    useCallback(
      () =>
        listSkills({ page: 1, page_size: PICKER_PAGE_SIZE }).then((page) =>
          page.items.map((skill) => ({ value: skill.id, label: `${skill.name} (${skill.key})` }))
        ),
      []
    )
  );

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listTasks(params);
      if (current !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
      setFailed(false);
    } catch {
      if (current === requestSeq.current) {
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const taskActions = useTaskActions({
    onCancelled: () => {
      void reload();
    }
  });

  const statusOptions = Object.fromEntries(
    TASK_STATUSES.map((status) => [
      status,
      { color: STATUS_COLORS[status] ?? 'grey', label: t(`task.status.${status}`) }
    ])
  );

  const setFilter = useCallback((patch: TaskListParams) => {
    setParams((prev) => ({ ...prev, ...patch, page: 1 }));
  }, []);

  return (
    <>
      <PageHeader title={t('task.title')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button data-testid="task-help" onClick={() => setHelpVisible(true)}>
              {t('task.help.action')}
            </Button>
          }
          search={
            <>
              <Select
                data-testid="task-filter-status"
                value={params.status ?? undefined}
                style={{ width: 120 }}
                showClear
                placeholder={t('task.filter.status')}
                optionList={TASK_STATUSES.map((status) => ({
                  value: status,
                  label: t(`task.status.${status}`)
                }))}
                onChange={(value) =>
                  setFilter({ status: (value as TaskListParams['status']) ?? undefined })
                }
              />
              <Select
                data-testid="task-filter-trigger"
                value={params.trigger_type ?? undefined}
                style={{ width: 120 }}
                showClear
                placeholder={t('task.filter.triggerType')}
                optionList={TRIGGER_TYPES.map((trigger) => ({
                  value: trigger,
                  label: t(`task.trigger.${trigger}`)
                }))}
                onChange={(value) =>
                  setFilter({ trigger_type: (value as TaskListParams['trigger_type']) ?? undefined })
                }
              />
              <Select
                data-testid="task-filter-agent"
                value={params.agent_id ?? undefined}
                style={{ width: 160 }}
                showClear
                filter
                placeholder={t('task.filter.agent')}
                optionList={agentPicker.options}
                onDropdownVisibleChange={agentPicker.onDropdownVisibleChange}
                onChange={(value) => setFilter({ agent_id: (value as string) ?? undefined })}
              />
              <Select
                data-testid="task-filter-actor"
                value={params.actor_user_id ?? undefined}
                style={{ width: 160 }}
                showClear
                filter
                placeholder={t('task.filter.actorUser')}
                optionList={actorPicker.options}
                onDropdownVisibleChange={actorPicker.onDropdownVisibleChange}
                onChange={(value) => setFilter({ actor_user_id: (value as string) ?? undefined })}
              />
              <Select
                data-testid="task-filter-skill"
                value={params.skill_id ?? undefined}
                style={{ width: 160 }}
                showClear
                filter
                placeholder={t('task.filter.skill')}
                optionList={skillPicker.options}
                onDropdownVisibleChange={skillPicker.onDropdownVisibleChange}
                onChange={(value) => setFilter({ skill_id: (value as string) ?? undefined })}
              />
              <DatePicker
                data-testid="task-filter-create"
                type="dateRange"
                style={{ width: 220 }}
                placeholder={[t('task.filter.createdFrom'), t('task.filter.createdTo')]}
                onChange={(value) => {
                  const range = Array.isArray(value) ? (value as Date[]) : [];
                  setFilter({
                    start_time: toIsoDate(range[0]),
                    end_time: toIsoEndOfDay(range[1])
                  });
                }}
              />
              <DatePicker
                data-testid="task-filter-deadline"
                type="dateRange"
                style={{ width: 220 }}
                placeholder={[t('task.filter.deadlineFrom'), t('task.filter.deadlineTo')]}
                onChange={(value) => {
                  const range = Array.isArray(value) ? (value as Date[]) : [];
                  setFilter({
                    deadline_from: toIsoDate(range[0]),
                    deadline_to: toIsoEndOfDay(range[1])
                  });
                }}
              />
              <ListActionButton
                data-testid="task-reset"
                onClick={() => setParams(DEFAULT_PARAMS)}
                action="reset"
              />
              <ListActionButton onClick={() => void reload()} action="refresh" />
            </>
          }
        />
        {params.schedule_id ? (
          <div style={{ display: 'flex', marginBottom: 8 }}>
            <Tag
              data-testid="task-filter-schedule"
              closable
              onClose={() => setFilter({ schedule_id: undefined })}
            >
              {`${t('task.filter.schedule')}: ${params.schedule_id}`}
            </Tag>
          </div>
        ) : null}
        <RemoteTable<TaskListItem>
          rowKey="task_id"
          className="app-table-nowrap"
          scroll={{ x: 2220 }}
          loading={loading}
          columns={[
            {
              title: t('task.columns.taskId'),
              dataIndex: 'task_id',
              width: 320,
              render: (value: string) => (
                <EntityLink testId={`task-link-${value}`} onClick={() => setDetailTaskId(value)}>
                  {value}
                </EntityLink>
              )
            },
            { title: t('task.columns.intent'), dataIndex: 'intent_key', width: 90 },
            {
              title: t('task.columns.agent'),
              dataIndex: 'agent_id',
              width: 190,
              render: (value: string) => <span title={value}>{value}</span>
            },
            {
              title: t('task.columns.actorUser'),
              dataIndex: 'actor_user_id',
              width: 190,
              render: (value: string) => <span title={value}>{value}</span>
            },
            {
              title: t('task.columns.skill'),
              dataIndex: 'skill_id',
              width: 190,
              render: (value: string) => <span title={value}>{value}</span>
            },
            {
              title: t('task.columns.triggerType'),
              dataIndex: 'trigger_type',
              width: 90,
              render: (value: string) => t(`task.trigger.${value}`)
            },
            {
              title: t('task.columns.status'),
              dataIndex: 'status',
              width: 92,
              render: (value: string) => <StatusTag status={value} options={statusOptions} />
            },
            {
              title: t('task.columns.childProgress'),
              dataIndex: 'child_finished',
              width: 88,
              render: (_: unknown, record: TaskListItem) => (
                <span data-testid={`task-children-${record.task_id}`}>
                  {`${record.child_finished ?? 0}/${record.child_total ?? 0}`}
                </span>
              )
            },
            {
              title: t('task.columns.startedAt'),
              dataIndex: 'started_at',
              width: 185,
              render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
            },
            {
              title: t('task.columns.finishedAt'),
              dataIndex: 'finished_at',
              width: 185,
              render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
            },
            {
              title: t('task.columns.deadlineAt'),
              dataIndex: 'deadline_at',
              width: 185,
              render: (value: string, record: TaskListItem) => (
                <span data-testid={`task-deadline-${record.task_id}`}>
                  <DateTimeText value={value} />
                </span>
              )
            },
            {
              title: t('task.columns.deliveryStatus'),
              dataIndex: 'delivery_status',
              width: 96,
              render: (value: string) => t(`task.delivery.${value}`)
            },
            {
              title: t('task.columns.error'),
              dataIndex: 'error_message',
              width: 220,
              render: (_: unknown, record: TaskListItem) => record.error_message ?? record.error_code ?? '-'
            },
            {
              title: t('task.columns.actions'),
              width: 96,
              render: (_: unknown, record: TaskListItem) =>
                CANCELLABLE_STATUSES.includes(record.status) && !record.cancel_requested ? (
                  <ConfirmAction
                    danger
                    title={t('task.cancel.confirm')}
                    testId={`task-row-cancel-${record.task_id}`}
                    onConfirm={() => void taskActions.cancel(record.task_id)}
                  >
                    {taskActions.cancellingTaskId === record.task_id
                      ? t('common.loading')
                      : t('task.cancel.action')}
                  </ConfirmAction>
                ) : (
                  '-'
                )
            }
          ]}
          dataSource={items}
          page={params.page ?? 1}
          pageSize={params.page_size ?? 10}
          total={total}
          onPageChange={(page) => setParams((prev) => ({ ...prev, page }))}
          onPageSizeChange={(page_size) => setParams((prev) => ({ ...prev, page: 1, page_size }))}
          empty={
            failed ? (
              <ErrorState onRetry={() => void reload()} />
            ) : (
              <EmptyState title={t('common.empty')} description={t('common.emptyHint')} />
            )
          }
        />
      </PageSection>
      <Modal
        centered
        visible={helpVisible}
        title={t('task.help.title')}
        footer={null}
        onCancel={() => setHelpVisible(false)}
      >
        <p data-testid="task-help-content">{t('task.help.content')}</p>
      </Modal>
      <TaskDetailSideSheet
        taskId={detailTaskId}
        onCancel={() => setDetailTaskId(null)}
        onSelectTask={(taskId) => setDetailTaskId(taskId)}
        onOpenSchedule={(scheduleId) => setDetailScheduleId(scheduleId)}
        onOpenRun={(runId) => related.openRun(runId)}
        onMutated={() => void reload()}
      />
      {/* 反向链接的落点：任务详情打开定时任务详情（嵌套 SideSheet 与 mcp 工具详情同形）。 */}
      <ScheduleDetailSideSheet
        scheduleId={detailScheduleId}
        onCancel={() => setDetailScheduleId(null)}
        onOpenTask={(taskId) => setDetailTaskId(taskId)}
      />
      {/* 同一条来源线的另一半：对话派生的任务回看它那次 Run。关联层由控制器互斥承载：
          Run→Task 关闭关联 Run、复用页面主 Task 面板（不递归堆叠）。 */}
      <RelatedDetailController
        state={related.state}
        onClose={related.close}
        onOpenRun={related.openRun}
        onOpenTask={(taskId) => {
          related.close();
          setDetailTaskId(taskId);
        }}
        onMutated={() => void reload()}
      />
    </>
  );
}
