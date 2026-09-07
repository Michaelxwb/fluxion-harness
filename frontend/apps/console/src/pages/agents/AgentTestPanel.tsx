import { useState } from "react";

import { Button, Input, Space, Timeline, Typography } from "@douyinfe/semi-ui";
import { useNavigate } from "react-router-dom";

import type { ConsoleApi, JsonRecord } from "../../types/console";

interface AgentTestPanelProps {
  readonly agentId: string;
  readonly api: ConsoleApi;
  readonly onCompleted: (traceId: string) => void;
}

function eventData(value: unknown): JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

/** TASK-012：Editor 内真实 test-run SSE 面板，按执行阶段投影 Timeline。 */
export function AgentTestPanel({ agentId, api, onCompleted }: AgentTestPanelProps) {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [output, setOutput] = useState("");
  const [traceId, setTraceId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(): Promise<void> {
    if (!prompt.trim()) {
      setError("请输入测试 Prompt");
      return;
    }
    setRunning(true);
    setStarted(true);
    setCompleted(false);
    setOutput("");
    setTraceId(null);
    setError(null);
    try {
      await api.testRunAgent(agentId, { input: prompt.trim() }, ({ event, data }) => {
        const payload = eventData(data);
        if (event === "token") {
          setOutput((current) => current + String(payload.content ?? payload.text ?? ""));
        }
        if (event === "completed") {
          const nextOutput = String(payload.output ?? "");
          const nextTraceId = String(payload.trace_id ?? "");
          if (nextOutput) setOutput(nextOutput);
          if (nextTraceId) {
            setTraceId(nextTraceId);
            onCompleted(nextTraceId);
          }
          setCompleted(true);
        }
        if (event === "error") setError(String(payload.message ?? "执行失败"));
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "执行失败");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div aria-label="Agent 测试" style={{ display: "grid", gap: 16 }}>
      <Input
        aria-label="测试 Prompt"
        onChange={(value) => setPrompt(String(value))}
        placeholder="输入一条消息验证当前智能体"
        value={prompt}
      />
      <Space>
        <Button loading={running} onClick={() => void run()} theme="solid" type="primary">
          运行测试
        </Button>
        {traceId ? <Typography.Text copyable>{`Trace ID: ${traceId}`}</Typography.Text> : null}
        {traceId ? (
          <Button
            onClick={() => navigate(`/operations/runs?keyword=${encodeURIComponent(traceId)}`)}
            size="small"
            type="tertiary"
          >
            跳转执行记录
          </Button>
        ) : null}
      </Space>
      {started ? (
        <div aria-label="测试对话" style={{ display: "grid", gap: 8 }}>
          <div aria-label="测试输入回显" className="test-bubble test-bubble--user">
            <Typography.Text>{prompt}</Typography.Text>
          </div>
          {running && !output ? (
            <div className="test-bubble test-bubble--assistant">
              <Typography.Text type="tertiary">正在输入…</Typography.Text>
            </div>
          ) : null}
          {output ? (
            <div aria-label="测试输出" className="test-bubble test-bubble--assistant">
              <Typography.Text>{output}</Typography.Text>
            </div>
          ) : null}
        </div>
      ) : null}
      {started ? (
        <Timeline aria-label="测试 Timeline">
          <Timeline.Item>输入 Prompt</Timeline.Item>
          {output ? <Timeline.Item>Model 调用</Timeline.Item> : null}
          {completed ? <Timeline.Item>执行完成</Timeline.Item> : null}
        </Timeline>
      ) : null}
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
    </div>
  );
}
