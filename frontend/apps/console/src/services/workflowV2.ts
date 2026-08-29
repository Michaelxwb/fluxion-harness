/**
 * TASK-002：WorkflowDefinition V2 九节点判别联合 schema 与校验器
 * （Phase 3 backend `resources/workflow_nodes.py` 契约对齐）。
 *
 * - 节点字段与 spec JSON 同形（snake_case）；V1 legacy step（无 type + capability_ref）
 *   兼容注入 `type: "capability"`；顶层遗留 `engine_ref` 忽略（remediation §14.3）。
 * - 诊断逐字段定位（E-02）：`{ nodeId?, field, message }`。
 * - 插值校验：`{{ node_id.output }}` 引用的 node_id 必须存在。
 */

import type {
  WorkflowDraftV2,
  WorkflowNodeFieldSchema,
  WorkflowSchemaV2,
  WorkflowV2Diagnostic,
  WorkflowValidationResultV2,
  WorkflowV2NodeKind
} from "../types/console";

const NODE_KINDS: readonly WorkflowV2NodeKind[] = [
  "capability",
  "agent",
  "condition",
  "switch",
  "parallel",
  "transform",
  "wait",
  "human_task",
  "subworkflow"
];

const AGENT_REF_PATTERN = /^agent:[^@\s]+@[^@\s]+$/;
const WORKFLOW_REF_PATTERN = /^workflow:[^@\s]+@[^@\s]+$/;
const CAPABILITY_REF_PATTERN = /^(skill|tool|mcp|plugin):[^@\s]+@[^@\s]+$/;
const INTERPOLATION_PATTERN = /\{\{\s*([A-Za-z0-9_-]+)\./g;

const COMMON_FIELDS: readonly WorkflowNodeFieldSchema[] = [
  { field: "id", required: true, title: "节点 ID", type: "string" },
  { field: "depends_on", required: false, title: "前置节点", type: "array" },
  { field: "timeout_ms", required: false, title: "节点超时（毫秒）", type: "number" },
  { field: "retry_policy", required: false, title: "重试意愿", type: "object" }
];

const KIND_TITLES: Readonly<Record<WorkflowV2NodeKind, string>> = {
  capability: "能力节点",
  agent: "智能体节点",
  condition: "条件路由",
  switch: "多路路由",
  parallel: "并行分支",
  transform: "值变换",
  wait: "定时等待",
  human_task: "人工审批",
  subworkflow: "子流程"
};

const KIND_FIELDS: Readonly<Record<WorkflowV2NodeKind, readonly WorkflowNodeFieldSchema[]>> = {
  capability: [
    { field: "capability_ref", required: true, title: "能力引用", type: "string", description: "(skill|tool|mcp|plugin):<id>@<version>" },
    { field: "input", required: false, title: "静态输入", type: "object" }
  ],
  agent: [
    { field: "agent_ref", required: true, title: "Agent 引用", type: "string", description: "agent:<id>@<version>" },
    { field: "prompt", required: false, title: "提示词", type: "string" },
    { field: "max_turns", required: false, title: "回合上限", type: "number" },
    { field: "input", required: false, title: "静态输入", type: "object" }
  ],
  condition: [
    { field: "expression", required: true, title: "谓词表达式", type: "string", description: "白名单表达式，支持 {{ node_id.output }} 插值" },
    { field: "then", required: true, title: "真分支后继", type: "array" },
    { field: "else", required: false, title: "假分支后继", type: "array" }
  ],
  switch: [
    { field: "expression", required: true, title: "路由表达式", type: "string" },
    { field: "cases", required: true, title: "分支", type: "array", description: "至少 1 项：{ value, node_ids }" },
    { field: "default", required: false, title: "默认后继", type: "array" }
  ],
  parallel: [
    { field: "branches", required: true, title: "并行分支", type: "array", description: "至少 2 项：{ branch_id, node_ids }" },
    { field: "join_policy", required: false, title: "汇聚策略", type: "string", description: "all | any" }
  ],
  transform: [
    { field: "source", required: true, title: "来源引用", type: "string" },
    { field: "transform", required: true, title: "变换模板", type: "string" }
  ],
  wait: [
    { field: "duration_seconds", required: true, title: "等待秒数", type: "number", description: "> 0" }
  ],
  human_task: [
    { field: "assignee", required: true, title: "审批人", type: "string", description: "user ref / role" },
    { field: "message", required: false, title: "审批提示", type: "string" },
    { field: "timeout_seconds", required: false, title: "审批超时（秒）", type: "number" }
  ],
  subworkflow: [
    { field: "workflow_ref", required: true, title: "子流程引用", type: "string", description: "workflow:<id>@<version>" },
    { field: "input", required: false, title: "静态输入", type: "object" }
  ]
};

/** V2 schema 契约冻结（getWorkflowSchema 数据源）。 */
export const WORKFLOW_V2_SCHEMA: WorkflowSchemaV2 = {
  nodeKinds: NODE_KINDS.map((kind) => ({
    fields: [...COMMON_FIELDS, ...KIND_FIELDS[kind]],
    kind,
    title: KIND_TITLES[kind]
  }))
};

interface DraftNode {
  readonly id: string;
  readonly type?: string;
  readonly [key: string]: unknown;
}

/** V2 校验（判别联合字段完整性 + 结构约束 + 插值存在性）。 */
export function validateWorkflowV2(
  draft: WorkflowDraftV2,
  capabilities: ReadonlySet<string>
): WorkflowValidationResultV2 {
  const diagnostics: WorkflowV2Diagnostic[] = [];
  const record = draft as unknown as Record<string, unknown>;

  if (typeof record.name !== "string" || !record.name.trim()) {
    diagnostics.push({ field: "name", message: "name 必须是非空字符串" });
  }
  if (!Array.isArray(record.steps) || record.steps.length === 0) {
    diagnostics.push({ field: "steps", message: "steps 必须是非空数组" });
    return { diagnostics, valid: false };
  }

  const nodes: DraftNode[] = record.steps.map((step) => normalizeNode(step));
  const ids = new Set<string>();
  for (const node of nodes) {
    if (typeof node.id !== "string" || !node.id.trim()) {
      diagnostics.push({ field: "id", message: "节点 id 必须是非空字符串", nodeId: undefined });
    } else if (ids.has(node.id)) {
      diagnostics.push({ field: "id", message: `节点 ID 重复: ${node.id}`, nodeId: node.id });
    } else {
      ids.add(node.id);
    }
  }

  for (const node of nodes) {
    validateNodeFields(node, capabilities, diagnostics);
  }
  validateReferences(nodes, ids, diagnostics);
  validateInterpolations(nodes, ids, diagnostics);

  return { diagnostics, valid: diagnostics.length === 0 };
}

/** V1 兼容：无 type 且含 capability_ref 的 step 注入 type="capability"。 */
function normalizeNode(step: unknown): DraftNode {
  const node = (typeof step === "object" && step !== null ? step : {}) as DraftNode;
  if (node.type === undefined && typeof node.capability_ref === "string") {
    return { ...node, type: "capability" };
  }
  return node;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateNodeFields(
  node: DraftNode,
  capabilities: ReadonlySet<string>,
  diagnostics: WorkflowV2Diagnostic[]
): void {
  const nodeId = typeof node.id === "string" ? node.id : undefined;
  const push = (field: string, message: string): void => {
    diagnostics.push({ field, message, nodeId });
  };

  const kind = node.type;
  if (typeof kind !== "string" || !NODE_KINDS.includes(kind as WorkflowV2NodeKind)) {
    push("type", `未知节点类型: ${String(kind)}`);
    return;
  }

  if (kind === "capability") {
    const ref = node.capability_ref;
    if (typeof ref !== "string" || !ref) {
      push("capability_ref", "capability_ref 必填");
    } else if (!CAPABILITY_REF_PATTERN.test(ref)) {
      push("capability_ref", `capability_ref 格式非法: ${ref}`);
    } else if (capabilities.size > 0 && !capabilities.has(ref)) {
      push("capability_ref", `Capability ref 不可用: ${ref}`);
    }
  }

  if (kind === "agent") {
    const ref = node.agent_ref;
    if (typeof ref !== "string" || !ref) {
      push("agent_ref", "agent_ref 必填");
    } else if (!AGENT_REF_PATTERN.test(ref)) {
      push("agent_ref", `agent_ref 格式非法（须为 agent:<id>@<version>）: ${ref}`);
    }
  }

  if (kind === "condition") {
    if (typeof node.expression !== "string" || !node.expression) {
      push("expression", "expression 必填");
    }
    if (!Array.isArray(node.then)) {
      push("then", "then 必须是后继节点 ID 数组");
    }
  }

  if (kind === "switch") {
    const cases = node.cases;
    if (!Array.isArray(cases) || cases.length === 0) {
      push("cases", "cases 必须是至少 1 项的分支数组");
    } else {
      // P2（review）：后端 SwitchCase 契约——每项必须 { value, node_ids[] }；
      // 前端校验对齐，避免"前端判 valid、切 HTTP 后端拒"。
      cases.forEach((entry, index) => {
        if (!isRecord(entry)) {
          push("cases", `cases[${index}] 必须是 { value, node_ids } 对象`);
          return;
        }
        if (typeof entry.value !== "string" || !entry.value) {
          push("cases", `cases[${index}].value 必填（分支匹配值）`);
        }
        if (!Array.isArray(entry.node_ids) || entry.node_ids.length === 0) {
          push("cases", `cases[${index}].node_ids 必须是后继节点 ID 数组`);
        }
      });
    }
  }

  if (kind === "parallel") {
    const branches = node.branches;
    if (!Array.isArray(branches) || branches.length < 2) {
      push("branches", "branches 必须是至少 2 项的并行分支数组");
    } else {
      // P2（review）：后端 ParallelBranch 契约——每项必须 { branch_id, node_ids[] }。
      branches.forEach((entry, index) => {
        if (!isRecord(entry)) {
          push("branches", `branches[${index}] 必须是 { branch_id, node_ids } 对象`);
          return;
        }
        if (typeof entry.branch_id !== "string" || !entry.branch_id) {
          push("branches", `branches[${index}].branch_id 必填（分支标识）`);
        }
        if (!Array.isArray(entry.node_ids) || entry.node_ids.length === 0) {
          push("branches", `branches[${index}].node_ids 必须是后继节点 ID 数组`);
        }
      });
    }
    const joinPolicy = node.join_policy;
    if (joinPolicy !== undefined && joinPolicy !== "all" && joinPolicy !== "any") {
      push("join_policy", `join_policy 只能是 all|any: ${String(joinPolicy)}`);
    }
  }

  if (kind === "transform") {
    if (typeof node.transform !== "string" || !node.transform) {
      push("transform", "transform 模板必填");
    }
    if (typeof node.source !== "string" || !node.source) {
      push("source", "source 引用必填");
    }
  }

  if (kind === "wait") {
    const duration = node.duration_seconds;
    if (typeof duration !== "number" || !(duration > 0)) {
      push("duration_seconds", `duration_seconds 必须大于 0: ${String(duration)}`);
    }
  }

  if (kind === "human_task") {
    if (typeof node.assignee !== "string" || !node.assignee) {
      push("assignee", "assignee 必填");
    }
    const timeout = node.timeout_seconds;
    if (timeout !== undefined && (typeof timeout !== "number" || !(timeout > 0))) {
      push("timeout_seconds", `timeout_seconds 必须大于 0: ${String(timeout)}`);
    }
  }

  if (kind === "subworkflow") {
    const ref = node.workflow_ref;
    if (typeof ref !== "string" || !ref) {
      push("workflow_ref", "workflow_ref 必填");
    } else if (!WORKFLOW_REF_PATTERN.test(ref)) {
      push("workflow_ref", `workflow_ref 格式非法（须为 workflow:<id>@<version>）: ${ref}`);
    }
  }
}

function validateReferences(
  nodes: readonly DraftNode[],
  ids: ReadonlySet<string>,
  diagnostics: WorkflowV2Diagnostic[]
): void {
  for (const node of nodes) {
    const nodeId = typeof node.id === "string" ? node.id : undefined;
    const push = (field: string, message: string): void => {
      diagnostics.push({ field, message, nodeId });
    };

    if (Array.isArray(node.depends_on)) {
      for (const dep of node.depends_on) {
        if (typeof dep !== "string" || !ids.has(dep)) {
          push("depends_on", `前置节点不存在: ${String(dep)}`);
        }
      }
    }

    const routingFields: readonly string[] = ["then", "else", "default"];
    for (const field of routingFields) {
      const targets = node[field];
      if (Array.isArray(targets)) {
        for (const target of targets) {
          if (typeof target !== "string" || !ids.has(target)) {
            push(field, `路由后继节点不存在: ${String(target)}`);
          }
        }
      }
    }

    const cases = node.cases;
    if (Array.isArray(cases)) {
      cases.forEach((item, index) => {
        if (typeof item === "object" && item !== null && Array.isArray(item.node_ids)) {
          for (const target of item.node_ids) {
            if (typeof target !== "string" || !ids.has(target)) {
              push("cases", `cases[${index}] 后继节点不存在: ${String(target)}`);
            }
          }
        }
      });
    }

    const branches = node.branches;
    if (Array.isArray(branches)) {
      branches.forEach((item, index) => {
        if (typeof item === "object" && item !== null && Array.isArray(item.node_ids)) {
          for (const target of item.node_ids) {
            if (typeof target !== "string" || !ids.has(target)) {
              push("branches", `branches[${index}] 成员节点不存在: ${String(target)}`);
            }
          }
        }
      });
    }
  }

  if (hasCycle(nodes, ids)) {
    diagnostics.push({
      field: "depends_on",
      message: "depends_on 存在环依赖"
    });
  }
}

/** Kahn 拓扑排序环检测（对齐 backend `_validate_workflow_dependencies`）。 */
function hasCycle(nodes: readonly DraftNode[], ids: ReadonlySet<string>): boolean {
  const indegree = new Map<string, number>();
  const adjacency = new Map<string, string[]>();
  for (const id of ids) {
    indegree.set(id, 0);
    adjacency.set(id, []);
  }
  for (const node of nodes) {
    if (typeof node.id !== "string" || !indegree.has(node.id)) continue;
    for (const dep of Array.isArray(node.depends_on) ? node.depends_on : []) {
      if (typeof dep !== "string" || !adjacency.has(dep)) continue;
      adjacency.get(dep)!.push(node.id);
      indegree.set(node.id, (indegree.get(node.id) ?? 0) + 1);
    }
  }
  const queue = [...indegree.entries()].filter(([, degree]) => degree === 0).map(([id]) => id);
  let visited = 0;
  while (queue.length > 0) {
    const current = queue.shift()!;
    visited += 1;
    for (const next of adjacency.get(current) ?? []) {
      const degree = (indegree.get(next) ?? 0) - 1;
      indegree.set(next, degree);
      if (degree === 0) queue.push(next);
    }
  }
  return visited !== ids.size;
}

function validateInterpolations(
  nodes: readonly DraftNode[],
  ids: ReadonlySet<string>,
  diagnostics: WorkflowV2Diagnostic[]
): void {
  for (const node of nodes) {
    const nodeId = typeof node.id === "string" ? node.id : undefined;
    const check = (field: string, value: unknown): void => {
      if (typeof value !== "string") return;
      INTERPOLATION_PATTERN.lastIndex = 0;
      for (const match of value.matchAll(INTERPOLATION_PATTERN)) {
        const referenced = match[1]!;
        if (!ids.has(referenced)) {
          diagnostics.push({
            field,
            message: `插值引用的节点不存在: {{ ${referenced}.… }}`,
            nodeId
          });
        }
      }
    };
    check("expression", node.expression);
    check("source", node.source);
    check("transform", node.transform);
    check("message", node.message);
    if (node.input !== undefined && typeof node.input === "object") {
      for (const value of Object.values(node.input as Record<string, unknown>)) {
        check("input", value);
      }
    }
  }
}
