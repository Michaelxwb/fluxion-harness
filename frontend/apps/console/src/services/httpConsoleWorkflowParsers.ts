import { isRecord } from "@fluxion/shared";

import type {
  User360Summary,
  WorkflowNodeFieldSchema,
  WorkflowNodeKindSchema,
  WorkflowQueueSummary,
  WorkflowRunProjection,
  WorkflowRunStatus,
  WorkflowSchemaV2,
  WorkflowValidationResultV2,
  WorkflowV2Diagnostic,
  WorkflowV2NodeKind,
  WorkflowWorkerSummary
} from "../types/console";
import { requiredRecord, requiredString } from "./httpConsoleParsers";

/** P2（review）：user_360 逐字段校验（原来裸强转，畸形 payload 会静默透传）。 */
export function parseUser360(value: unknown): User360Summary {
  const record = requiredRecord(value, "user_360");
  const identity = requiredRecord(record.identity, "user_360.identity");
  const channels = Array.isArray(identity.channels)
    ? identity.channels.map((channel) => {
        const ch = requiredRecord(channel, "user_360.identity.channels");
        return {
          channel_type: requiredString(ch.channel_type, "channels.channel_type"),
          channel_user_id: requiredString(ch.channel_user_id, "channels.channel_user_id")
        };
      })
    : [];
  return {
    identity: {
      platform_user_id: requiredString(identity.platform_user_id, "identity.platform_user_id"),
      display_name: requiredString(identity.display_name, "identity.display_name"),
      channels
    },
    profile: isRecord(record.profile) ? record.profile : null,
    preferences: isRecord(record.preferences) ? record.preferences : null,
    capabilities: Array.isArray(record.capabilities) ? record.capabilities.filter(isRecord) : [],
    policy: Array.isArray(record.policy) ? record.policy.filter(isRecord) : [],
    activity_count: typeof record.activity_count === "number" ? record.activity_count : 0
  };
}

// ---------------------------------------------------------------------------
// TASK-002 workflow V2 payload 解析（envelope.data → 契约类型；wire snake_case）

export function parseWorkflowSchema(value: unknown): WorkflowSchemaV2 {
  if (!isRecord(value) || !Array.isArray(value.node_kinds)) {
    throw new Error("workflows.schema 响应无效");
  }
  const nodeKinds: WorkflowNodeKindSchema[] = value.node_kinds.map(parseNodeKindSchema);
  return { nodeKinds };
}

function parseNodeKindSchema(value: unknown): WorkflowNodeKindSchema {
  if (!isRecord(value) || typeof value.kind !== "string" || !Array.isArray(value.fields)) {
    throw new Error("workflows.schema.node_kind 响应无效");
  }
  return {
    fields: value.fields.map((field) => {
      if (!isRecord(field) || typeof field.field !== "string") {
        throw new Error("workflows.schema.field 响应无效");
      }
      return {
        description: typeof field.description === "string" ? field.description : undefined,
        field: field.field,
        required: field.required === true,
        title: typeof field.title === "string" ? field.title : field.field,
        type: parseFieldType(field.type)
      };
    }),
    kind: value.kind as WorkflowV2NodeKind,
    title: typeof value.title === "string" ? value.title : value.kind
  };
}

function parseFieldType(value: unknown): WorkflowNodeFieldSchema["type"] {
  const allowed = ["string", "number", "boolean", "object", "array"];
  return typeof value === "string" && allowed.includes(value)
    ? (value as WorkflowNodeFieldSchema["type"])
    : "string";
}

export function parseWorkflowValidation(value: unknown): WorkflowValidationResultV2 {
  if (!isRecord(value) || typeof value.valid !== "boolean" || !Array.isArray(value.diagnostics)) {
    throw new Error("workflows.validate 响应无效");
  }
  const diagnostics: WorkflowV2Diagnostic[] = value.diagnostics.map((item) => {
    if (!isRecord(item) || typeof item.field !== "string" || typeof item.message !== "string") {
      throw new Error("workflows.validate.diagnostic 响应无效");
    }
    return {
      field: item.field,
      message: item.message,
      nodeId: typeof item.node_id === "string" ? item.node_id : undefined
    };
  });
  return { diagnostics, valid: value.valid };
}

export function parseWorkflowRuns(value: unknown): WorkflowRunProjection[] {
  // P1-3：后端列表端点统一 {items, ...} 分页 envelope——兼容裸数组（过渡）
  const items = isRecord(value) && Array.isArray(value.items) ? value.items : value;
  if (!Array.isArray(items)) throw new Error("workflows.runs 响应无效");
  return items.map(parseWorkflowRun);
}

function parseWorkflowRun(value: unknown): WorkflowRunProjection {
  if (!isRecord(value)) throw new Error("workflows.run 响应无效");
  const status = value.status;
  const allowed: readonly WorkflowRunStatus[] = [
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "paused"
  ];
  if (
    typeof status !== "string" ||
    !allowed.includes(status as WorkflowRunStatus)
  ) {
    throw new Error(`workflows.run status 无效: ${String(status)}`);
  }
  return {
    createdAt: requiredString(value.created_at, "created_at"),
    executionId: requiredString(value.execution_id, "execution_id"),
    nodeStates: isRecord(value.node_states)
      ? (value.node_states as WorkflowRunProjection["nodeStates"])
      : {},
    pinnedRefs: Array.isArray(value.pinned_refs)
      ? value.pinned_refs.map((ref) => ({
          id: isRecord(ref) && typeof ref.id === "string" ? ref.id : "",
          kind: isRecord(ref) && typeof ref.kind === "string" ? ref.kind : "",
          version: isRecord(ref) && typeof ref.version === "string" ? ref.version : ""
        }))
      : [],
    runId: requiredString(value.run_id, "run_id"),
    status: status as WorkflowRunStatus,
    traceId: requiredString(value.trace_id, "trace_id"),
    updatedAt: requiredString(value.updated_at, "updated_at"),
    workflowId: requiredString(value.workflow_id, "workflow_id"),
    workflowVersion: requiredString(value.workflow_version, "workflow_version")
  };
}

export function parseQueues(value: unknown): WorkflowQueueSummary[] {
  if (!Array.isArray(value)) throw new Error("operations.queues 响应无效");
  return value.map((item) => {
    if (!isRecord(item)) throw new Error("operations.queue 响应无效");
    return {
      depth: typeof item.depth === "number" ? item.depth : 0,
      name: requiredString(item.name, "name"),
      queueId: requiredString(item.queue_id, "queue_id"),
      workers: typeof item.workers === "number" ? item.workers : 0
    };
  });
}

export function parseWorkers(value: unknown): WorkflowWorkerSummary[] {
  if (!Array.isArray(value)) throw new Error("operations.workers 响应无效");
  return value.map((item) => {
    if (!isRecord(item)) throw new Error("operations.worker 响应无效");
    const status = item.status;
    if (status !== "running" && status !== "idle" && status !== "stopped") {
      throw new Error(`operations.worker status 无效: ${String(status)}`);
    }
    return {
      queues: Array.isArray(item.queues)
        ? item.queues.filter((q): q is string => typeof q === "string")
        : [],
      runningWorkflows:
        typeof item.running_workflows === "number" ? item.running_workflows : 0,
      startedAt: requiredString(item.started_at, "started_at"),
      status,
      workerId: requiredString(item.worker_id, "worker_id")
    };
  });
}

