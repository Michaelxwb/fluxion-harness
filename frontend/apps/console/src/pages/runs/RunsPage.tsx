import { useEffect, useState } from "react";

import { Button, Descriptions, Space, Table, Typography } from "@douyinfe/semi-ui";
import { IconRefresh } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, RunDetail, VersionRef } from "../../types/console";

interface RunsPageProps {
  readonly api: ConsoleApi;
}

export function RunsPage({ api }: RunsPageProps) {
  const [runs, setRuns] = useState<readonly RunDetail[]>([]);
  const [selected, setSelected] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    void loadRuns();
  }, []);

  return (
    <div className="page-stack">
      <PageHeader
        description="Trace 查询失败只影响本页，不阻断 Resource 发布。"
        extra={
          <Button icon={<IconRefresh />} onClick={() => void loadRuns()}>
            刷新
          </Button>
        }
        title="Runs / Traces"
      />
      <ErrorBanner message={error} />
      <RunTable onSelect={setSelected} runs={runs} />
      {selected ? <RunSnapshot run={selected} /> : null}
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
      title: "Execution"
    },
    {
      dataIndex: "status",
      render: (_value: unknown, record: RunDetail) => <StatusTag status={record.status} />,
      title: "Status"
    },
    { dataIndex: "startedAt", title: "Started" }
  ];
  return <Table columns={columns} dataSource={[...runs]} pagination={false} rowKey="executionId" />;
}

function RunSnapshot({ run }: { readonly run: RunDetail }) {
  return (
    <section aria-label="ExecutionSnapshot" className="panel">
      <Typography.Title heading={4}>ExecutionSnapshot</Typography.Title>
      <Descriptions row>
        <Descriptions.Item itemKey="RuntimeProfile">
          {versionLabel(run.snapshot.runtimeProfile)}
        </Descriptions.Item>
      </Descriptions>
      <VersionGroup refs={run.snapshot.skills} title="Skills" />
      <VersionGroup refs={run.snapshot.mcps} title="MCP" />
      <VersionGroup refs={run.snapshot.plugins} title="Plugins" />
      <VersionGroup refs={run.snapshot.policies} title="Policies" />
      <Typography.Text type="tertiary">{run.traceEvents.length} trace event(s)</Typography.Text>
    </section>
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
