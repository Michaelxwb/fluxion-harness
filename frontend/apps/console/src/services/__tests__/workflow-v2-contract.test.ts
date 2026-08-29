/**
 * TASK-002 契约一致性验收（Acceptance-Refs: S-10~S-14 数据源前置）。
 *
 * 真实边界：in-memory 校验器 vs Phase 3 WorkflowDefinition V2 九节点判别联合契约
 * （backend resources/workflow_nodes.py 对齐）；诊断逐字段定位（E-02 基础）。
 */
import { describe, expect, it } from "vitest";

import { createInMemoryConsoleApi } from "../inMemoryConsoleApi";
import type {
  WorkflowDraftV2,
  WorkflowV2NodeKind
} from "../../types/console";

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

function api() {
  return createInMemoryConsoleApi({
    actorId: "admin-001",
    audit: [],
    bindings: [],
    credentials: [],
    capabilities: ["skill:report-source@1", "tool:mailer@2", "mcp:search@1"],
    resources: [],
    runs: [],
    tenantId: "tenant-a"
  });
}

interface MutableDraft {
  name: string;
  display_name?: string;
  description?: string;
  steps: Record<string, unknown>[];
}

function validMixedDraft(): MutableDraft {
  return {
    name: "weekly-report",
    display_name: "每周报表",
    description: "每周经营报表",
    steps: [
      {
        id: "collect",
        type: "capability",
        capability_ref: "skill:report-source@1",
        depends_on: [],
        input: { period: "last-week" }
      },
      {
        id: "check",
        type: "condition",
        depends_on: ["collect"],
        expression: "{{ collect.output.rows }} > 0",
        then: ["notify"],
        else: []
      },
      {
        id: "notify",
        type: "capability",
        capability_ref: "tool:mailer@2",
        depends_on: ["check"],
        input: { body: "{{ collect.output.rows }} 行数据已就绪" }
      }
    ]
  };
}

describe("TASK-002 getWorkflowSchema 契约冻结", () => {
  it("返回 9 种节点判别联合的 schema（kind + 字段集）", async () => {
    const schema = await api().getWorkflowSchema();
    const kinds = schema.nodeKinds.map((kind) => kind.kind);
    expect(kinds).toEqual([...NODE_KINDS]);
  });

  it("每类节点的必填字段与 V2 契约一致（诊断一一对应的基础）", async () => {
    const schema = await api().getWorkflowSchema();
    const byKind = new Map(schema.nodeKinds.map((kind) => [kind.kind, kind]));
    expect(
      byKind.get("capability")!.fields.find((f) => f.field === "capability_ref")?.required
    ).toBe(true);
    expect(byKind.get("agent")!.fields.find((f) => f.field === "agent_ref")?.required).toBe(true);
    expect(byKind.get("condition")!.fields.find((f) => f.field === "expression")?.required).toBe(
      true
    );
    expect(byKind.get("switch")!.fields.find((f) => f.field === "cases")?.required).toBe(true);
    expect(byKind.get("parallel")!.fields.find((f) => f.field === "branches")?.required).toBe(true);
    expect(byKind.get("transform")!.fields.find((f) => f.field === "transform")?.required).toBe(
      true
    );
    expect(byKind.get("wait")!.fields.find((f) => f.field === "duration_seconds")?.required).toBe(
      true
    );
    expect(byKind.get("human_task")!.fields.find((f) => f.field === "assignee")?.required).toBe(
      true
    );
    expect(byKind.get("subworkflow")!.fields.find((f) => f.field === "workflow_ref")?.required).toBe(
      true
    );
  });
});

describe("TASK-002 validateWorkflow V2 校验", () => {
  it("合法混合图通过（含插值引用存在）", async () => {
    const result = await api().validateWorkflow(validMixedDraft() as unknown as WorkflowDraftV2);
    expect(result.valid).toBe(true);
    expect(result.diagnostics).toEqual([]);
  });

  it("V1 兼容：无 type 的 legacy step（含顶层 engine_ref）注入 capability 并通过", async () => {
    const draft = {
      name: "legacy",
      engine_ref: "workflow-engine://primary",
      steps: [{ id: "collect", capability_ref: "skill:report-source@1" }]
    } as unknown as WorkflowDraftV2;
    const result = await api().validateWorkflow(draft as unknown as WorkflowDraftV2);
    expect(result.valid).toBe(true);
  });

  it("未知节点类型被拒且诊断定位字段", async () => {
    const draft = validMixedDraft();
    draft.steps[0]!.type = "mystery";
    const result = await api().validateWorkflow(draft as unknown as WorkflowDraftV2);
    expect(result.valid).toBe(false);
    expect(result.diagnostics.some((d) => d.nodeId === "collect" && d.field === "type")).toBe(true);
  });

  it.each([
    ["capability", { capability_ref: undefined }, "capability_ref"],
    ["agent", { agent_ref: "bad-format" }, "agent_ref"],
    ["condition", { expression: undefined }, "expression"],
    ["condition", { then: ["missing-node"] }, "then"],
    ["switch", { cases: undefined }, "cases"],
    ["switch", { cases: [] }, "cases"],
    // P2（review）：嵌套结构对齐后端契约——case 缺 value/node_ids 同样报 cases
    ["switch", { cases: [{ value: "a" }] }, "cases"],
    ["switch", { cases: [{ node_ids: ["notify"] }] }, "cases"],
    ["parallel", { branches: [{ branch_id: "b1", node_ids: ["collect"] }] }, "branches"],
    // P2（review）：branch 缺 branch_id / node_ids 为空同样报 branches
    ["parallel", { branches: [{ branch_id: "b1", node_ids: ["collect"] }, { node_ids: ["collect"] }] }, "branches"],
    ["parallel", { branches: [{ branch_id: "b1", node_ids: [] }, { branch_id: "b2", node_ids: ["collect"] }] }, "branches"],
    ["transform", { transform: undefined }, "transform"],
    ["wait", { duration_seconds: 0 }, "duration_seconds"],
    ["human_task", { assignee: undefined }, "assignee"],
    ["subworkflow", { workflow_ref: "not-a-ref" }, "workflow_ref"]
  ] as const)(
    "%s 节点非法配置 → 诊断定位到字段 %s",
    async (kind, overrides, field) => {
      const draft = validMixedDraft();
      const node: Record<string, unknown> = {
        id: "target-node",
        depends_on: ["collect"],
        type: kind
      };
      if (kind === "capability") node.capability_ref = "skill:report-source@1";
      if (kind === "agent") node.agent_ref = "agent:helper@1";
      if (kind === "condition") {
        node.expression = "{{ collect.output.rows }} > 0";
        node.then = ["notify"];
        node.else = [];
      }
      if (kind === "switch") {
        node.expression = "{{ collect.output.kind }}";
        node.cases = [{ value: "a", node_ids: ["notify"] }];
        node.default = [];
      }
      if (kind === "parallel") {
        node.branches = [
          { branch_id: "b1", node_ids: ["collect"] },
          { branch_id: "b2", node_ids: ["collect"] }
        ];
        node.join_policy = "all";
      }
      if (kind === "transform") {
        node.source = "{{ collect.output }}";
        node.transform = "{{ collect.output.rows }} 行";
      }
      if (kind === "wait") node.duration_seconds = 1;
      if (kind === "human_task") {
        node.assignee = "user-a";
        node.message = "请确认";
        node.timeout_seconds = 60;
      }
      if (kind === "subworkflow") node.workflow_ref = "workflow:child@1";
      for (const [key, value] of Object.entries(overrides)) {
        if (value === undefined) delete node[key];
        else node[key] = value;
      }
      draft.steps.push(node);

      const result = await api().validateWorkflow(draft as unknown as WorkflowDraftV2);
      expect(result.valid).toBe(false);
      expect(
        result.diagnostics.some((d) => d.nodeId === "target-node" && d.field === field),
        `diagnostics: ${JSON.stringify(result.diagnostics)}`
      ).toBe(true);
    }
  );

  it("重复节点 ID / depends_on 悬空 / 环依赖 / 插值悬空 全部有对应诊断", async () => {
    const base = validMixedDraft();

    const dup = validMixedDraft();
    dup.steps.push({ ...dup.steps[0]! });
    const dupResult = await api().validateWorkflow(dup as unknown as WorkflowDraftV2);
    expect(dupResult.diagnostics.some((d) => d.field === "id")).toBe(true);

    const dangling = validMixedDraft();
    dangling.steps[2]!.depends_on = ["ghost"];
    const danglingResult = await api().validateWorkflow(dangling as unknown as WorkflowDraftV2);
    expect(danglingResult.diagnostics.some((d) => d.nodeId === "notify" && d.field === "depends_on")).toBe(true);

    const cyclic = validMixedDraft();
    cyclic.steps[0]!.depends_on = ["notify"];
    const cyclicResult = await api().validateWorkflow(cyclic as unknown as WorkflowDraftV2);
    expect(cyclicResult.diagnostics.some((d) => d.field === "depends_on")).toBe(true);

    const interp = validMixedDraft();
    interp.steps[1]!.expression = "{{ ghost.output }} > 0";
    const interpResult = await api().validateWorkflow(interp as unknown as WorkflowDraftV2);
    expect(interpResult.diagnostics.some((d) => d.nodeId === "check" && d.field === "expression")).toBe(true);
    void base;
  });

  it("name 缺失 → 顶层诊断（node_id 为空）", async () => {
    const draft: MutableDraft = validMixedDraft();
    draft.name = "";
    const result = await api().validateWorkflow(draft as unknown as WorkflowDraftV2);
    expect(result.diagnostics.some((d) => d.nodeId === undefined && d.field === "name")).toBe(true);
  });
});

describe("TASK-002 runs/queues/workers 数据源契约", () => {
  it("listWorkflowRuns 返回 workflow_run 投影契约（Phase 3 对齐）", async () => {
    const runs = await api().listWorkflowRuns();
    expect(runs.length).toBeGreaterThan(0);
    for (const run of runs) {
      expect(typeof run.runId).toBe("string");
      expect(typeof run.workflowId).toBe("string");
      expect(typeof run.executionId).toBe("string");
      expect(typeof run.traceId).toBe("string");
      expect(["running", "succeeded", "failed", "cancelled", "paused"]).toContain(run.status);
      expect(typeof run.nodeStates).toBe("object");
      expect(Array.isArray(run.pinnedRefs)).toBe(true);
    }
    expect(runs.some((run) => run.status === "succeeded")).toBe(true);
  });

  it("listWorkflowRuns 可按 workflowId 过滤", async () => {
    const runs = await api().listWorkflowRuns();
    const target = runs[0]!;
    const filtered = await api().listWorkflowRuns(target.workflowId);
    expect(filtered.every((run) => run.workflowId === target.workflowId)).toBe(true);
    expect(filtered.length).toBeGreaterThan(0);
  });

  it("listQueues / listWorkers 返回运营视图契约（⛳依赖缺口 in-memory 先行）", async () => {
    const queues = await api().listQueues();
    expect(queues.length).toBeGreaterThan(0);
    for (const queue of queues) {
      expect(typeof queue.queueId).toBe("string");
      expect(typeof queue.name).toBe("string");
      expect(typeof queue.depth).toBe("number");
      expect(typeof queue.workers).toBe("number");
    }

    const workers = await api().listWorkers();
    expect(workers.length).toBeGreaterThan(0);
    for (const worker of workers) {
      expect(typeof worker.workerId).toBe("string");
      expect(["running", "idle", "stopped"]).toContain(worker.status);
      expect(Array.isArray(worker.queues)).toBe(true);
    }
  });
});
