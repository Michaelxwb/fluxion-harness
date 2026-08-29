/**
 * C403 NodeConfigForm（TASK-012 / CMP-09）：按节点 type 渲染 V2 判别联合字段集，
 * 切换类型字段集随之切换；props 只读，变更经 onChange 上抛（不原地修改）。
 */
import { Input, InputNumber, Select, TextArea } from "@douyinfe/semi-ui";

import type { WorkflowV2Node, WorkflowV2NodeKind } from "../../types/console";

interface NodeConfigFormProps {
  readonly node: WorkflowV2Node;
  readonly onChange: (node: WorkflowV2Node) => void;
}

const NODE_KIND_OPTIONS: { label: string; value: WorkflowV2NodeKind }[] = [
  { label: "能力节点 capability", value: "capability" },
  { label: "智能体节点 agent", value: "agent" },
  { label: "条件路由 condition", value: "condition" },
  { label: "多路路由 switch", value: "switch" },
  { label: "并行分支 parallel", value: "parallel" },
  { label: "值变换 transform", value: "transform" },
  { label: "定时等待 wait", value: "wait" },
  { label: "人工审批 human_task", value: "human_task" },
  { label: "子流程 subworkflow", value: "subworkflow" }
];

export function NodeConfigForm({ node, onChange }: NodeConfigFormProps) {
  const patch = (fields: Partial<WorkflowV2Node>): void => {
    onChange({ ...node, ...fields } as WorkflowV2Node);
  };
  const record = node as unknown as Record<string, unknown>;

  return (
    <section aria-label="节点配置" className="node-config-form">
      <Select
        aria-label="节点类型"
        optionList={NODE_KIND_OPTIONS}
        onChange={(value) => onChange(switchType(node, value as WorkflowV2NodeKind))}
        value={node.type}
      />
      <Input
        aria-label="id"
        placeholder="节点 ID（流程内唯一）"
        onChange={(value) => patch({ id: value } as Partial<WorkflowV2Node>)}
        value={node.id}
      />
      <Input
        aria-label="depends_on"
        placeholder="前置节点（逗号分隔）"
        onChange={(value) => patch({ depends_on: splitList(value) } as Partial<WorkflowV2Node>)}
        value={(node.depends_on ?? []).join(",")}
      />

      {node.type === "capability" ? (
        <>
          <Input
            aria-label="capability_ref"
            placeholder="(skill|tool|mcp|plugin):<id>@<version>"
            onChange={(value) => patch({ capability_ref: value } as Partial<WorkflowV2Node>)}
            value={typeof record.capability_ref === "string" ? record.capability_ref : ""}
          />
          <JsonField
            ariaLabel="input"
            placeholder="静态输入 JSON 对象"
            value={record.input}
            onChange={(value) => patch({ input: value } as Partial<WorkflowV2Node>)}
          />
        </>
      ) : null}

      {node.type === "agent" ? (
        <>
          <Input
            aria-label="agent_ref"
            placeholder="agent:<id>@<version>"
            onChange={(value) => patch({ agent_ref: value } as Partial<WorkflowV2Node>)}
            value={typeof record.agent_ref === "string" ? record.agent_ref : ""}
          />
          <TextArea
            aria-label="prompt"
            onChange={(value) => patch({ prompt: value } as Partial<WorkflowV2Node>)}
            placeholder="提示词"
            value={typeof record.prompt === "string" ? record.prompt : ""}
          />
        </>
      ) : null}

      {node.type === "condition" ? (
        <>
          <TextArea
            aria-label="expression"
            onChange={(value) => patch({ expression: value } as Partial<WorkflowV2Node>)}
            placeholder="谓词表达式（支持 {{ node_id.output }} 插值）"
            value={typeof record.expression === "string" ? record.expression : ""}
          />
          <Input
            aria-label="then"
            placeholder="真分支后继（逗号分隔）"
            onChange={(value) => patch({ then: splitList(value) } as Partial<WorkflowV2Node>)}
            value={Array.isArray(record.then) ? (record.then as string[]).join(",") : ""}
          />
          <Input
            aria-label="else"
            placeholder="假分支后继（逗号分隔）"
            onChange={(value) =>
              patch({ else: splitList(value) } as unknown as Partial<WorkflowV2Node>)
            }
            value={Array.isArray(record.else) ? (record.else as string[]).join(",") : ""}
          />
        </>
      ) : null}

      {node.type === "switch" ? (
        <>
          <TextArea
            aria-label="expression"
            onChange={(value) => patch({ expression: value } as Partial<WorkflowV2Node>)}
            placeholder="路由表达式"
            value={typeof record.expression === "string" ? record.expression : ""}
          />
          <JsonField
            ariaLabel="cases"
            placeholder='分支 JSON 数组：[{"value":"a","node_ids":["n1"]}]'
            value={record.cases}
            onChange={(value) => patch({ cases: value } as Partial<WorkflowV2Node>)}
          />
        </>
      ) : null}

      {node.type === "parallel" ? (
        <>
          <JsonField
            ariaLabel="branches"
            placeholder='并行分支 JSON 数组（≥2）：[{"branch_id":"b1","node_ids":["n1"]}]'
            value={record.branches}
            onChange={(value) => patch({ branches: value } as Partial<WorkflowV2Node>)}
          />
          <Select
            aria-label="join_policy"
            optionList={[
              { label: "all（全部分支完成）", value: "all" },
              { label: "any（任一分支完成）", value: "any" }
            ]}
            onChange={(value) => patch({ join_policy: value } as Partial<WorkflowV2Node>)}
            value={typeof record.join_policy === "string" ? record.join_policy : "all"}
          />
        </>
      ) : null}

      {node.type === "transform" ? (
        <>
          <Input
            aria-label="source"
            onChange={(value) => patch({ source: value } as Partial<WorkflowV2Node>)}
            placeholder="来源引用"
            value={typeof record.source === "string" ? record.source : ""}
          />
          <TextArea
            aria-label="transform"
            onChange={(value) => patch({ transform: value } as Partial<WorkflowV2Node>)}
            placeholder="变换模板（支持 {{ node_id.output }} 插值）"
            value={typeof record.transform === "string" ? record.transform : ""}
          />
        </>
      ) : null}

      {node.type === "wait" ? (
        <InputNumber
          aria-label="duration_seconds"
          onChange={(value) =>
            patch({ duration_seconds: value ?? 0 } as Partial<WorkflowV2Node>)
          }
          placeholder="等待秒数（> 0）"
          value={typeof record.duration_seconds === "number" ? record.duration_seconds : undefined}
        />
      ) : null}

      {node.type === "human_task" ? (
        <>
          <Input
            aria-label="assignee"
            onChange={(value) => patch({ assignee: value } as Partial<WorkflowV2Node>)}
            placeholder="审批人（user ref / role）"
            value={typeof record.assignee === "string" ? record.assignee : ""}
          />
          <TextArea
            aria-label="message"
            onChange={(value) => patch({ message: value } as Partial<WorkflowV2Node>)}
            placeholder="审批提示"
            value={typeof record.message === "string" ? record.message : ""}
          />
          <InputNumber
            aria-label="timeout_seconds"
            onChange={(value) =>
              patch({ timeout_seconds: value ?? undefined } as Partial<WorkflowV2Node>)
            }
            placeholder="审批超时（秒，可选）"
            value={
              typeof record.timeout_seconds === "number" ? record.timeout_seconds : undefined
            }
          />
        </>
      ) : null}

      {node.type === "subworkflow" ? (
        <Input
          aria-label="workflow_ref"
          onChange={(value) => patch({ workflow_ref: value } as Partial<WorkflowV2Node>)}
          placeholder="workflow:<id>@<version>"
          value={typeof record.workflow_ref === "string" ? record.workflow_ref : ""}
        />
      ) : null}
    </section>
  );
}

/** JSON 字段（object/array）：编辑态为序列化文本，失焦解析；解析失败保留原文由校验兜底。 */
function JsonField(props: {
  readonly ariaLabel: string;
  readonly placeholder: string;
  readonly value: unknown;
  readonly onChange: (value: unknown) => void;
}) {
  const text =
    props.value === undefined || props.value === null
      ? ""
      : JSON.stringify(props.value, null, 0);
  return (
    <TextArea
      aria-label={props.ariaLabel}
      onBlur={(event) => {
        const raw = event.target.value.trim();
        if (!raw) {
          props.onChange(undefined);
          return;
        }
        try {
          props.onChange(JSON.parse(raw));
        } catch {
          // 保留原文交由校验诊断（不静默吞掉语法错误）
          props.onChange(raw);
        }
      }}
      placeholder={props.placeholder}
      defaultValue={text}
      key={text}
    />
  );
}

/** 类型切换：保留公共字段（id/depends_on/timeout_ms/retry_policy/output_schema），
 * kind 字段重置为该类型默认值。P2（review）：retry_policy/output_schema 是
 * WorkflowV2NodeBase 公共契约字段，切换类型不得静默丢弃。 */
function switchType(node: WorkflowV2Node, kind: WorkflowV2NodeKind): WorkflowV2Node {
  const common = {
    depends_on: node.depends_on ?? [],
    id: node.id,
    timeout_ms: node.timeout_ms,
    retry_policy: node.retry_policy,
    output_schema: node.output_schema
  };
  switch (kind) {
    case "capability":
      return { ...common, capability_ref: "", input: {}, type: kind };
    case "agent":
      return { ...common, agent_ref: "", input: {}, prompt: "", type: kind };
    case "condition":
      return { ...common, else: [], expression: "", then: [], type: kind };
    case "switch":
      return { ...common, cases: [], default: [], expression: "", type: kind };
    case "parallel":
      return { ...common, branches: [], join_policy: "all", type: kind };
    case "transform":
      return { ...common, source: "", transform: "", type: kind };
    case "wait":
      return { ...common, duration_seconds: 0, type: kind };
    case "human_task":
      return { ...common, assignee: "", message: "", type: kind };
    case "subworkflow":
      return { ...common, input: {}, workflow_ref: "", type: kind };
  }
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

export { NODE_KIND_OPTIONS };
