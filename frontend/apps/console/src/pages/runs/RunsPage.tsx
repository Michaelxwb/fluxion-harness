import { useEffect, useState } from "react";

import { Button, Card, Descriptions, Empty, Skeleton, Space, Table, Typography } from "@douyinfe/semi-ui";
import { IconRefresh } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { RunsTable } from "../../components/operations/RunsTable";
import { PageHeader } from "../../components/PageHeader";
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

export function RunsPage({ api }: RunsPageProps) {
  const [runs, setRuns] = useState<readonly RunDetail[]>([]);
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // C407（TASK-014）：Phase 3 workflow_run 投影（trace 关联）
  const [workflowRuns, setWorkflowRuns] = useState<readonly WorkflowRunProjection[] | null>(null);
  const [workflowRunsError, setWorkflowRunsError] = useState<string | null>(null);
  const [workflowRunsReloadKey, setWorkflowRunsReloadKey] = useState(0);

  async function loadRuns(): Promise<void> {
    try {
      const loaded = await api.listRuns();
      setRuns(loaded);
      setSelected(loaded[0] ?? null);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  useEffect(() => {
    let active = true;
    setWorkflowRunsError(null);
    void api
      .listWorkflowRuns()
      .then((items) => {
        if (active) setWorkflowRuns(items);
      })
      .catch((cause: unknown) => {
        if (active) {
          setWorkflowRunsError(cause instanceof Error ? cause.message : "未知错误");
        }
      });
    return () => {
      active = false;
    };
  }, [api, workflowRunsReloadKey]);

  useEffect(() => {
    void loadRuns();
  }, []);

  return (
    <div className="page-stack">
      <PageHeader
        description="追踪查询失败只影响本页，不阻断资源发布。"
        extra={
          <Button icon={<IconRefresh />} onClick={() => void loadRuns()}>
            刷新
          </Button>
        }
        title="执行记录"
      />
      <ErrorBanner message={error} />
      <RunTable onSelect={setSelected} runs={runs} />
      {selected ? <RunSnapshot run={selected} /> : null}
      <Card title="工作流运行（trace 关联）">
        {workflowRunsError !== null ? (
          <ErrorBanner
            message={`加载失败：${workflowRunsError}`}
            onRetry={() => setWorkflowRunsReloadKey((key) => key + 1)}
          />
        ) : workflowRuns === null ? (
          <div aria-label="工作流运行加载中">
            <Skeleton.Title />
          </div>
        ) : (
          <RunsTable runs={workflowRuns} />
        )}
      </Card>
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
      columns={columns}
      dataSource={[...runs]}
      empty={<Empty description="暂无运行记录" />}
      pagination={false}
      rowKey="executionId"
    />
  );
}

function RunSnapshot({ run }: { readonly run: RunDetail }) {
  return (
    <Card aria-label="执行快照" bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }} title="执行快照">
      <Descriptions row>
        <Descriptions.Item itemKey="运行态">
          {versionLabel(run.snapshot.runtimeProfile)}
        </Descriptions.Item>
      </Descriptions>
      <VersionGroup refs={run.snapshot.skills} title="技能" />
      <VersionGroup refs={run.snapshot.mcps} title="MCP 工具" />
      <VersionGroup refs={run.snapshot.plugins} title="插件" />
      <VersionGroup refs={run.snapshot.policies} title="策略" />
      <Typography.Text type="tertiary">{run.traceEvents.length} 条追踪事件</Typography.Text>
    </Card>
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
