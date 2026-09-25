/**
 * 审计列表表格供给（设计 §3.3 CMP-02「标准字段列，主展示字段打开详情」、§3.6 列表状态、§3.7 字段顺序）。
 *
 * 仓库级冻结 verifier 要求每个模块列表页（`*Page.tsx`）保留公共 `RemoteTable`（内置
 * `PaginationFooter`），因此本文件以「RemoteTable 入参工厂」形态供给：列定义（docs/15 口径）、
 * 行渲染（`StatusTag`/`DateTimeText`/`EntityLink`）、空态与错误态槽位、以及 page/pageSize/total 的
 * footer 联动，全部在这里产出，由 `AuditPage` 原样展开给 `RemoteTable`（不整表替换、不在页面重复套壳）。
 */

import { Button } from '@douyinfe/semi-ui';
import type { TFunction } from 'i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { EmptyState } from '../../../components/common/EmptyState';
import { EntityLink } from '../../../components/common/EntityLink';
import { ErrorState } from '../../../components/common/ErrorState';
import type { RemoteTableProps } from '../../../components/common/RemoteTable';
import { StatusTag, type StatusTagOption } from '../../../components/common/StatusTag';
import type { AuditListItem } from '../types';

/** 列表渲染入参：设计 §3.4 的 `AuditTableProps` + 列表状态机的失败/重试/清筛选出口。 */
export interface AuditTableOptions {
  items: AuditListItem[];
  loading: boolean;
  /** 服务端回显页码（`useAuditList` 以响应为准，见 hooks/useAuditList.ts）。 */
  page: number;
  pageSize: number;
  /** 服务端总数：footer 的区间文案与总页数都由它驱动。 */
  total: number;
  failed: boolean;
  onPageChange(page: number, pageSize: number): void;
  /** 主展示字段/Trace ID 打开详情的 seam（TASK-013 的详情 SideSheet 消费）。 */
  onOpenDetail(item: AuditListItem): void;
  /** 失败态重试（[E-06] 保留筛选条件）。 */
  onRetry(): void;
  /** 空态「清筛选」出口（设计 §3.6）。 */
  onReset(): void;
  t: TFunction;
}

/** 列数组契约：与 `RemoteTableProps.columns` 同口径（去掉可选），拆出的列片段共用同一返回类型。 */
type AuditTableColumns = NonNullable<RemoteTableProps<AuditListItem>['columns']>;

/** 列定义：UI 名称与字段口径见 docs/15 §2「运行审计」段，列序与交互稿一致（设计 §3.7）。 */
function buildAuditColumns(
  t: TFunction,
  onOpenDetail: (item: AuditListItem) => void
): AuditTableColumns {
  return [
    ...buildIdentityColumns(t),
    ...buildTargetColumns(t, onOpenDetail),
    ...buildResultAndTraceColumns(t, onOpenDetail)
  ];
}

/** 时间 / 审计类型 / 操作用户 / Agent：身份与归类字段（设计 §3.7 列序前四列）。 */
function buildIdentityColumns(t: TFunction): AuditTableColumns {
  return [
    {
      title: t('audit.columns.time'),
      dataIndex: 'occurredAt',
      render: (value: string) => <DateTimeText value={value} />
    },
    {
      title: t('audit.columns.auditType'),
      dataIndex: 'auditType',
      render: (value: AuditListItem['auditType']) => t(`audit.auditType.${value}`)
    },
    {
      title: t('audit.columns.actor'),
      dataIndex: 'actorName',
      render: (_: unknown, record: AuditListItem) => record.actorName ?? record.actorUserId
    },
    {
      title: t('audit.columns.agent'),
      dataIndex: 'agentId',
      render: (_: unknown, record: AuditListItem) => record.agentName ?? record.agentId ?? '-'
    }
  ];
}

/** 操作目标 / 动作：主展示字段在此，点击打开只读详情（设计 §3.3.1）。 */
function buildTargetColumns(
  t: TFunction,
  onOpenDetail: (item: AuditListItem) => void
): AuditTableColumns {
  return [
    {
      // 主展示字段（docs/15「操作目标」）：点击打开只读详情（设计 §3.3.1）。
      title: t('audit.columns.target'),
      dataIndex: 'resourceId',
      render: (value: string, record: AuditListItem) => (
        <EntityLink testId={`audit-link-${record.auditId}`} onClick={() => onOpenDetail(record)}>
          {value}
        </EntityLink>
      )
    },
    { title: t('audit.columns.action'), dataIndex: 'action' }
  ];
}

/** 执行结果 / Trace ID：结果由 `StatusTag` 承载，Trace 同样打开详情（设计 §3.3.1）。 */
function buildResultAndTraceColumns(
  t: TFunction,
  onOpenDetail: (item: AuditListItem) => void
): AuditTableColumns {
  const statusOptions: Record<string, StatusTagOption> = {
    SUCCESS: { color: 'green', label: t('audit.resultStatus.SUCCESS') },
    FAILED: { color: 'red', label: t('audit.resultStatus.FAILED') },
    DENIED: { color: 'red', label: t('audit.resultStatus.DENIED') }
  };

  return [
    {
      title: t('audit.columns.result'),
      dataIndex: 'resultStatus',
      render: (value: string) => <StatusTag status={value} options={statusOptions} />
    },
    {
      title: t('audit.columns.traceId'),
      dataIndex: 'traceId',
      render: (value: string | undefined, record: AuditListItem) =>
        value ? (
          <EntityLink testId={`audit-trace-${record.auditId}`} onClick={() => onOpenDetail(record)}>
            {value}
          </EntityLink>
        ) : (
          '-'
        )
    }
  ];
}

/** 供给 `RemoteTable` 的完整入参：列/行渲染、空态与错误态槽位、分页联动（footer 反映服务端 total）。 */
export function buildAuditTableProps(
  options: AuditTableOptions
): RemoteTableProps<AuditListItem> {
  const { t } = options;
  return {
    rowKey: 'auditId',
    loading: options.loading,
    columns: buildAuditColumns(t, options.onOpenDetail),
    dataSource: options.items,
    page: options.page,
    pageSize: options.pageSize,
    total: options.total,
    onPageChange: (page) => options.onPageChange(page, options.pageSize),
    onPageSizeChange: (pageSize) => options.onPageChange(1, pageSize),
    empty: options.failed ? (
      <ErrorState onRetry={options.onRetry} />
    ) : (
      // 设计 §3.6：空态提供「清筛选」出口。
      <EmptyState
        title={t('common.empty')}
        description={t('common.emptyHint')}
        action={<Button onClick={options.onReset}>{t('common.reset')}</Button>}
      />
    )
  };
}
