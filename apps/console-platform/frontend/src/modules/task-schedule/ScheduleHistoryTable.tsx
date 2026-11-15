import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { EntityLink } from '../../components/common/EntityLink';
import { ErrorState } from '../../components/common/ErrorState';
import { RemoteTable } from '../../components/common/RemoteTable';
import { StatusTag } from '../../components/common/StatusTag';
import {
  dstEventLabel,
  dstEventSummary,
  dstResolutionLabel,
  fireHistoryRows,
  fireStatusLabel,
  fireStatusOptions,
  isFireRow,
  skipReasonLabel,
  taskStatusLabel,
  taskStatusOptions,
  unknownLabel,
  type FireHistoryRow,
  type Translate
} from './scheduleCatalog';
import { listScheduleFires } from './services/schedules';

export interface ScheduleHistoryTableProps {
  scheduleId: string;
  onOpenTask(taskId: string): void;
}

/**
 * 调度历史 = 真实 Fire（每次 occurrence 的处理结果）+ DST 事件（不产生 Fire 的跳过事实）。
 *
 * 两类事实在同一次分页响应里分开返回，这里合并排序但用 `kind` 区分：DST 行不显示执行链接、
 * 不计入 Fire 状态，也不伪造 `scheduled_at`。
 */
export function ScheduleHistoryTable(props: ScheduleHistoryTableProps) {
  const { t } = useTranslation();
  // 词条出口适配：纯映射层只依赖 `(key) => string`，不 import i18next 运行时。
  const translate: Translate = (key) => t(key);
  const [rows, setRows] = useState<FireHistoryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const result = await listScheduleFires(props.scheduleId, {
        page,
        page_size: pageSize
      });
      if (current !== requestSeq.current) {
        return;
      }
      setRows(fireHistoryRows(result));
      setTotal(result.total);
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
  }, [props.scheduleId, page, pageSize]);

  useEffect(() => {
    setPage(1);
  }, [props.scheduleId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const statusOptions = fireStatusOptions(translate);
  const executionOptions = taskStatusOptions(translate);

  return (
    <div data-testid="schedule-history">
      <RemoteTable<FireHistoryRow>
        rowKey="key"
        loading={loading}
        columns={[
          {
            key: 'time',
            title: t('schedule.fire.columns.time'),
            dataIndex: 'at',
            render: (value: string, row: FireHistoryRow) => (
              <span data-testid={`${row.kind.toLowerCase()}-row-time-${row.key}`}>
                <DateTimeText value={value} />
              </span>
            )
          },
          {
            key: 'status',
            title: t('schedule.fire.columns.status'),
            dataIndex: 'kind',
            render: (_: unknown, row: FireHistoryRow) =>
              isFireRow(row) ? (
                <StatusTag
                  status={row.fire.status}
                  options={statusOptions}
                  fallback={{
                    color: 'grey',
                    label: fireStatusLabel(translate, row.fire.status)
                  }}
                />
              ) : (
                <StatusTag
                  status={row.event.event_type}
                  options={{}}
                  fallback={{
                    color: 'grey',
                    label: dstEventLabel(translate, row.event.event_type)
                  }}
                />
              )
          },
          {
            key: 'skipReason',
            title: t('schedule.fire.columns.skipReason'),
            dataIndex: 'at',
            render: (_: unknown, row: FireHistoryRow) =>
              isFireRow(row) ? skipReasonLabel(translate, row.fire.skip_reason) : '-'
          },
          {
            key: 'missedCount',
            title: t('schedule.fire.columns.missedCount'),
            dataIndex: 'at',
            render: (_: unknown, row: FireHistoryRow) =>
              isFireRow(row) ? String(row.fire.missed_count) : '-'
          },
          {
            key: 'dst',
            title: t('schedule.fire.columns.dst'),
            dataIndex: 'at',
            render: (_: unknown, row: FireHistoryRow) =>
              isFireRow(row) ? (
                dstResolutionLabel(translate, row.fire.dst_resolution)
              ) : (
                <span data-testid={`dst-event-${row.event.event_id}`}>
                  {dstEventSummary(row.event)}
                </span>
              )
          },
          {
            key: 'admissionError',
            title: t('schedule.fire.columns.admissionError'),
            dataIndex: 'at',
            render: (_: unknown, row: FireHistoryRow) =>
              isFireRow(row) && row.fire.admission_error_code
                ? unknownLabel(translate, 'schedule.fire', row.fire.admission_error_code)
                : '-'
          },
          {
            key: 'execution',
            title: t('schedule.fire.columns.execution'),
            dataIndex: 'at',
            render: (_: unknown, row: FireHistoryRow) => {
              if (!isFireRow(row) || !row.fire.execution_id) {
                return '-';
              }
              return (
                <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                  <EntityLink
                    testId={`fire-execution-${row.fire.fire_id}`}
                    onClick={() => props.onOpenTask(row.fire.execution_id as string)}
                  >
                    {row.fire.execution_id.slice(0, 8)}
                  </EntityLink>
                  {row.fire.execution_status ? (
                    <StatusTag
                      status={row.fire.execution_status}
                      options={executionOptions}
                      fallback={{
                        color: 'grey',
                        label: taskStatusLabel(translate, row.fire.execution_status)
                      }}
                    />
                  ) : null}
                </span>
              );
            }
          }
        ]}
        dataSource={rows}
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPage(1);
          setPageSize(size);
        }}
        empty={
          failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : (
            <EmptyState title={t('common.empty')} description={t('schedule.fire.empty')} />
          )
        }
      />
    </div>
  );
}
