/**
 * Phase 5 TASK-006：`/build/eval` 实页（Phase 4 占位升级）。
 *
 * EvalSet 列表 / EvalRun 列表 / 详情 / 触发评测（POST /admin/evals/{id}/run）；
 * gate 阻断决策（score 回退、基线不可用）以标准错误响应呈现（ErrorBanner）。
 * 数据经 services（in-memory/http 同契约），组件零裸 fetch；
 * 容器/展示分离：展示组件 props 只读 + 事件上抛，四态完备。
 */
import { useCallback, useEffect, useState } from "react";

import { Button, Card, Descriptions, Empty, Input, Skeleton, Table, Tag, Typography } from "@douyinfe/semi-ui";
import { IconPlay } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import type { ConsoleApi, EvalRunSummary, EvalSetSummary } from "../../types/console";

interface EvalPageProps {
  readonly api: ConsoleApi;
}

export function EvalPage({ api }: EvalPageProps) {
  const [evalSets, setEvalSets] = useState<readonly EvalSetSummary[] | null>(null);
  const [evalSetsError, setEvalSetsError] = useState<string | null>(null);
  const [runs, setRuns] = useState<readonly EvalRunSummary[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [selectedSet, setSelectedSet] = useState<EvalSetSummary | null>(null);
  const [selectedRun, setSelectedRun] = useState<EvalRunSummary | null>(null);
  const [traceId, setTraceId] = useState("");
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);

  const loadSets = useCallback(async (): Promise<void> => {
    try {
      setEvalSets(await api.listEvalSets());
      setEvalSetsError(null);
    } catch (cause) {
      setEvalSetsError(cause instanceof Error ? cause.message : "未知错误");
    }
  }, [api]);

  const loadRuns = useCallback(async (): Promise<void> => {
    try {
      setRuns(await api.listEvalRuns());
      setRunsError(null);
    } catch (cause) {
      setRunsError(cause instanceof Error ? cause.message : "未知错误");
    }
  }, [api]);

  useEffect(() => {
    void loadSets();
    void loadRuns();
  }, [loadSets, loadRuns]);

  async function trigger(): Promise<void> {
    if (selectedSet === null || traceId.trim() === "") return;
    setTriggering(true);
    setTriggerError(null);
    try {
      await api.triggerEvalRun({
        evalSetId: selectedSet.id,
        evalSetVersion: selectedSet.version,
        traceId: traceId.trim()
      });
      setTraceId("");
      await loadRuns();
    } catch (cause) {
      // gate 阻断/基线不可用等 envelope 失败 message 原样呈现（E-04 联动）
      setTriggerError(cause instanceof Error ? cause.message : "未知错误");
    } finally {
      setTriggering(false);
    }
  }

  return (
    <div className="page-stack">
      <PageHeader
        description="评测集版本化生命周期；Release Gate 阻断决策以标准响应呈现。"
        extra={
          <Button
            icon={<IconPlay />}
            disabled={selectedSet === null || traceId.trim() === "" || triggering}
            loading={triggering}
            onClick={() => void trigger()}
          >
            触发评测
          </Button>
        }
        title="评测"
      />

      <Card title="评测集">
        {evalSetsError !== null ? (
          <ErrorBanner message={`评测集加载失败：${evalSetsError}`} onRetry={() => void loadSets()} />
        ) : evalSets === null ? (
          <div aria-label="评测集加载中">
            <Skeleton.Title />
          </div>
        ) : (
          <EvalSetsTable evalSets={evalSets} onSelect={setSelectedSet} selectedId={selectedSet?.id ?? null} />
        )}
      </Card>

      <Card title="触发评测">
        <Typography.Text strong>Trace ID</Typography.Text>
        <Input
          aria-label="Trace ID"
          placeholder="输入被测 execution 的 trace_id"
          value={traceId}
          onChange={(value) => setTraceId(value)}
        />
        <Typography.Text type="tertiary">
          {selectedSet
            ? `目标：${selectedSet.name} @ ${selectedSet.version}（${selectedSet.caseCount} 条用例）`
            : "先在上方选择评测集"}
        </Typography.Text>
        <ErrorBanner message={triggerError} />
      </Card>

      <Card title="评测运行">
        {runsError !== null ? (
          <ErrorBanner message={`评测运行加载失败：${runsError}`} onRetry={() => void loadRuns()} />
        ) : runs === null ? (
          <div aria-label="评测运行加载中">
            <Skeleton.Title />
          </div>
        ) : (
          <EvalRunsTable runs={runs} onSelect={setSelectedRun} />
        )}
      </Card>

      {selectedRun !== null ? <EvalRunDetail run={selectedRun} /> : null}
    </div>
  );
}

// ---- 展示组件（props 只读 + 事件上抛）----

interface EvalSetsTableProps {
  readonly evalSets: readonly EvalSetSummary[];
  readonly selectedId: string | null;
  readonly onSelect: (item: EvalSetSummary) => void;
}

function EvalSetsTable({ evalSets, selectedId, onSelect }: EvalSetsTableProps) {
  const columns = [
    {
      dataIndex: "id",
      render: (_value: unknown, record: EvalSetSummary) => (
        <Button onClick={() => onSelect(record)} type={record.id === selectedId ? "primary" : "tertiary"}>
          {record.id}
        </Button>
      ),
      title: "评测集"
    },
    { dataIndex: "name", title: "名称" },
    { dataIndex: "version", title: "版本" },
    {
      dataIndex: "status",
      render: (value: string) => <Tag color={value === "published" ? "green" : "grey"}>{value}</Tag>,
      title: "状态"
    },
    { dataIndex: "caseCount", title: "用例数" }
  ];
  return (
    <Table
      columns={columns}
      dataSource={[...evalSets]}
      empty={<Empty description="评测集暂无数据" />}
      pagination={false}
      rowKey="id"
    />
  );
}

interface EvalRunsTableProps {
  readonly runs: readonly EvalRunSummary[];
  readonly onSelect: (run: EvalRunSummary) => void;
}

function EvalRunsTable({ runs, onSelect }: EvalRunsTableProps) {
  const columns = [
    {
      dataIndex: "runId",
      render: (_value: unknown, record: EvalRunSummary) => (
        <Button onClick={() => onSelect(record)} type="tertiary">
          {record.runId}
        </Button>
      ),
      title: "运行"
    },
    {
      dataIndex: "evalSetId",
      render: (_value: unknown, record: EvalRunSummary) => `${record.evalSetId}@${record.evalSetVersion}`,
      title: "评测集"
    },
    {
      dataIndex: "passed",
      render: (value: boolean) => <Tag color={value ? "green" : "red"}>{value ? "passed" : "failed"}</Tag>,
      title: "结果"
    },
    { dataIndex: "score", title: "Score" },
    { dataIndex: "createdAt", title: "时间" }
  ];
  return (
    <Table
      columns={columns}
      dataSource={[...runs]}
      empty={<Empty description="评测运行暂无数据" />}
      pagination={false}
      rowKey="runId"
    />
  );
}

function EvalRunDetail({ run }: { readonly run: EvalRunSummary }) {
  return (
    <Card aria-label="EvalRun 详情" title="EvalRun 详情">
      <Descriptions row>
        <Descriptions.Item itemKey="运行">{run.runId}</Descriptions.Item>
        <Descriptions.Item itemKey="评测集">{`${run.evalSetId}@${run.evalSetVersion}`}</Descriptions.Item>
        <Descriptions.Item itemKey="Score">{run.score}</Descriptions.Item>
        <Descriptions.Item itemKey="结果">{run.passed ? "passed" : "failed"}</Descriptions.Item>
        <Descriptions.Item itemKey="Trace">{run.traceId}</Descriptions.Item>
        <Descriptions.Item itemKey="时间">{run.createdAt}</Descriptions.Item>
      </Descriptions>
    </Card>
  );
}
