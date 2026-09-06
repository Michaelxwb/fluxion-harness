import { useEffect, useMemo, useState } from "react";

import { Button, Empty, Select, Spin, Tag, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, EvalRunSummary, EvalSetSummary } from "../../types/console";

interface AgentEvalPanelProps {
  readonly agentId: string;
  readonly api: ConsoleApi;
  readonly latestTraceId: string | null;
}

/** TASK-012：Agent 生命周期内的 EvalSet 选择与 EvalRun 结果，不新增一级 Eval 菜单。 */
export function AgentEvalPanel({ agentId, api, latestTraceId }: AgentEvalPanelProps) {
  const [sets, setSets] = useState<readonly EvalSetSummary[] | null>(null);
  const [runs, setRuns] = useState<readonly EvalRunSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [candidate, setCandidate] = useState<EvalRunSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([api.listEvalSets(), api.listEvalRuns()]).then(
      ([allSets, allRuns]) => {
        if (!active) return;
        setSets(
          allSets.filter(
            (item) => item.targetKind === "agent_definition" && item.targetId === agentId
          )
        );
        setRuns(allRuns);
      },
      (cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "评测数据加载失败");
      }
    );
    return () => {
      active = false;
    };
  }, [agentId, api]);

  const selected = sets?.find((item) => item.id === selectedId) ?? null;
  const baseline = useMemo(
    () => runs.find((run) => run.evalSetId === selectedId) ?? null,
    [runs, selectedId]
  );

  async function trigger(): Promise<void> {
    if (!selected) {
      setError("请选择评测集");
      return;
    }
    if (!latestTraceId) {
      setError("请先在「测试」页完成一次 Test Run，以生成可追溯 Trace");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.triggerEvalRun({
        evalSetId: selected.id,
        evalSetVersion: selected.version,
        traceId: latestTraceId
      });
      setCandidate(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "评测执行失败");
    } finally {
      setBusy(false);
    }
  }

  if (sets === null && !error) return <Spin />;
  if (sets?.length === 0) {
    return <Empty description="暂无面向此 Agent 的 EvalSet，请先创建评测集" />;
  }
  return (
    <div aria-label="Agent 评测" style={{ display: "grid", gap: 16 }}>
      <Typography.Text id="agent-eval-set-label">评测集</Typography.Text>
      <Select
        aria-labelledby="agent-eval-set-label"
        onChange={(value) => setSelectedId(String(value ?? ""))}
        optionList={(sets ?? []).map((item) => ({
          label: `${item.name}（${item.caseCount} 条用例）`,
          value: item.id
        }))}
        value={selectedId}
      />
      <Button loading={busy} onClick={() => void trigger()} theme="solid" type="primary">
        发起评测
      </Button>
      {candidate ? (
        <div aria-label="评测结果">
          <Typography.Text strong>{`Candidate Score ${candidate.score.toFixed(2)}`}</Typography.Text>
          <Tag color={candidate.passed ? "green" : "red"}>{candidate.passed ? "通过" : "未通过"}</Tag>
          <Typography.Paragraph type="tertiary">
            {baseline ? `Baseline Score ${baseline.score.toFixed(2)}` : "Baseline 暂无"}
          </Typography.Paragraph>
        </div>
      ) : null}
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
    </div>
  );
}
