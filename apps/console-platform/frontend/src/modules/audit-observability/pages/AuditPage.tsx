/**
 * 运行审计列表页容器（设计 §3.2 `/audits`、§3.3 CMP-01、§3.5 状态划分）。
 *
 * 本页持有筛选/分页/详情选择状态，渲染 `ModuleToolbar`（左上主操作位 + 右上筛选栏）与列表：
 * 列表数据状态机归 TASK-012 的 `hooks/useAuditList`，列表体（列/行渲染/空错槽位/分页联动）归
 * TASK-012 的 `components/AuditTable`，两者都只经 TASK-010 的 service 层取数（不裸用 HTTP 客户端）。
 * 页面继续渲染公共 `RemoteTable`（内置 `PaginationFooter`，仓库级 verifier 冻结其存在），入参由
 * `buildAuditTableProps` 原样供给；详情选择态驱动 TASK-013 的只读 `AuditDetailSideSheet`，左上主操作位渲染
 * TASK-014 的导出按钮（按当前筛选建任务，状态机归 `hooks/useAuditExport`）。
 *
 * 状态划分（自上而下）：`useAuditQueryState`（筛选/分页状态与出口）、`useAuditDetailSelection`（详情
 * 选择态）、`AuditPageToolbar`/`AuditRefreshNotice`/`AuditDetailPanel`（三处局部 JSX）→ `AuditPage`
 * （只做装配与取数编排）。关联对象「Run …」由 `useRelatedDetail` + `RelatedDetailController` 就地
 * 承载运行详情（Run 无独立页面）；「Task …」落 `/tasks?taskId=` 深链。失败呈现分流（设计 §3.6）：首载失败（无行）走列表整页 `ErrorState`，刷新失败
 * （已有行）保留行并在列表上方给非破坏性提示与重试——本页按「是否有行」推导两者，hook 只置 `failed`。
 */

import { Banner, Button } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { PageHeader, PageSection } from '../../../components/common/ConsolePage';
import { ModuleToolbar } from '../../../components/common/ModuleToolbar';
import { RemoteTable } from '../../../components/common/RemoteTable';
import { AuditDetailSideSheet } from '../components/AuditDetailSideSheet';
import { AuditTraceSideSheet } from '../components/AuditTraceSideSheet';
import { AuditViewNotice } from '../components/AuditViewNotice';
import { AuditExportButton } from '../components/AuditExportButton';
import { buildAuditTableProps } from '../components/AuditTable';
import { AuditFilterBar } from '../components/AuditFilterBar';
import { useAuditList } from '../hooks/useAuditList';
import {
  RelatedDetailController,
  useRelatedDetail
} from '../../run-observability/RelatedDetailController';
import { auditViewOf } from '../auditLabels';
import {
  AUDIT_PAGE_SIZE_DEFAULT,
  type AuditDetailType,
  type AuditExportCreateRequest,
  type AuditListItem,
  type AuditListQuery
} from '../types';

/** TASK-012 `components/AuditTable.tsx` 的入参（设计 §3.4）。 */
export interface AuditTableProps {
  items: AuditListItem[];
  loading: boolean;
  page: number;
  pageSize: number;
  total: number;
  onPageChange(page: number, pageSize: number): void;
  onOpenDetail(item: AuditListItem): void;
  onOpenRun(runId: string): void;
  onOpenTask(taskId: string): void;
  onOpenTrace(traceId: string): void;
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
  auditType: AuditDetailType;
  auditId: string;
}

/** 初始/重置查询：不含任何筛选项（重置即回到此值，设计 §3.3.1「重置」）。 */
const DEFAULT_QUERY: AuditListQuery = {
  page: 1,
  pageSize: AUDIT_PAGE_SIZE_DEFAULT,
  view: 'operation'
};

const AUDIT_TYPES = ['RUN', 'CONFIG', 'TOOL', 'EGRESS', 'MODEL'] as const;
const UUID_VALUE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Overview links enter the same visible filters used by the audit list and export. */
function auditQueryFromLink(searchParams: URLSearchParams): AuditListQuery {
  const agentId = searchParams.get('agentId');
  const auditType = AUDIT_TYPES.find((value) => value === searchParams.get('auditType'));
  return {
    ...DEFAULT_QUERY,
    auditType,
    view: auditViewOf(auditType),
    agentId: agentId && UUID_VALUE.test(agentId) ? agentId : undefined
  };
}

/** 筛选/分页状态（设计 §3.3.1/§3.4）：筛选合并补丁（页码由筛选栏重置为 1）、翻页落回本页状态。 */
function useAuditQueryState() {
  const [searchParams] = useSearchParams();
  const linkQuery = searchParams.toString();
  const lastLink = useRef(linkQuery);
  const [query, setQuery] = useState<AuditListQuery>(() => auditQueryFromLink(searchParams));

  useEffect(() => {
    if (lastLink.current === linkQuery) return;
    lastLink.current = linkQuery;
    setQuery(auditQueryFromLink(new URLSearchParams(linkQuery)));
  }, [linkQuery]);

  /** 视图由类型派生后写回 query：筛选与视图不可能出现不一致。 */
  const handleFilterChange = useCallback((patch: Partial<AuditListQuery>) => {
    setQuery((prev) => {
      const merged = { ...prev, ...patch };
      return { ...merged, view: auditViewOf(merged.auditType) };
    });
  }, []);

  /** 重置：清空全部筛选并回到第 1 页（设计 §3.3.1「重置」）。 */
  const handleReset = useCallback(() => {
    setQuery(DEFAULT_QUERY);
  }, []);

  const handlePageChange = useCallback((page: number, pageSize: number) => {
    setQuery((prev) => ({ ...prev, page, pageSize }));
  }, []);

  return { query, handleFilterChange, handleReset, handlePageChange };
}

/** 详情选择态（设计 §3.5）：打开落选择态、关闭清空（SideSheet 随之卸载）。 */
function useAuditDetailSelection() {
  const [detail, setDetail] = useState<AuditDetailSelection | null>(null);

  const handleOpenDetail = useCallback((item: AuditListItem) => {
    // RUN 操作行没有审计详情：它由运行详情承载（列表里的两个入口都直达那里）
    if (item.auditType === 'RUN') {
      return;
    }
    setDetail({ auditType: item.auditType, auditId: item.auditId });
  }, []);

  const handleCloseDetail = useCallback(() => {
    setDetail(null);
  }, []);

  return { detail, handleOpenDetail, handleCloseDetail };
}

/** 工具栏入参（设计 §3.3.1）：筛选/分页状态 + 三个出口；导出筛选与列表筛选同源、不含分页。 */
interface AuditPageToolbarProps {
  query: AuditListQuery;
  exportFilters: AuditExportCreateRequest['filters'];
  onChange(patch: Partial<AuditListQuery>): void;
  onReset(): void;
  onRefresh(): void;
}

/** 工具栏：左主操作位是 TASK-014 的导出按钮（按当前筛选建任务、提交中禁用），右上挂筛选栏。 */
function AuditPageToolbar(props: AuditPageToolbarProps) {
  return (
    <ModuleToolbar
      actions={<AuditExportButton filters={props.exportFilters} />}
      search={
        <AuditFilterBar
          value={props.query}
          onChange={props.onChange}
          onReset={props.onReset}
          onRefresh={props.onRefresh}
        />
      }
    />
  );
}

/** 详情面板入参：选择态（null 即不渲染）与关闭出口。 */
interface AuditDetailPanelProps {
  detail: AuditDetailSelection | null;
  handleCloseDetail(): void;
}

/** 详情面板（设计 §3.5）：TASK-013 的只读 SideSheet，按选择态渲染、关闭即清空。 */
function AuditDetailPanel({ detail, handleCloseDetail }: AuditDetailPanelProps) {
  if (detail === null) {
    return null;
  }
  return (
    <AuditDetailSideSheet
      visible
      auditType={detail.auditType}
      auditId={detail.auditId}
      onClose={handleCloseDetail}
    />
  );
}

/** 刷新失败提示入参：显式重试入口复用页面的刷新出口（[E-06] 就地重试，不自动重提）。 */
interface AuditRefreshNoticeProps {
  onRetry(): void;
}

/**
 * 刷新失败提示（[E-06] 非破坏性）：已加载行照常展示，只在列表上方就地给文案与重试入口——与
 * `AuditExportButton` 的失败形态一致，不用整页 `ErrorState` 顶掉已有数据。
 */
function AuditRefreshNotice(props: AuditRefreshNoticeProps) {
  const { t } = useTranslation();
  return (
    <div
      data-testid="audit-refresh-error"
      style={{ display: 'flex', alignItems: 'center', gap: 8, maxWidth: 420 }}
    >
      <Banner type="danger" closeIcon={null} description={t('audit.list.refreshFailed')} />
      <Button
        theme="borderless"
        type="primary"
        data-testid="audit-refresh-retry"
        onClick={props.onRetry}
      >
        {t('common.retry')}
      </Button>
    </div>
  );
}

export function AuditPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { query, handleFilterChange, handleReset, handlePageChange } = useAuditQueryState();
  /** Trace 详情选择态：与筛选无关，列表保持原样。 */
  const [traceId, setTraceId] = useState<string | null>(null);
  const { detail, handleOpenDetail, handleCloseDetail } = useAuditDetailSelection();
  const { items, loading, page, pageSize, total, failed, reload } = useAuditList(query);
  /** 关联对象直达：Run 就地叠加运行详情（无独立页面），Task 落任务列表深链（既有口径）。 */
  const related = useRelatedDetail();

  const handleOpenTask = useCallback(
    (taskId: string) => {
      navigate(`/tasks?taskId=${taskId}`);
    },
    [navigate]
  );

  /** Trace 详情：只读侧栏展示该 Trace 的全部记录，列表保持原样（不切视图、不清筛选）。 */
  const handleOpenTrace = useCallback((trace: string) => {
    setTraceId(trace);
  }, []);

  /** 刷新与失败重试同一出口（[E-06] 保留筛选条件，按当前页重取）。 */
  const handleRefresh = useCallback(() => {
    void reload();
  }, [reload]);

  /** 刷新失败：已有行可保留 ⇒ 行照常展示，只在列表上方给非破坏性提示（设计 §3.6）。 */
  const refreshFailed = failed && items.length > 0;
  /** 首载失败：无行可保留 ⇒ 由列表整页 `ErrorState` 承载（保留既有行为与筛选条件）。 */
  const firstLoadFailed = failed && !refreshFailed;

  /** 列表渲染入参（设计 §3.4）：TASK-012 的 AuditTable 落地后原样消费。 */
  const tableProps: AuditTableProps = {
    items,
    loading,
    page,
    pageSize,
    total,
    onPageChange: handlePageChange,
    onOpenDetail: handleOpenDetail,
    onOpenRun: related.openRun,
    onOpenTask: handleOpenTask,
    onOpenTrace: handleOpenTrace
  };

  /**
   * 导出筛选与列表筛选同源（设计 §3.5）：去掉分页与视图字段后交给导出按钮——
   * 导出永远是全量明细，`view` 不在导出请求体里（后端 DTO 禁止额外字段）。
   */
  const { page: _page, pageSize: _pageSize, view: _view, ...exportFilters } = query;

  /** 表格入参（设计 §3.4/§3.6）：列/行渲染/空错槽位/分页全部由 AuditTable 供给。 */
  const auditTable = buildAuditTableProps({
    ...tableProps,
    failed: firstLoadFailed,
    onRetry: handleRefresh,
    onReset: handleReset,
    t
  });

  return (
    <>
      <PageHeader title={t('audit.title')} description={t('audit.subtitle')} />
      <PageSection>
        <AuditPageToolbar
          query={query}
          exportFilters={exportFilters}
          onChange={handleFilterChange}
          onReset={handleReset}
          onRefresh={handleRefresh}
        />
        {refreshFailed ? <AuditRefreshNotice onRetry={handleRefresh} /> : null}
        {query.view === 'detail' ? (
          <AuditViewNotice onBack={() => handleFilterChange({ auditType: undefined, page: 1 })} />
        ) : null}
        <RemoteTable<AuditListItem> {...auditTable} />
        <AuditDetailPanel detail={detail} handleCloseDetail={handleCloseDetail} />
        <AuditTraceSideSheet traceId={traceId} onClose={() => setTraceId(null)} />
        {/* 关联对象「Run …」的直达详情（与审计详情内的嵌套层同源，控制器互斥承载一层）。 */}
        <RelatedDetailController
          state={related.state}
          onClose={related.close}
          onOpenTask={related.openTask}
          onOpenRun={related.openRun}
        />
      </PageSection>
    </>
  );
}
