import { useEffect, useMemo, useRef, useState } from "react";

import { DatePicker, Descriptions, Select, SideSheet, Table, Typography } from "@douyinfe/semi-ui";
import { useSearchParams } from "react-router-dom";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { ResourceId } from "../../components/ResourceId";
import { PageHeader } from "../../components/PageHeader";
import {
  DEFAULT_PAGE_SIZE,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import type { AuditFilters, AuditRecord, ConsoleApi, PageData } from "../../types/console";

interface AuditPageProps {
  readonly api: ConsoleApi;
}

/** TASK-021（§8.10）：审计页标准化——操作类型/操作者/对象类型组合过滤（后端
 * 查询参数下推，时间范围不做前端全量过滤）+ 搜索 + 右下单套分页 + 详情只读
 * SideSheet（request_id/trace_id 关联呈现，规则 23；before/after 快照 diff）。 */
export function AuditPage({ api }: AuditPageProps) {
  const [page, setPage] = useState<PageData<AuditRecord> | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState("");
  const [actorFilter, setActorFilter] = useState("");
  const [targetTypeFilter, setTargetTypeFilter] = useState("");
  const [timeRange, setTimeRange] = useState<readonly [Date, Date] | null>(null);
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("keyword") ?? "");
  const [selected, setSelected] = useState<AuditRecord | null>(null);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  // 页量 ref：Semi 切换页量时会连带触发 onChange，两次回调同 tick 执行，
  // ref 保证第二次（翻页）请求读到最新页量，避免新旧页量请求竞态。
  const pageSizeRef = useRef(DEFAULT_PAGE_SIZE);

  async function loadAudit(nextPage: number, filters?: AuditFilters, size?: number): Promise<void> {
    const pageSizeForRequest = size ?? pageSizeRef.current;
    try {
      setCurrentPage(nextPage);
      setPage(
        await api.listAudit(
          { page: nextPage, pageSize: pageSizeForRequest },
          filters ?? buildFilters(actionFilter, actorFilter, targetTypeFilter, timeRange)
        )
      );
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "未知错误");
    }
  }

  function applyTimeRange(next: readonly [Date, Date] | null): void {
    setTimeRange(next);
    void loadAudit(1, buildFilters(actionFilter, actorFilter, targetTypeFilter, next));
  }

  useEffect(() => {
    void loadAudit(1);
  }, []);

  const rows = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return (page?.items ?? []).filter(
      (record) =>
        !keyword ||
        record.action.toLowerCase().includes(keyword) ||
        record.actorId.toLowerCase().includes(keyword) ||
        record.resourceId.toLowerCase().includes(keyword)
    );
  }, [page, search]);

  const actionOptions = useMemo(
    () => distinctOptions(page?.items ?? [], (record) => record.action),
    [page]
  );
  const actorOptions = useMemo(
    () => distinctOptions(page?.items ?? [], (record) => record.actorId),
    [page]
  );
  const targetTypeOptions = useMemo(
    () => distinctOptions(page?.items ?? [], (record) => record.targetType ?? ""),
    [page]
  );

  return (
    <div className="page-stack">
      <PageHeader description="审计是独立事实源，不以普通日志替代。" title="操作审计" />
      <ErrorBanner message={error} />
      <div aria-label="审计列表">
        <StandardListCard
          empty={page !== null && rows.length === 0}
          emptyDescription="暂无审计记录"
          error={error}
          footer={
            page !== null && page.total > 0 ? (
              <StandardListFooter
                onPageChange={(next) => void loadAudit(next)}
                onPageSizeChange={(next) => {
                  pageSizeRef.current = next;
                  setPageSize(next);
                  void loadAudit(1, undefined, next);
                }}
                page={currentPage}
                pageSize={pageSize}
                total={page.total}
              />
            ) : undefined
          }
          loading={page === null && !error}
          onRetry={() => void loadAudit(1)}
          toolbar={
            <StandardListToolbar
              primary={null}
              filters={
                <>
                  <span className="sr-only" id="audit-time-range-label">
                    时间范围过滤
                  </span>
                  <DatePicker
                    aria-labelledby="audit-time-range-label"
                    data-testid="audit-time-range"
                    format="yyyy-MM-dd HH:mm:ss"
                    onChange={(dates: Date | Date[] | string | string[] | undefined) => {
                      applyTimeRange(
                        Array.isArray(dates) &&
                          dates.length === 2 &&
                          dates[0] instanceof Date &&
                          dates[1] instanceof Date
                          ? [dates[0], dates[1]]
                          : null
                      );
                    }}
                    placeholder={["开始时间", "结束时间"]}
                    presets={TIME_PRESETS}
                    showClear
                    style={{ width: 320 }}
                    type="dateTimeRange"
                    value={timeRange === null ? [] : [...timeRange]}
                  />
                  <span className="sr-only" id="audit-action-filter-label">
                    操作类型过滤
                  </span>
                  <Select
                    aria-labelledby="audit-action-filter-label"
                    filter
                    onChange={(value) => {
                      const next = String(value ?? "");
                      setActionFilter(next);
                      void loadAudit(1, buildFilters(next, actorFilter, targetTypeFilter, timeRange));
                    }}
                    optionList={[{ label: "全部操作", value: "" }, ...actionOptions]}
                    placeholder="操作类型"
                    style={{ width: 150 }}
                    value={actionFilter}
                  />
                  <span className="sr-only" id="audit-actor-filter-label">
                    操作者过滤
                  </span>
                  <Select
                    aria-labelledby="audit-actor-filter-label"
                    filter
                    onChange={(value) => {
                      const next = String(value ?? "");
                      setActorFilter(next);
                      void loadAudit(1, buildFilters(actionFilter, next, targetTypeFilter, timeRange));
                    }}
                    optionList={[{ label: "全部操作者", value: "" }, ...actorOptions]}
                    placeholder="操作者"
                    style={{ width: 140 }}
                    value={actorFilter}
                  />
                  <span className="sr-only" id="audit-target-filter-label">
                    对象类型过滤
                  </span>
                  <Select
                    aria-labelledby="audit-target-filter-label"
                    filter
                    onChange={(value) => {
                      const next = String(value ?? "");
                      setTargetTypeFilter(next);
                      void loadAudit(1, buildFilters(actionFilter, actorFilter, next, timeRange));
                    }}
                    optionList={[{ label: "全部对象", value: "" }, ...targetTypeOptions]}
                    placeholder="对象类型"
                    style={{ width: 150 }}
                    value={targetTypeFilter}
                  />
                </>
              }
              search={
                <StandardListSearch
                  onChange={setSearch}
                  placeholder="搜索审计"
                  value={search}
                />
              }
            />
          }
        >
          <Table<AuditRecord>
            aria-label="审计表格"
            columns={[
              {
                dataIndex: "action",
                render: (value: string) => <Typography.Text link>{value}</Typography.Text>,
                title: "操作"
              },
              { dataIndex: "actorId", title: "操作者" },
              {
                dataIndex: "resourceId",
                render: (value: string) => <ResourceId id={value} />,
                title: "资源"
              },
              { dataIndex: "resourceVersion", title: "版本" },
              {
                dataIndex: "at",
                render: (value: string) => <RelativeTime value={value} />,
                title: "时间"
              }
            ]}
            dataSource={[...rows]}
            onRow={(record) => ({
              onClick: () => setSelected(record ?? null),
              style: { cursor: "pointer" }
            })}
            pagination={false}
            rowKey="id"
          />
        </StandardListCard>
      </div>
      <Typography.Text type="tertiary">审计日志保留 30 天热查询</Typography.Text>
      <SideSheet
        onCancel={() => setSelected(null)}
        title="审计详情"
        visible={selected !== null}
        width={800}
      >
        {selected ? (
          <div aria-label="审计详情" style={{ display: "grid", gap: 16 }}>
            <Descriptions row>
              <Descriptions.Item itemKey="操作">{selected.action}</Descriptions.Item>
              <Descriptions.Item itemKey="操作者">{selected.actorId}</Descriptions.Item>
              <Descriptions.Item itemKey="对象类型">{selected.targetType ?? "-"}</Descriptions.Item>
              <Descriptions.Item itemKey="资源">{selected.resourceId}</Descriptions.Item>
              <Descriptions.Item itemKey="版本">{selected.resourceVersion}</Descriptions.Item>
              <Descriptions.Item itemKey="时间">
                <RelativeTime value={selected.at} />
              </Descriptions.Item>
            </Descriptions>
            <div>
              <Typography.Text strong>链路关联（规则 23）</Typography.Text>
              <div style={{ display: "grid", gap: 4, paddingTop: 8 }}>
                <Typography.Text copyable>{`request_id: ${selected.requestId ?? "-"}`}</Typography.Text>
                <Typography.Text copyable>{`trace_id: ${selected.traceId ?? "-"}`}</Typography.Text>
              </div>
            </div>
            <div>
              <Typography.Text strong>before / after 快照</Typography.Text>
              <pre aria-label="审计快照" style={{ background: "var(--semi-color-fill-0)", fontSize: 12, marginTop: 8, overflowX: "auto", padding: 12 }}>
{`before: ${JSON.stringify(selected.before ?? null, null, 2)}\n\nafter: ${JSON.stringify(selected.after ?? null, null, 2)}`}
              </pre>
            </div>
          </div>
        ) : null}
      </SideSheet>
    </div>
  );
}

/** 审计时间范围：开始-结束双端筛选（Semi DatePicker dateTimeRange），
 * 快捷预设保留近 1 小时/近 24 小时/近 7 天，清空即全部时间。 */
type AuditTimeRange = readonly [Date, Date] | null;

const TIME_PRESETS: { readonly text: string; readonly start: () => Date; readonly end: () => Date }[] = [
  { text: "近 1 小时", start: () => hoursAgo(1), end: () => new Date() },
  { text: "近 24 小时", start: () => hoursAgo(24), end: () => new Date() },
  { text: "近 7 天", start: () => hoursAgo(24 * 7), end: () => new Date() }
];

function hoursAgo(hours: number): Date {
  return new Date(Date.now() - hours * 3600 * 1000);
}

function buildFilters(action: string, actorId: string, targetType: string, timeRange: AuditTimeRange = null): AuditFilters {
  return {
    action: action || undefined,
    actorId: actorId || undefined,
    targetType: targetType || undefined,
    createdFrom: timeRange === null ? undefined : timeRange[0].toISOString(),
    createdTo: timeRange === null ? undefined : timeRange[1].toISOString()
  };
}

function distinctOptions(
  records: readonly AuditRecord[],
  key: (record: AuditRecord) => string
): readonly { label: string; value: string }[] {
  return [...new Set(records.map(key).filter((item) => item))]
    .sort()
    .map((item) => ({ label: item, value: item }));
}
