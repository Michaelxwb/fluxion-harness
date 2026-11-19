import { actionColumn } from '../../components/common/tableLayout';
import { ScheduleRowActions } from './ScheduleRowActions';
import { Select, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';

import { ListActionButton } from '../../components/common/ListActionButton';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { TableText } from '../../components/common/TableText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { EntityLink } from '../../components/common/EntityLink';
import { StatusTag } from '../../components/common/StatusTag';
import { ScheduleDetailSideSheet } from './ScheduleDetailSideSheet';
import { EntityNameText } from './EntityNameText';
import { TaskDetailSideSheet } from './TaskDetailSideSheet';
import { listAgentsForPicker, listUsers } from '../user-identity/services/users';
import {
  fireStatusLabel,
  scheduleModeLabel,
  scheduleStatusFallback,
  scheduleStatusOptions,
  SCHEDULE_STATUSES,
  type Translate
} from './scheduleCatalog';
import { listSchedules, type ScheduleListItem, type ScheduleListParams } from './services/schedules';

const DEFAULT_PARAMS: ScheduleListParams = { page: 1, page_size: 15 };
/** 下拉可选项一次取够（后端 page_size 上限 100），超出部分无法在此选择。 */
const PICKER_PAGE_SIZE = 100;

interface PickerOption {
  value: string;
  label: string;
}

/**
 * 筛选下拉的可选项**首次展开时才取**：与任务列表同口径，避免进页面就多发请求；
 * 失败由共享 ApiClient 拦截器提示，下拉留空、下次展开可重试，不升级成页面级失败。
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
    if (!visible || loaded.current) return;
    loaded.current = true;
    void loadRef.current().then(setOptions, () => {
      loaded.current = false;
    });
  }, []);
  return { options, onDropdownVisibleChange };
}

/** 待处理徽标：等待回答（pending_human）与需关注（needs_attention）是两类不同事实。 */
function AttentionBadge(props: { record: ScheduleListItem; t: Translate }) {
  if (props.record.pending_human) {
    return (
      <Tag data-testid={`schedule-pending-human-${props.record.schedule_id}`} color="amber">
        {props.t('schedule.badge.pendingHuman')}
      </Tag>
    );
  }
  if (props.record.needs_attention) {
    return (
      <Tag data-testid={`schedule-needs-attention-${props.record.schedule_id}`} color="red">
        {props.t('schedule.badge.needsAttention')}
      </Tag>
    );
  }
  return <span>-</span>;
}

/**
 * 定时任务列表（design §3.10）：无创建入口；状态、模式、下次/最近触发与待处理标记
 * 全部来自后端投影。深链 `?scheduleId=` 仍落到本页并直接打开详情。
 */
export function SchedulePage() {
  const { t } = useTranslation();
  const translate: Translate = (key) => t(key);
  const [params, setParams] = useState<ScheduleListParams>(DEFAULT_PARAMS);
  const [items, setItems] = useState<ScheduleListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detailScheduleId, setDetailScheduleId] = useState<string | null>(null);
  const [historyTaskId, setHistoryTaskId] = useState<string | null>(null);
  const [detailReloadKey, setDetailReloadKey] = useState(0);
  const requestSeq = useRef(0);

  // 深链（概览「下一批定时触发」= `/schedules?scheduleId=…`）：一次性初值，不回写 URL。
  // 目标已删/越权时由详情侧渲染 404 态，不静默落回空列表（e2e E-04）。
  const [searchParams] = useSearchParams();
  const deepLinkScheduleId = searchParams.get('scheduleId');

  const agentPicker = usePickerOptions(
    useCallback(
      () =>
        listAgentsForPicker({ errorTip: 'always' }).then((page) =>
          page.items.map((agent) => ({ value: agent.id, label: `${agent.name} (${agent.key})` }))
        ),
      []
    )
  );
  const actorPicker = usePickerOptions(
    useCallback(
      () =>
        listUsers({ page: 1, page_size: PICKER_PAGE_SIZE }, { errorTip: 'always' }).then((page) =>
          page.items.map((user) => ({
            value: user.id,
            label: `${user.display_name} (${user.user_code})`
          }))
        ),
      []
    )
  );

  useEffect(() => {
    if (deepLinkScheduleId) {
      setDetailScheduleId(deepLinkScheduleId);
    }
  }, [deepLinkScheduleId]);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listSchedules(params);
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

  const onScheduleDeleted = (scheduleId: string): void => {
    if (detailScheduleId === scheduleId) setDetailScheduleId(null);
    if (items.length === 1 && (params.page ?? 1) > 1) {
      setParams((prev) => ({ ...prev, page: (prev.page ?? 1) - 1 }));
    } else {
      void reload();
    }
  };

  const statusOptions = scheduleStatusOptions(translate);

  return (
    <>
      <PageHeader title={t('schedule.title')} />
      <PageSection>
        <ModuleToolbar
          search={
            <>
              <Select
                data-testid="schedule-filter-status"
                value={params.status ?? undefined}
                style={{ width: 150 }}
                showClear
                placeholder={t('schedule.filter.status')}
                optionList={SCHEDULE_STATUSES.map((status) => ({
                  value: status,
                  label: scheduleStatusFallback(translate, status).label
                }))}
                onChange={(value) =>
                  setParams((prev) => ({
                    ...prev,
                    status: (value as ScheduleListParams['status']) ?? undefined,
                    page: 1
                  }))
                }
              />
              <Select
                data-testid="schedule-filter-agent"
                value={params.agent_id ?? undefined}
                style={{ width: 160 }}
                showClear
                filter
                placeholder={t('schedule.filter.agent')}
                optionList={agentPicker.options}
                onDropdownVisibleChange={agentPicker.onDropdownVisibleChange}
                onChange={(value) =>
                  setParams((prev) => ({
                    ...prev,
                    agent_id: (value as string) ?? undefined,
                    page: 1
                  }))
                }
              />
              <Select
                data-testid="schedule-filter-user"
                value={params.actor_user_id ?? undefined}
                style={{ width: 160 }}
                showClear
                filter
                placeholder={t('schedule.filter.user')}
                optionList={actorPicker.options}
                onDropdownVisibleChange={actorPicker.onDropdownVisibleChange}
                onChange={(value) =>
                  setParams((prev) => ({
                    ...prev,
                    actor_user_id: (value as string) ?? undefined,
                    page: 1
                  }))
                }
              />
              <ListActionButton
                data-testid="schedule-reset"
                onClick={() => setParams(DEFAULT_PARAMS)}
                action="reset"
              />
              <ListActionButton onClick={() => void reload()} action="refresh" />
            </>
          }
        />
        <RemoteTable<ScheduleListItem>
          rowKey="schedule_id"
          className="schedule-table app-table-fit app-table-nowrap"
          columnGrowth={{ name: 1 }}
          loading={loading}
          columns={[
            {
              title: t('schedule.columns.name'),
              dataIndex: 'name',
              width: 220,
              ellipsis: true,
              render: (value: string, record: ScheduleListItem) => (
                <EntityLink
                  testId={`schedule-link-${record.schedule_id}`}
                  onClick={() => setDetailScheduleId(record.schedule_id)}
                >
                  <TableText>{value}</TableText>
                </EntityLink>
              )
            },
            {
              title: t('schedule.columns.agent'),
              dataIndex: 'agent_id',
              width: 110,
              ellipsis: true,
              render: (_: unknown, record: ScheduleListItem) => (
                <EntityNameText name={record.agent_name} id={record.agent_id} />
              )
            },
            {
              title: t('schedule.columns.user'),
              dataIndex: 'actor_user_id',
              width: 110,
              ellipsis: true,
              render: (_: unknown, record: ScheduleListItem) => (
                <EntityNameText name={record.actor_name} id={record.actor_user_id} />
              )
            },
            {
              title: t('schedule.columns.mode'),
              dataIndex: 'mode',
              width: 80,
              render: (value: string) => scheduleModeLabel(translate, value)
            },
            { title: t('schedule.columns.timezone'), dataIndex: 'timezone', width: 100, ellipsis: true },
            {
              title: t('schedule.columns.status'),
              dataIndex: 'status',
              width: 80,
              render: (value: string) => (
                <StatusTag
                  status={value}
                  options={statusOptions}
                  fallback={scheduleStatusFallback(translate, value)}
                />
              )
            },
            {
              title: t('schedule.columns.attention'),
              dataIndex: 'pending_human',
              width: 80,
              render: (_: unknown, record: ScheduleListItem) => (
                <AttentionBadge record={record} t={translate} />
              )
            },
            {
              title: t('schedule.columns.nextFireAt'),
              dataIndex: 'next_fire_at',
              width: 170,
              render: (value: string | null, record: ScheduleListItem) => (
                <span data-testid={`schedule-next-fire-${record.schedule_id}`}>
                  {value ? <DateTimeText value={value} /> : '-'}
                </span>
              )
            },
            {
              title: t('schedule.columns.lastFireAt'),
              dataIndex: 'last_fire',
              width: 210,
              render: (value: ScheduleListItem['last_fire']) =>
                value ? (
                  <span
                    style={{
                      display: 'inline-flex',
                      flexDirection: 'row',
                      gap: 6,
                      alignItems: 'center',
                      whiteSpace: 'nowrap'
                    }}
                  >
                    <DateTimeText value={value.scheduled_at} />
                    <StatusTag
                      status={value.status}
                      options={{
                        [value.status]: {
                          color: 'grey',
                          label: fireStatusLabel(translate, value.status)
                        }
                      }}
                    />
                  </span>
                ) : (
                  '-'
                )
            },
            {
              ...actionColumn(150),
              title: t('schedule.columns.actions'),
              render: (_: unknown, record: ScheduleListItem) => (
                <ScheduleRowActions
                  schedule={record}
                  onMutated={() => {
                    setDetailReloadKey((value) => value + 1);
                    void reload();
                  }}
                  onDeleted={() => onScheduleDeleted(record.schedule_id)}
                />
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
              <EmptyState title={t('common.empty')} description={t('schedule.empty')} />
            )
          }
        />
      </PageSection>
      <ScheduleDetailSideSheet
        scheduleId={detailScheduleId}
        reloadKey={detailReloadKey}
        onCancel={() => setDetailScheduleId(null)}
        onOpenTask={(taskId) => setHistoryTaskId(taskId)}
        closeOnEsc={historyTaskId === null}
      />
      <TaskDetailSideSheet
        taskId={historyTaskId}
        onCancel={() => setHistoryTaskId(null)}
        onSelectTask={(taskId) => setHistoryTaskId(taskId)}
        closeOnEsc
      />
    </>
  );
}
