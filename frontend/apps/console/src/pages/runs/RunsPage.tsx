import { useEffect, useMemo, useState } from "react";

import { Button, Card, Descriptions, Select, SideSheet, Space, Table, Timeline, Typography } from "@douyinfe/semi-ui";
import { IconRefresh } from "@douyinfe/semi-icons";
import { useSearchParams } from "react-router-dom";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RunsTable } from "../../components/operations/RunsTable";
import { PageHeader } from "../../components/PageHeader";
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
 * 不选中）；移除 Queue/Worker Summary 区块（§8.9 明确删除，运维信息不进产品页）；
 * Agent Run / Workflow Run 类型过滤统一呈现（不拆两页）。 */
export function RunsPage({ api }: RunsPageProps) {
  const [runs, setRuns] = useState<readonly RunDetail[] | null>(null);
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // TASK-023：异常工作台跳转带过滤参数（?statusFilter=failed → 过滤态直达）
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("statusFilter") ?? "");
  const [kindFilter, setKindFilter] = useState("");
  const [page, setPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  // C407（TASK-014）：Phase 3 workflow_run 投影（trace 关联）
  const [workflowRuns, setWorkflowRuns] = useState<readonly WorkflowRunProjection[] | null>(null);

  async function loadRuns(): Promise<void> {
    try {
      const loaded = await api.listRuns();
      setRuns(loaded);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

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

  useEffect(() => {
    void loadRuns();
  }, [reloadKey]);

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return (runs ?? []).filter((run) => {
      if (statusFilter && run.status !== statusFilter) return false;
      if (kindFilter) {
        const isWorkflowRun = workflowRuns?.some(
          (workflowRun) => workflowRun.workflowId === run.snapshot.runtimeProfile.id
        );
        if (kindFilter === "workflow" && !isWorkflowRun) return false;
        if (kindFilter === "agent" && isWorkflowRun) return false;
      }
      return !keyword || run.executionId.toLowerCase().includes(keyword);
    });
  }, [kindFilter, runs, search, statusFilter, workflowRuns]);
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

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
      <div aria-label="执行记录列表">
        <StandardListCard
          empty={runs !== null && filtered.length === 0}
          emptyDescription="暂无运行记录"
          error={error}
          footer={
            runs !== null && filtered.length > 0 ? (
              <StandardListFooter
                onPageChange={setPage}
                page={page}
                pageSize={PAGE_SIZE}
                total={filtered.length}
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
                  <span className="sr-only" id="run-kind-filter-label">
                    类型过滤
                  </span>
                  <Select
                    aria-labelledby="run-kind-filter-label"
                    onChange={(value) => {
                      setKindFilter(String(value ?? ""));
                      setPage(1);
                    }}
                    optionList={[
                      { label: "全部类型", value: "" },
                      { label: "Agent Run", value: "agent" },
                      { label: "Workflow Run", value: "workflow" }
                    ]}
                    placeholder="全部类型"
                    style={{ width: 150 }}
                    value={kindFilter}
                  />
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
                    setPage(1);
                  }}
                  placeholder="搜索执行 ID / Trace"
                  value={search}
                />
              }
            />
          }
        >
          <RunTable onSelect={setSelected} runs={paged} />
        </StandardListCard>
      </div>
      {workflowRuns !== null ? <RunsTable runs={workflowRuns} /> : null}
      <RunDetailSideSheet onClose={() => setSelected(null)} run={selected} />
    </div>
  );
}

interface RunTableProps {
  readonly runs: readonly RunDetail[];
  readonly onSelect: (run: RunDetail) => void;
}

function RunTable({ onSelect, runs }: RunTableProps) {
  const columns = [
    {
      dataIndex: "executionId",
      render: (_value: unknown, record: RunDetail) => (
        <Button onClick={() => onSelect(record)} type="tertiary">
          {record.executionId}
        </Button>
      ),
      title: "执行"
    },
    {
      dataIndex: "status",
      render: (_value: unknown, record: RunDetail) => <StatusTag status={record.status} />,
      title: "状态"
    },
    { dataIndex: "startedAt", title: "开始时间" }
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
              <Descriptions.Item itemKey="开始时间">{run.startedAt}</Descriptions.Item>
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
