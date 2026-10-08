import { Table } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { EntityLink } from '../../../components/common/EntityLink';
import { StatusTag } from '../../../components/common/StatusTag';
import type { RunOperationOutline } from '../services/runs';
import {
  completionModeLabel,
  errorPhaseLabel,
  operationStatusFallback,
  operationStatusOptions,
  taskStatusFallback,
  taskStatusOptions
} from '../statusOptions';

export interface RunOperationTableProps {
  rows: readonly RunOperationOutline[];
  /**
   * 点击「查看任务」的上抛意图；**组件不导航、不改 props**。`task_id` 为空时两处
   * （任务单元格与末列操作）都不可点击——不为未受理的提交造假链接。
   */
  onOpenTask?(taskId: string): void;
}

/** 关联操作局部清单（设计 §3.3 CMP-02）：详情 Tab 内的局部表格，不自行 HTTP。 */
export function RunOperationTable(props: RunOperationTableProps) {
  const { t } = useTranslation();
  const statusOptions = operationStatusOptions(t);
  const taskOptions = taskStatusOptions(t);

  return (
    <Table
      rowKey="operation_id"
      dataSource={[...props.rows]}
      pagination={false}
      size="small"
      data-testid="run-operation-table"
      columns={[
        {
          title: t('run.operation.columns.operationId'),
          dataIndex: 'operation_id',
          width: 300,
          render: (value: string) => (
            <span className="mono" title={value}>
              {value}
            </span>
          )
        },
        {
          title: t('run.operation.columns.mode'),
          dataIndex: 'completion_mode',
          width: 160,
          render: (value: string) => completionModeLabel(t, value)
        },
        {
          title: t('run.operation.columns.status'),
          dataIndex: 'status',
          width: 200,
          render: (_: unknown, record: RunOperationOutline) => (
            <span data-testid={`run-operation-status-${record.operation_id}`}>
              <StatusTag
                status={record.status}
                options={statusOptions}
                fallback={operationStatusFallback(t, record.status)}
              />
              {record.error_phase ? (
                <span className="detail-hint">
                  {errorPhaseLabel(t, record.error_phase)}
                  {record.error_code ? ` · ${record.error_code}` : ''}
                </span>
              ) : null}
            </span>
          )
        },
        {
          title: t('run.operation.columns.task'),
          dataIndex: 'task_id',
          width: 320,
          render: (_: unknown, record: RunOperationOutline) =>
            record.task_id ? (
              <span data-testid={`run-operation-task-cell-${record.operation_id}`}>
                {props.onOpenTask ? (
                  <EntityLink
                    testId={`run-operation-task-${record.task_id}`}
                    onClick={() => props.onOpenTask?.(record.task_id as string)}
                  >
                    {record.task_id}
                  </EntityLink>
                ) : (
                  <span className="mono" title={record.task_id}>
                    {record.task_id}
                  </span>
                )}
                {record.task_status ? (
                  <StatusTag
                    status={record.task_status}
                    options={taskOptions}
                    fallback={taskStatusFallback(t, record.task_status)}
                  />
                ) : null}
              </span>
            ) : (
              <span data-testid={`run-operation-no-task-${record.operation_id}`}>
                {record.status === 'SUBMIT_PENDING'
                  ? t('run.operation.submissionPending')
                  : t('run.operation.noTask')}
              </span>
            )
        },
        {
          title: t('run.operation.columns.submittedAt'),
          dataIndex: 'submitted_at',
          width: 185,
          render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
        },
        {
          title: t('run.operation.columns.completedAt'),
          dataIndex: 'completed_at',
          width: 185,
          render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
        },
        {
          title: t('run.operation.columns.actions'),
          width: 96,
          render: (_: unknown, record: RunOperationOutline) =>
            record.task_id && props.onOpenTask ? (
              <EntityLink
                testId={`run-operation-view-task-${record.task_id}`}
                onClick={() => props.onOpenTask?.(record.task_id as string)}
              >
                {t('run.operation.viewTask')}
              </EntityLink>
            ) : (
              '-'
            )
        }
      ]}
    />
  );
}
