import { useEffect, useRef, useState } from "react";

import { Button, Card, Descriptions, Select, SideSheet, Space, Table, Tabs, Timeline, Typography } from "@douyinfe/semi-ui";
import { IconRefresh } from "@douyinfe/semi-icons";
import { useSearchParams } from "react-router-dom";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RunsTable } from "../../components/operations/RunsTable";
import { PageHeader } from "../../components/PageHeader";
import { RelativeTime } from "../../components/RelativeTime";
import {
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import { StatusTag } from "../../components/StatusTag";
import type {
  ConsoleApi,
  RunDetail,
  VersionRef,
  WorkflowRunProjection
} from "../../types/console";

interface RunsPageProps {
  readonly api: ConsoleApi;
}

const PAGE_SIZE = 10;

/** TASK-020（§8.9）：执行记录页标准化——Run Detail 迁入只读 SideSheet（默认
 * 不选中）；移除 Queue/Worker Summary 区块（§8.9 明确删除，运维信息不进产品页）。
 * FEAT-03：分页/状态/keyword 全部服务端化（同一集合分页与 count），受控状态 +
 * 请求序号 guard 防乱序覆盖。类型分型（Agent/Workflow）需服务端 workflow 归属
 * 过滤支持，另行设计——本页不再做页内 kind 过滤（曾静默只看已加载页）。 */
export function RunsPage({ api }: RunsPageProps) {
  const [runs, setRuns] = useState<readonly RunDetail[] | null>(null);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // TASK-023：异常工作台跳转带过滤参数（?statusFilter=failed → 过滤态直达）
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("keyword") ?? "");
  const [debouncedSearch, setDebouncedSearch] = useState(() => searchParams.get("keyword") ?? "");
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("statusFilter") ?? "");
  const [page, setPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  const [agentNames, setAgentNames] = useState<ReadonlyMap<string, string>>(new Map());
  // C407（TASK-014）：Phase 3 workflow_run 投影（trace 关联）
  const [workflowRuns, setWorkflowRuns] = useState<readonly WorkflowRunProjection[] | null>(null);
  const [runTab, setRunTab] = useState("agent");
  const requestSeq = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    let active = true;
    requestSeq.current += 1;
    const requestId = requestSeq.current;
    setError(null);
    setRuns(null);
    void api
      .listRuns({
        page,
        pageSize: PAGE_SIZE,
        status: statusFilter || undefined,
        keyword: debouncedSearch.trim() || undefined
      })
      .then((result) => {
        // E-02 乱序防护：仅最新请求可写状态。
        if (!active || requestId !== requestSeq.current) return;
        setRuns(result.items);
        setTotal(result.total);
        // 智能体名页内 join（随页大小有界），失败回退 ID。
        void (async () => {
          const names = new Map<string, string>();
          await Promise.all(
            result.items.map(async (run) => {
              const ref = run.agentDefinition;
              if (!ref || names.has(ref.id)) return;
              try {
                const detail = await api.getResource("agent_definition", ref.id);
                const spec = detail.spec as Record<string, unknown>;
                names.set(
                  ref.id,
                  String(spec.display_name ?? spec.name ?? ref.id)
                );
              } catch {
                names.set(ref.id, ref.id);
              }
            })
          );
          if (!active || requestId !== requestSeq.current) return;
          setAgentNames(names);
        })();
      })
      .catch((cause: unknown) => {
        if (!active || requestId !== requestSeq.current) return;
        setError(toErrorMessage(cause));
      });
    return () => {
      active = false;
    };
  }, [api, page, statusFilter, debouncedSearch, reloadKey]);

  useEffect(() => {
    let active = true;
    void api
      .listWorkflowRuns()
      .then((items) => {
        if (active) setWorkflowRuns(items);
      })
      .catch(() => {
        // workflow 投影加载失败不阻断主列表（追踪查询失败只影响本页区块）
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  return (
    <div className="page-stack">
      <PageHeader
        description="追踪查询失败只影响本页，不阻断资源发布。"
        extra={
          <Button icon={<IconRefresh />} onClick={() => setReloadKey((key) => key + 1)}>
            刷新
          </Button>
        }
        title="执行记录"
      />
      <ErrorBanner message={error} />
      <Tabs activeKey={runTab} onChange={setRunTab} type="line">
        <Tabs.TabPane itemKey="agent" tab="智能体执行">
          <div aria-label="执行记录列表">
            <StandardListCard
              empty={runs !== null && total === 0}
              emptyDescription="暂无运行记录"
              error={error}
              footer={
                runs !== null && total > 0 ? (
                  <StandardListFooter
                    onPageChange={setPage}
                    page={page}
                    pageSize={PAGE_SIZE}
                    total={total}
                  />
                ) : undefined
              }
              loading={runs === null && !error}
              onRetry={() => setReloadKey((key) => key + 1)}
              toolbar={
                <StandardListToolbar
                  primary={<span aria-hidden />}
                  filters={
                    <>
                      <span className="sr-only" id="run-status-filter-label">
                        状态过滤
                      </span>
                      <Select
                        aria-labelledby="run-status-filter-label"
                        onChange={(value) => {
                          setStatusFilter(String(value ?? ""));
                          setPage(1);
                        }}
                        optionList={[
                          { label: "全部状态", value: "" },
                          { label: "成功", value: "succeeded" },
                          { label: "失败", value: "failed" },
                          { label: "运行中", value: "running" }
                        ]}
                        placeholder="状态"
                        style={{ width: 120 }}
                        value={statusFilter}
                      />
                    </>
                  }
                  search={
                    <StandardListSearch
                      onChange={(value) => {
                        setSearch(value);
                      }}
                      placeholder="搜索执行 ID / Trace"
                      value={search}
                    />
                  }
                />
              }
            >
              <RunTable agentNames={agentNames} onSelect={setSelected} runs={runs ?? []} />
            </StandardListCard>
          </div>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="workflow" tab="工作流运行">
          {workflowRuns !== null ? <RunsTable runs={workflowRuns} /> : null}
        </Tabs.TabPane>
      </Tabs>
      <RunDetailSideSheet onClose={() => setSelected(null)} run={selected} />
    </div>
  );
}
interface RunTableProps {
  readonly runs: readonly RunDetail[];
  readonly agentNames: ReadonlyMap<string, string>;
  readonly onSelect: (run: RunDetail) => void;
}

function RunTable({ agentNames, onSelect, runs }: RunTableProps) {
  const columns = [
    {
      dataIndex: "executionId",
      render: (_value: unknown, record: RunDetail) => (
        <Button
          onClick={() => onSelect(record)}
          title={record.executionId}
          type="tertiary"
        >
          {truncateId(record.executionId)}
        </Button>
      ),
      title: "执行"
    },
    {
      render: (_value: unknown, record: RunDetail) => (
        <Typography.Text type="tertiary">
          {record.agentDefinition
            ? (agentNames.get(record.agentDefinition.id) ?? record.agentDefinition.id)
            : "—"}
        </Typography.Text>
      ),
      title: "智能体"
    },
    {
      dataIndex: "status",
      render: (_value: unknown, record: RunDetail) => <StatusTag status={record.status} />,
      title: "状态"
    },
    {
      render: (_value: unknown, record: RunDetail) => (
        <Typography.Text
          ellipsis={{ showTooltip: true }}
          style={{ maxWidth: 240 }}
          type={record.status === "failed" ? "danger" : "tertiary"}
        >
          {failureSummary(record)}
        </Typography.Text>
      ),
      title: "失败摘要"
    },
    {
      render: (_value: unknown, record: RunDetail) => (
        <Typography.Text type="tertiary">{formatLatency(record.latencyMs)}</Typography.Text>
      ),
      title: "耗时"
    },
    {
      dataIndex: "startedAt",
      render: (value: string) => <RelativeTime value={value} />,
      title: "开始时间"
    }
  ];
  return (
    <Table
      aria-label="运行记录表格"
      columns={columns}
      dataSource={[...runs]}
      pagination={false}
      rowKey="executionId"
    />
  );
}

/** 执行 ID 截断展示（hover 全文）；耗时无 endedAt 字段，后端补齐前显示占位。 */
function truncateId(id: string): string {
  return id.length > 24 ? `${id.slice(0, 16)}…${id.slice(-6)}` : id;
}

function failureSummary(run: RunDetail): string {
  if (run.error) {
    return run.error;
  }
  if (run.status !== "failed") {
    return "—";
  }
  const clue = run.traceEvents.find((event) => /error|fail/i.test(event.event));
  return clue ? clue.event : "失败（详情见 Trace）";
}

/** 耗时格式化（latency_ms；缺失显示占位，后端补字段前）。 */
function formatLatency(latencyMs: number | null | undefined): string {
  if (latencyMs === null || latencyMs === undefined) {
    return "—";
  }
  if (latencyMs >= 1000) {
    return `${(latencyMs / 1000).toFixed(1)}s`;
  }
  return `${Math.round(latencyMs)}ms`;
}

/** 只读 Run Detail SideSheet（§8.9）：Summary/Timeline/Tool·Model Calls/Trace/
 * ExecutionSnapshot 分区；无任何可写控件（Edit 与 Detail 分离，§7.3）。 */
function RunDetailSideSheet({
  onClose,
  run
}: {
  readonly onClose: () => void;
  readonly run: RunDetail | null;
}) {
  const toolModelCalls = (run?.traceEvents ?? []).filter((event) => /tool|model/i.test(event.event));
  return (
    <SideSheet
      onCancel={onClose}
      title="Run Detail"
      visible={run !== null}
      width={860}
    >
      {run ? (
        <div className="run-detail" aria-label="Run Detail" style={{ display: "grid", gap: 16 }}>
          <Card title="Summary">
            <Descriptions row>
              <Descriptions.Item itemKey="执行 ID">{run.executionId}</Descriptions.Item>
              <Descriptions.Item itemKey="状态"><StatusTag status={run.status} /></Descriptions.Item>
              <Descriptions.Item itemKey="开始时间">
                <RelativeTime value={run.startedAt} />
              </Descriptions.Item>
              <Descriptions.Item itemKey="耗时">
                {formatLatency(run.latencyMs)}
              </Descriptions.Item>
              {run.status === "failed" ? (
                <Descriptions.Item itemKey="失败摘要">{failureSummary(run)}</Descriptions.Item>
              ) : null}
            </Descriptions>
          </Card>
          <Card title="Timeline">
            <Timeline aria-label="执行 Timeline">
              {run.traceEvents.map((event) => (
                <Timeline.Item key={event.id} time={event.at}>
                  {event.event}
                </Timeline.Item>
              ))}
            </Timeline>
          </Card>
          <Card title="Tool · Model Calls">
            {toolModelCalls.length === 0 ? (
              <Typography.Text type="tertiary">本次执行无 Tool/Model 调用</Typography.Text>
            ) : (
              <div aria-label="Tool/Model 调用">
              <Table
                columns={[
                  { title: "调用", dataIndex: "event" },
                  { title: "时间", dataIndex: "at" }
                ]}
                dataSource={toolModelCalls.map((event) => ({ key: event.id, ...event }))}
                pagination={false}
                size="small"
              />
            </div>
            )}
          </Card>
          <Card title="Trace">
            <Table
              aria-label="Trace 事件"
              columns={[
                { title: "事件", dataIndex: "event" },
                { title: "时间", dataIndex: "at" }
              ]}
              dataSource={run.traceEvents.map((event) => ({ key: event.id, ...event }))}
              pagination={false}
              size="small"
            />
          </Card>
          <Card aria-label="Execution Snapshot" title="Execution Snapshot">
            <Descriptions row>
              <Descriptions.Item itemKey="运行态">
                {versionLabel(run.snapshot.runtimeProfile)}
              </Descriptions.Item>
            </Descriptions>
            <VersionGroup refs={run.snapshot.skills} title="技能" />
            <VersionGroup refs={run.snapshot.mcps} title="MCP 工具" />
            <VersionGroup refs={run.snapshot.plugins} title="插件" />
            <VersionGroup refs={run.snapshot.policies} title="策略" />
          </Card>
        </div>
      ) : null}
    </SideSheet>
  );
}

function VersionGroup({ refs, title }: { readonly refs: readonly VersionRef[]; readonly title: string }) {
  return (
    <Space align="start" className="version-group">
      <Typography.Text strong>{title}</Typography.Text>
      <Space wrap>
        {refs.map((ref) => (
          <Typography.Text code key={`${ref.id}:${ref.version}`}>
            {versionLabel(ref)}
          </Typography.Text>
        ))}
      </Space>
    </Space>
  );
}

function versionLabel(ref: VersionRef): string {
  return `${ref.id} @ ${ref.version}`;
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
