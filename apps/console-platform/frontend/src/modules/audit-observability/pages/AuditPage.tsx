/**
 * 运行审计列表页容器（设计 §3.2 `/audits`、§3.3 CMP-01、§3.5 状态划分）。
 *
 * 本页持有筛选/分页/详情选择状态，渲染 `ModuleToolbar`（左上主操作位 + 右上筛选栏）与列表：
 * 列表数据状态机归 TASK-012 的 `hooks/useAuditList`，列表体（列/行渲染/空错槽位/分页联动）归
 * TASK-012 的 `components/AuditTable`，两者都只经 TASK-010 的 service 层取数（不裸用 HTTP 客户端）。
 * 页面继续渲染公共 `RemoteTable`（内置 `PaginationFooter`，仓库级 verifier 冻结其存在），入参由
 * `buildAuditTableProps` 原样供给；详情选择态驱动 TASK-013 的只读 `AuditDetailSideSheet`、左上主操作位的
 * 导出按钮归 TASK-014。
 */

import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageSection } from '../../../components/common/ConsolePage';
import { ModuleToolbar } from '../../../components/common/ModuleToolbar';
import { RemoteTable } from '../../../components/common/RemoteTable';
import { AuditDetailSideSheet } from '../components/AuditDetailSideSheet';
import { buildAuditTableProps } from '../components/AuditTable';
import { AuditFilterBar } from '../components/AuditFilterBar';
import { useAuditList } from '../hooks/useAuditList';
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
  const [detail, setDetail] = useState<AuditDetailSelection | null>(null);
  const { items, loading, page, pageSize, total, failed, reload } = useAuditList(query);

  const handleFilterChange = useCallback((patch: Partial<AuditListQuery>) => {
    setQuery((prev) => ({ ...prev, ...patch }));
  }, []);

  const handleReset = useCallback(() => {
    setQuery(DEFAULT_QUERY);
  }, []);

  /** 刷新与失败重试同一出口（[E-06] 保留筛选条件，按当前页重取）。 */
  const handleRefresh = useCallback(() => {
    void reload();
  }, [reload]);

  const handlePageChange = useCallback((page: number, pageSize: number) => {
    setQuery((prev) => ({ ...prev, page, pageSize }));
  }, []);

  const handleOpenDetail = useCallback((item: AuditListItem) => {
    setDetail({ auditType: item.auditType, auditId: item.auditId });
  }, []);

  /** 关闭详情：清空选择态（设计 §3.5，SideSheet 随之卸载）。 */
  const handleCloseDetail = useCallback(() => {
    setDetail(null);
  }, []);

  /** 列表渲染入参（设计 §3.4）：TASK-012 的 AuditTable 落地后原样消费。 */
  const tableProps: AuditTableProps = {
    items,
    loading,
    page,
    pageSize,
    total,
    onPageChange: handlePageChange,
    onOpenDetail: handleOpenDetail
  };

  /** 表格入参（设计 §3.4/§3.6）：列/行渲染/空错槽位/分页全部由 AuditTable 供给。 */
  const auditTable = buildAuditTableProps({
    ...tableProps,
    failed,
    onRetry: handleRefresh,
    onReset: handleReset,
    t
  });

  // 详情选择态（设计 §3.5）由本页持有：TASK-013 的只读 SideSheet 按选择态渲染，关闭即清空。
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
      <RemoteTable<AuditListItem> {...auditTable} />
      {detail ? (
        <AuditDetailSideSheet
          visible
          auditType={detail.auditType}
          auditId={detail.auditId}
          onClose={handleCloseDetail}
        />
      ) : null}
    </PageSection>
  );
}
