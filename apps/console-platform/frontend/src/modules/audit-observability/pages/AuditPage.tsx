/**
 * 运行审计列表页容器（设计 §3.2 `/audits`、§3.3 CMP-01、§3.5 状态划分）。
 *
 * 本页持有筛选/分页/详情选择状态，渲染 `ModuleToolbar`（左上主操作位 + 右上筛选栏）与列表；
 * 取数只经 TASK-010 的 service 层（共享 api client，组件不裸用 HTTP 客户端）。
 * 表格渲染与详情 SideSheet 归 TASK-012/013：本页导出两者需满足的 props 契约（设计 §3.4），
 * 并由 TASK-014 在左上主操作位接入导出按钮。
 */

import { Button } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageSection } from '../../../components/common/ConsolePage';
import { DateTimeText } from '../../../components/common/DateTimeText';
import { EmptyState } from '../../../components/common/EmptyState';
import { EntityLink } from '../../../components/common/EntityLink';
import { ErrorState } from '../../../components/common/ErrorState';
import { ModuleToolbar } from '../../../components/common/ModuleToolbar';
import { RemoteTable } from '../../../components/common/RemoteTable';
import { StatusTag, type StatusTagOption } from '../../../components/common/StatusTag';
import { AuditFilterBar } from '../components/AuditFilterBar';
import { listAudits } from '../services/auditService';
import { AUDIT_PAGE_SIZE_DEFAULT, type AuditListItem, type AuditListQuery } from '../types';

/** TASK-012 `components/AuditTable.tsx` 的入参（设计 §3.4）。 */
export interface AuditTableProps {
  items: AuditListItem[];
  loading: boolean;
  page: number;
  pageSize: number;
  total: number;
  onPageChange(page: number, pageSize: number): void;
  onOpenDetail(item: AuditListItem): void;
}

/** TASK-013 `components/AuditDetailSideSheet.tsx` 的入参（设计 §3.4）。 */
export interface AuditDetailSideSheetProps {
  visible: boolean;
  auditType: AuditListItem['auditType'];
  auditId: string | null;
  onClose(): void;
}

/** 详情选择态（设计 §3.5）：4 张来源表 UUID 不互通，选择必须同时携带 audit_type。 */
interface AuditDetailSelection {
  auditType: AuditListItem['auditType'];
  auditId: string;
}

/** 初始/重置查询：不含任何筛选项（重置即回到此值，设计 §3.3.1「重置」）。 */
const DEFAULT_QUERY: AuditListQuery = { page: 1, pageSize: AUDIT_PAGE_SIZE_DEFAULT };

export function AuditPage() {
  const { t } = useTranslation();
  const [query, setQuery] = useState<AuditListQuery>(DEFAULT_QUERY);
  const [items, setItems] = useState<AuditListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detail, setDetail] = useState<AuditDetailSelection | null>(null);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listAudits(query);
      if (current !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
      setFailed(false);
    } catch {
      // [E-06] 失败不改筛选：条件与已加载数据保留，由 ErrorState 就地重试。
      if (current === requestSeq.current) {
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [query]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleFilterChange = useCallback((patch: Partial<AuditListQuery>) => {
    setQuery((prev) => ({ ...prev, ...patch }));
  }, []);

  const handleReset = useCallback(() => {
    setQuery(DEFAULT_QUERY);
  }, []);

  const handleRefresh = useCallback(() => {
    void reload();
  }, [reload]);

  const handleOpenDetail = useCallback((item: AuditListItem) => {
    setDetail({ auditType: item.auditType, auditId: item.auditId });
  }, []);

  const statusOptions: Record<string, StatusTagOption> = {
    SUCCESS: { color: 'green', label: t('audit.resultStatus.SUCCESS') },
    FAILED: { color: 'red', label: t('audit.resultStatus.FAILED') }
  };

  /** 列表渲染入参（设计 §3.4）：TASK-012 的 AuditTable 落地后原样消费。 */
  const tableProps: AuditTableProps = {
    items,
    loading,
    page: query.page,
    pageSize: query.pageSize,
    total,
    onPageChange: (page, pageSize) => setQuery((prev) => ({ ...prev, page, pageSize })),
    onOpenDetail: handleOpenDetail
  };

  // 详情选择态（设计 §3.5）由本页持有；TASK-013 落地后在此渲染
  // `<AuditDetailSideSheet {...detail} />`（props 形状见上方 AuditDetailSideSheetProps）。

  return (
    <PageSection>
      <ModuleToolbar
        // 左主操作位：设计 §3.3.1 的「导出」归 TASK-014，落地后在此渲染导出按钮。
        actions={null}
        search={
          <AuditFilterBar
            value={query}
            onChange={handleFilterChange}
            onSearch={handleRefresh}
            onReset={handleReset}
            onRefresh={handleRefresh}
          />
        }
      />
      <RemoteTable<AuditListItem>
        rowKey="auditId"
        loading={tableProps.loading}
        columns={[
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
            dataIndex: 'actorUserId',
            render: (_: unknown, record: AuditListItem) => record.actorName ?? record.actorUserId
          },
          {
            title: t('audit.columns.agent'),
            dataIndex: 'agentId',
            render: (_: unknown, record: AuditListItem) => record.agentName ?? record.agentId ?? '-'
          },
          {
            title: t('audit.columns.target'),
            dataIndex: 'target',
            render: (value: string, record: AuditListItem) => (
              <EntityLink
                testId={`audit-link-${record.auditId}`}
                onClick={() => tableProps.onOpenDetail(record)}
              >
                {value}
              </EntityLink>
            )
          },
          { title: t('audit.columns.action'), dataIndex: 'action' },
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
                <EntityLink
                  testId={`audit-trace-${record.auditId}`}
                  onClick={() => tableProps.onOpenDetail(record)}
                >
                  {value}
                </EntityLink>
              ) : (
                '-'
              )
          }
        ]}
        dataSource={tableProps.items}
        page={tableProps.page}
        pageSize={tableProps.pageSize}
        total={tableProps.total}
        onPageChange={(page) => tableProps.onPageChange(page, tableProps.pageSize)}
        onPageSizeChange={(pageSize) => tableProps.onPageChange(1, pageSize)}
        empty={
          failed ? (
            <ErrorState onRetry={() => void reload()} />
          ) : (
            // 设计 §3.6：空态提供「清筛选」出口。
            <EmptyState
              title={t('common.empty')}
              description={t('common.emptyHint')}
              action={<Button onClick={handleReset}>{t('common.reset')}</Button>}
            />
          )
        }
      />
    </PageSection>
  );
}
