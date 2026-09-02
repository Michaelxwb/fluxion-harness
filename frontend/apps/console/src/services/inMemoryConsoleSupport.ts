import type {
  AuditRecord,
  BindingRecord,
  ControlPlaneItem,
  CredentialMetadata,
  EvalRunSummary,
  EvalSetSummary,
  JsonRecord,
  PageData,
  PageRequest,
  PlatformUser,
  ResourceSummary,
  ResourceType,
  ResourceVersion,
  RunDetail,
  ValidationResult,
  WorkflowQueueSummary,
  WorkflowRunProjection,
  WorkflowValidationResultV2,
  WorkflowWorkerSummary
} from "../types/console";
import type { P1View } from "../types/navigation";

export interface ConsoleSeed {
  readonly tenantId: string;
  readonly actorId: string;
  readonly resources: readonly ResourceVersion[];
  readonly bindings: readonly BindingRecord[];
  readonly credentials: readonly CredentialMetadata[];
  readonly runs: readonly RunDetail[];
  readonly audit: readonly AuditRecord[];
  readonly capabilities?: readonly string[];
  readonly p1Views?: Partial<Record<P1View, readonly ControlPlaneItem[]>>;
  readonly p1ViewErrors?: readonly P1View[];
  readonly p1ViewPending?: readonly P1View[];
  readonly users?: readonly PlatformUser[];
  readonly evalSets?: readonly EvalSetSummary[];
  readonly evalRuns?: readonly EvalRunSummary[];
  readonly evalSetsError?: boolean;
  readonly evalRunsError?: boolean;
  readonly evalTriggerError?: string;
}

export function formatDiagnostic(
  diagnostic: WorkflowValidationResultV2["diagnostics"][number]
): string {
  return diagnostic.nodeId
    ? `${diagnostic.nodeId}.${diagnostic.field}: ${diagnostic.message}`
    : `${diagnostic.field}: ${diagnostic.message}`;
}

export async function validateAgentPublish(
  resource: ResourceVersion,
  listVisible: (kind: ResourceType) => Promise<readonly ResourceSummary[]>,
  latestResource: (kind: ResourceType, id: string) => ResourceVersion
): Promise<ValidationResult> {
  const issues: string[] = [];
  const capabilities = Array.isArray(resource.spec?.capabilities) ? resource.spec.capabilities : [];
  const declaredTools = new Set(
    capabilities
      .map((capability) => capability as JsonRecord)
      .filter((item) => String(item.type ?? "") === "tool")
      .map((item) => String(item.capability_ref ?? ""))
  );
  for (const capability of capabilities) {
    const item = capability as JsonRecord;
    const kind = String(item.type ?? "");
    const ref = String(item.capability_ref ?? "");
    const pin = String(item.version_pin ?? "");
    if (!ref || !["skill", "mcp"].includes(kind)) continue;
    const visible = await listVisible(kind as ResourceType);
    if (!visible.some((summary) => summary.resourceId === ref)) {
      issues.push(`能力引用 ${ref}@${pin} 不可解析（${kind} 资源不存在）`);
      continue;
    }
    if (kind !== "skill") continue;
    const required = latestResource("skill", ref).spec.required_capabilities;
    for (const need of Array.isArray(required) ? required : []) {
      if (!declaredTools.has(String(need))) {
        issues.push(`${ref} 需要能力 ${String(need)}，但 Agent 未声明`);
      }
    }
  }
  const policy = resource.spec?.model_policy as JsonRecord | undefined;
  const primary = policy?.primary_model_ref as JsonRecord | undefined;
  if (!primary) {
    issues.push("模型定义缺失（model_policy.primary_model_ref 必填）");
  } else {
    const models = await listVisible("model_definition");
    if (!models.some((summary) => summary.resourceId === String(primary.id))) {
      issues.push(
        `模型定义 ${String(primary.id)}@${String(primary.version)} 不存在` +
          "（model_policy.primary_model_ref 不可解析）"
      );
    }
  }
  return issues.length
    ? { valid: false, diagnostics: issues }
    : { valid: true, diagnostics: ["校验通过"] };
}

export const WORKFLOW_RUNS: readonly WorkflowRunProjection[] = [
  {
    runId: "weekly-report:exec-1001",
    workflowId: "weekly-report",
    workflowVersion: "v1",
    executionId: "exec-1001",
    traceId: "trace-1001",
    status: "succeeded",
    nodeStates: {
      collect: { status: "succeeded" },
      notify: { status: "succeeded" }
    },
    pinnedRefs: [{ id: "weekly-report", kind: "workflow", version: "v1" }],
    createdAt: "2026-08-28T08:00:00Z",
    updatedAt: "2026-08-28T08:02:00Z"
  },
  {
    runId: "weekly-report:exec-1002",
    workflowId: "weekly-report",
    workflowVersion: "v1",
    executionId: "exec-1002",
    traceId: "trace-1002",
    status: "running",
    nodeStates: {
      collect: { status: "succeeded" },
      review: { status: "running" }
    },
    pinnedRefs: [{ id: "weekly-report", kind: "workflow", version: "v1" }],
    createdAt: "2026-08-29T08:00:00Z",
    updatedAt: "2026-08-29T08:01:00Z"
  },
  {
    runId: "onboarding:exec-1003",
    workflowId: "onboarding",
    workflowVersion: "v2",
    executionId: "exec-1003",
    traceId: "trace-1003",
    status: "failed",
    nodeStates: {
      provision: { error: "provider unavailable", status: "failed" }
    },
    pinnedRefs: [{ id: "onboarding", kind: "workflow", version: "v2" }],
    createdAt: "2026-08-27T10:00:00Z",
    updatedAt: "2026-08-27T10:00:30Z"
  }
];

export const WORKFLOW_QUEUES: readonly WorkflowQueueSummary[] = [
  { depth: 3, name: "workflow 主队列", queueId: "workflow-main", workers: 2 },
  { depth: 0, name: "workflow 低优先级", queueId: "workflow-low", workers: 1 }
];

export const WORKFLOW_WORKERS: readonly WorkflowWorkerSummary[] = [
  {
    queues: ["workflow-main"],
    runningWorkflows: 1,
    startedAt: "2026-08-29T07:00:00Z",
    status: "running",
    workerId: "worker-0"
  },
  {
    queues: ["workflow-main", "workflow-low"],
    runningWorkflows: 0,
    startedAt: "2026-08-29T07:00:00Z",
    status: "idle",
    workerId: "worker-1"
  },
  {
    queues: [],
    runningWorkflows: 0,
    startedAt: "2026-08-28T07:00:00Z",
    status: "stopped",
    workerId: "worker-2"
  }
];

export function p1ViewTitle(view: P1View): string {
  const titles: Record<P1View, string> = {
    capabilities: "能力注册",
    plugin_policy: "插件钩子",
    users_channels: "用户管理"
  };
  return titles[view];
}

export function defaultConsoleSeed(): ConsoleSeed {
  return {
    actorId: "admin-001",
    audit: [],
    bindings: [],
    credentials: [
      {
        credentialRef: "secret://openai-prod",
        lastRotatedAt: "2026-08-20T08:00:00Z",
        provider: "openai",
        status: "active"
      }
    ],
    resources: [
      {
        resourceId: "runtime-profile-main",
        resourceType: "runtime_profile",
        spec: { display_name: "Main Runtime", model: "gpt-5", timeout_ms: 3000 },
        status: "published",
        tenantId: "tenant-a",
        updatedAt: "2026-08-23T08:00:00Z",
        version: "v1",
        visibility: "tenant"
      }
    ],
    runs: [],
    tenantId: "tenant-a"
  };
}

export function uniqueResourceKeys(resources: readonly ResourceVersion[]) {
  const seen = new Set<string>();
  return resources.flatMap((resource) => {
    const key = `${resource.resourceType}:${resource.resourceId}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ resourceId: resource.resourceId, resourceType: resource.resourceType }];
  });
}

export function toSummary(resource: ResourceVersion): ResourceSummary {
  const name = resource.spec.display_name;
  return {
    currentVersion: resource.version,
    displayName: typeof name === "string" ? name : resource.resourceId,
    resourceId: resource.resourceId,
    resourceType: resource.resourceType,
    status: resource.status,
    updatedAt: resource.updatedAt,
    visibility: resource.visibility
  };
}

export function page<T>(items: readonly T[], request: PageRequest): PageData<T> {
  const start = (request.page - 1) * request.pageSize;
  return {
    items: items.slice(start, start + request.pageSize),
    page: request.page,
    pageSize: request.pageSize,
    total: items.length
  };
}

export function compareVersionDesc(left: ResourceVersion, right: ResourceVersion): number {
  return versionNumber(right.version) - versionNumber(left.version);
}

function versionNumber(version: string): number {
  const match = /^v?(\d+)$/.exec(version);
  return match ? Number(match[1]) : 0;
}

export function nextVersion(resources: readonly ResourceVersion[]): string {
  const maxVersion = Math.max(0, ...resources.map((resource) => versionNumber(resource.version)));
  const prefix = resources.some((resource) => resource.version.startsWith("v")) ? "v" : "";
  return `${prefix}${maxVersion + 1}`;
}

export function sameVersion(left: ResourceVersion, right: ResourceVersion): boolean {
  return (
    left.resourceType === right.resourceType &&
    left.resourceId === right.resourceId &&
    left.tenantId === right.tenantId &&
    left.version === right.version
  );
}

export function nowIso(): string {
  return new Date().toISOString();
}

export function cloneResource(resource: ResourceVersion): ResourceVersion {
  return { ...resource, spec: cloneJson(resource.spec) };
}

export function cloneBinding(binding: BindingRecord): BindingRecord {
  return { ...binding };
}

export function cloneRun(run: RunDetail): RunDetail {
  return cloneJson(run);
}

export function cloneJson<T extends JsonRecord | RunDetail>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
