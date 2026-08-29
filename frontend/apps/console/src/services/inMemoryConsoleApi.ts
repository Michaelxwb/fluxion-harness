import type {
  AuditRecord,
  BindingInput,
  BindingRecord,
  ConsoleApi,
  ControlPlaneItem,
  CredentialMetadata,
  EvalRunSummary,
  EvalSetSummary,
  EvalTriggerInput,
  JsonRecord,
  IssuedChatAccess,
  JsonSchemaNode,
  PageData,
  PageRequest,
  PlatformUser,
  PublishResult,
  ResourceSummary,
  ResourceCreateInput,
  ResourceType,
  ResourceVersion,
  RollbackResult,
  ConsoleDataSource,
  RunDetail,
  ValidationResult,
  User360Summary,
  WorkflowDraftV2,
  WorkflowQueueSummary,
  WorkflowRunProjection,
  WorkflowSchemaV2,
  WorkflowValidationResultV2,
  WorkflowWorkerSummary
} from "../types/console";
import type { P1View } from "../types/navigation";
import { IN_MEMORY_RESOURCE_SCHEMAS } from "./inMemorySchemas";
import { validateWorkflowV2, WORKFLOW_V2_SCHEMA } from "./workflowV2";

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
  // ---- Phase 5 TASK-006：Eval 实页 seed ----
  readonly evalSets?: readonly EvalSetSummary[];
  readonly evalRuns?: readonly EvalRunSummary[];
  readonly evalSetsError?: boolean;
  readonly evalRunsError?: boolean;
  readonly evalTriggerError?: string;
}

export function createInMemoryConsoleApi(seed: ConsoleSeed = defaultConsoleSeed()): ConsoleApi {
  return new InMemoryConsoleApi(seed);
}

class InMemoryConsoleApi implements ConsoleApi {
  readonly dataSource: ConsoleDataSource = "in-memory";
  private readonly tenantId: string;
  private readonly actorId: string;
  private resources: ResourceVersion[];
  private bindings: BindingRecord[];
  private readonly credentials: CredentialMetadata[];
  private readonly runs: RunDetail[];
  private audit: AuditRecord[];
  private readonly capabilities: ReadonlySet<string>;
  private readonly p1Views: Partial<Record<P1View, readonly ControlPlaneItem[]>>;
  private readonly p1ViewErrors: ReadonlySet<P1View>;
  private readonly p1ViewPending: ReadonlySet<P1View>;
  private users: PlatformUser[];
  private readonly chatAccessIds = new Set<string>();
  // ---- Phase 5 TASK-006：Eval 实页状态 ----
  private evalSets: EvalSetSummary[];
  private evalRuns: EvalRunSummary[];
  private readonly evalSetsError: boolean;
  private readonly evalRunsError: boolean;
  private readonly evalTriggerError: string | null;

  constructor(seed: ConsoleSeed) {
    this.tenantId = seed.tenantId;
    this.actorId = seed.actorId;
    this.resources = seed.resources.map(cloneResource);
    this.bindings = seed.bindings.map(cloneBinding);
    this.credentials = seed.credentials.map((credential) => ({ ...credential }));
    this.runs = seed.runs.map(cloneRun);
    this.audit = seed.audit.map((record) => ({ ...record }));
    this.capabilities = new Set(seed.capabilities ?? []);
    this.p1Views = Object.fromEntries(
      Object.entries(seed.p1Views ?? {}).map(([view, items]) => [
        view,
        items?.map((item) => ({ ...item })) ?? []
      ])
    );
    this.p1ViewErrors = new Set(seed.p1ViewErrors ?? []);
    this.p1ViewPending = new Set(seed.p1ViewPending ?? []);
    this.users = (seed.users ?? []).map((user) => ({ ...user }));
    this.evalSets = (seed.evalSets ?? []).map((item) => ({ ...item }));
    this.evalRuns = (seed.evalRuns ?? []).map((item) => ({ ...item }));
    this.evalSetsError = seed.evalSetsError ?? false;
    this.evalRunsError = seed.evalRunsError ?? false;
    this.evalTriggerError = seed.evalTriggerError ?? null;
  }

  async listResources(resourceType?: ResourceType): Promise<PageData<ResourceSummary>> {
    const items = uniqueResourceKeys(this.resources)
      .map((key) => this.latestResource(key.resourceType, key.resourceId))
      .filter(
        (resource) =>
          (resourceType === undefined || resource.resourceType === resourceType) &&
          this.canSee(resource)
      )
      .map(toSummary)
      .sort((left, right) => left.resourceId.localeCompare(right.resourceId));
    // P2（review）：后端 page_size ≤ 100（console.py）；in-memory 对齐上限，防 >100 条时
    // 切 HTTP 后列表静默变少。
    return page(items, { page: 1, pageSize: Math.min(items.length || 20, 100) });
  }

  async getResourceSchema(resourceType: ResourceType): Promise<JsonSchemaNode> {
    // ADR-012：真相源是后端 schema endpoint；inMemory 用内嵌镜像（见 schemas 文件头注释）。
    const schema = IN_MEMORY_RESOURCE_SCHEMAS[resourceType];
    if (!schema) throw new Error(`unsupported resource type: ${resourceType}`);
    return schema;
  }

  async getResource(
    resourceType: ResourceType,
    resourceId: string,
    version?: string
  ): Promise<ResourceVersion> {
    const resource = version
      ? this.findVersion(resourceType, resourceId, version)
      : this.latestResource(resourceType, resourceId);
    if (!this.canSee(resource)) {
      throw new Error("resource is not visible in current tenant");
    }
    return cloneResource(resource);
  }

  async createResource(input: ResourceCreateInput): Promise<ResourceVersion> {
    if (this.resources.some((resource) => sameVersion(resource, { ...input, status: "draft", tenantId: this.tenantId, updatedAt: "" }))) {
      throw new Error("resource version already exists");
    }
    const resource: ResourceVersion = {
      ...input,
      spec: cloneJson(input.spec),
      status: "draft",
      tenantId: this.tenantId,
      updatedAt: nowIso()
    };
    this.resources = [...this.resources, resource];
    return cloneResource(resource);
  }

  async createDraftFromLatest(resourceType: ResourceType, resourceId: string): Promise<ResourceVersion> {
    const latest = this.latestResource(resourceType, resourceId);
    if (!this.canSee(latest)) {
      throw new Error("resource is not visible in current tenant");
    }
    if (latest.status === "draft") {
      return cloneResource(latest);
    }
    const draft = cloneResource({
      ...latest,
      status: "draft",
      version: nextVersion(this.versionsFor(resourceType, resourceId)),
      updatedAt: nowIso()
    });
    this.resources = [...this.resources, draft];
    return cloneResource(draft);
  }

  async updateDraft(resource: ResourceVersion, spec: JsonRecord): Promise<ResourceVersion> {
    if (resource.status !== "draft") {
      throw new Error("已发布版本不可直接修改，请创建新的 Draft Version");
    }
    const updated = cloneResource({ ...resource, spec: cloneJson(spec), updatedAt: nowIso() });
    this.resources = this.resources.map((candidate) =>
      sameVersion(candidate, updated) ? updated : candidate
    );
    return cloneResource(updated);
  }

  async validateDraft(resource: ResourceVersion): Promise<ValidationResult> {
    const current = this.findVersion(resource.resourceType, resource.resourceId, resource.version);
    if (current.resourceType === "workflow") {
      // TASK-002：V2 九节点判别联合校验（V1 legacy spec 兼容注入 capability）。
      const result = validateWorkflowV2(
        current.spec as unknown as WorkflowDraftV2,
        this.capabilities
      );
      return {
        diagnostics: result.diagnostics.map(formatDiagnostic),
        valid: result.valid
      };
    }
    // 对齐后端 validate_resource_version：非 workflow 类型走各 kind 的 pydantic 模型
    // 校验（model_validate），并不统一要求 model/timeout_ms。此前 in-memory 对所有
    // 非 workflow 类型硬查 model + timeout_ms，使 skill/mcp/policy 等不含这两个字段
    // 的 spec 恒返回 invalid，与真实 HTTP 后端（valid）分叉。
    return { valid: true, diagnostics: ["校验通过"] };
  }

  async publishVersion(resource: ResourceVersion): Promise<PublishResult> {
    const current = this.findVersion(resource.resourceType, resource.resourceId, resource.version);
    if (current.status !== "draft") {
      throw new Error("version conflict");
    }
    if (current.resourceType === "workflow") {
      const validation = await this.validateDraft(current);
      if (!validation.valid) {
        throw new Error(validation.diagnostics.join("；"));
      }
    }
    const published = cloneResource({ ...current, status: "published", updatedAt: nowIso() });
    this.resources = this.resources.map((candidate) =>
      sameVersion(candidate, published) ? published : candidate
    );
    this.recordAudit("publish", published.resourceId, published.version);
    return {
      eventStatus: "published",
      kubernetesWorkloadCreated: false,
      resourceId: published.resourceId,
      status: "published",
      version: published.version
    };
  }

  async rollbackVersion(resource: ResourceVersion, targetVersion: string): Promise<RollbackResult> {
    const target = this.findVersion(resource.resourceType, resource.resourceId, targetVersion);
    const newVersion = nextVersion(this.versionsFor(resource.resourceType, resource.resourceId));
    const rollback = cloneResource({ ...target, status: "published", updatedAt: nowIso(), version: newVersion });
    this.resources = [...this.resources, rollback];
    this.recordAudit("rollback", rollback.resourceId, targetVersion);
    return { newVersion, resourceId: rollback.resourceId, status: "published", targetVersion };
  }

  async listVersions(
    resourceType: ResourceType,
    resourceId: string,
    request: PageRequest
  ): Promise<PageData<ResourceVersion>> {
    const items = this.versionsFor(resourceType, resourceId).sort(compareVersionDesc);
    return page(items.map(cloneResource), request);
  }

  async listVisibleResources(resourceType: ResourceType): Promise<readonly ResourceSummary[]> {
    const pageData = await this.listResources(resourceType);
    return pageData.items;
  }

  async listBindings(
    request: PageRequest,
    resourceType?: ResourceType
  ): Promise<PageData<BindingRecord>> {
    const items =
      resourceType === undefined
        ? this.bindings
        : this.bindings.filter((binding) => binding.resourceType === resourceType);
    return page(items.map(cloneBinding), request);
  }

  async saveBinding(input: BindingInput): Promise<BindingRecord> {
    const target = this.latestResource(input.resourceType, input.resourceId);
    if (!this.canSee(target)) {
      throw new Error("resource is not visible in current tenant");
    }
    if (input.credentialRef && !this.credentials.some((item) => item.credentialRef === input.credentialRef)) {
      throw new Error("credential ref is not visible in current tenant");
    }
    const record = this.createBindingRecord(input);
    this.bindings = [record, ...this.bindings];
    this.recordAudit("binding.update", record.resourceId, record.versionSelector);
    return cloneBinding(record);
  }

  async listCredentials(): Promise<readonly CredentialMetadata[]> {
    return this.credentials.map((credential) => ({ ...credential }));
  }

  async listRuns(): Promise<readonly RunDetail[]> {
    return this.runs.map(cloneRun);
  }

  async listAudit(request: PageRequest): Promise<PageData<AuditRecord>> {
    return page(this.audit.map((record) => ({ ...record })), request);
  }

  async listP1View(view: P1View): Promise<readonly ControlPlaneItem[]> {
    if (this.p1ViewPending.has(view)) {
      return await new Promise<readonly ControlPlaneItem[]>(() => undefined);
    }
    if (this.p1ViewErrors.has(view)) {
      throw new Error(`${p1ViewTitle(view)} 加载失败`);
    }
    return (this.p1Views[view] ?? []).map((item) => ({ ...item }));
  }

  async listPlatformUsers(request: PageRequest): Promise<PageData<PlatformUser>> {
    return page(this.users.map((user) => ({ ...user })), request);
  }

  async createPlatformUser(platformUserId: string, displayName: string): Promise<PlatformUser> {
    if (this.users.some((user) => user.platformUserId === platformUserId)) {
      throw new Error("platform user already exists");
    }
    const user = { createdAt: nowIso(), displayName, platformUserId };
    this.users = [...this.users, user];
    return { ...user };
  }

  async issueChatAccess(
    platformUserId: string,
    agentId: string
  ): Promise<IssuedChatAccess> {
    const accessId = `chat-access-${this.chatAccessIds.size + 1}`;
    const token = `test-token-${this.chatAccessIds.size + 1}`;
    this.chatAccessIds.add(accessId);
    return {
      accessId,
      chatPath: `/chat/#/${token}`,
      createdAt: nowIso(),
      platformUserId,
      agentId,
      token
    };
  }

  async testRunAgent(
    agentId: string,
    input: { input: string },
    onEvent: (event: { event: string; data: unknown }) => void
  ): Promise<void> {
    if (agentId.startsWith("fail")) {
      onEvent({ event: "error", data: { message: "provider unavailable" } });
      return;
    }
    onEvent({ event: "token", data: { text: "你好" } });
    onEvent({ event: "token", data: { text: "！" } });
    onEvent({ event: "completed", data: { output: "你好！" } });
  }

  async getUser360(platformUserId: string): Promise<User360Summary> {
    const user = this.users.find((u) => u.platformUserId === platformUserId);
    if (!user) throw new Error(`user_not_found: ${platformUserId}`);
    const activity = this.audit.filter((a) => a.resourceId === platformUserId);
    return {
      identity: {
        platform_user_id: user.platformUserId,
        display_name: user.displayName,
        channels: []
      },
      profile: null,
      preferences: null,
      capabilities: [],
      policy: [],
      activity_count: activity.length
    };
  }

  async revokeChatAccess(accessId: string): Promise<void> {
    if (!this.chatAccessIds.delete(accessId)) throw new Error("chat access not found");
  }

  // ---- Phase 5 TASK-006：Eval 实页契约（in-memory 先行，http 同契约）----

  async listEvalSets(): Promise<readonly EvalSetSummary[]> {
    if (this.evalSetsError) throw new Error("评测集加载失败");
    return this.evalSets.map((item) => ({ ...item }));
  }

  async listEvalRuns(): Promise<readonly EvalRunSummary[]> {
    if (this.evalRunsError) throw new Error("评测运行加载失败");
    return this.evalRuns.map((item) => ({ ...item }));
  }

  async triggerEvalRun(input: EvalTriggerInput): Promise<EvalRunSummary> {
    if (this.evalTriggerError !== null) {
      // 模拟 HTTP envelope 失败路径（如 Release Gate 阻断，message 原样呈现）
      throw new Error(this.evalTriggerError);
    }
    const run: EvalRunSummary = {
      runId: `run-${input.evalSetId}-${this.evalRuns.length + 1}`,
      evalSetId: input.evalSetId,
      evalSetVersion: input.evalSetVersion,
      score: 1,
      passed: true,
      traceId: input.traceId,
      createdAt: new Date().toISOString()
    };
    this.evalRuns = [...this.evalRuns, run];
    return { ...run };
  }

  // ---- TASK-002 workflow V2 契约（in-memory 先行，⛳依赖缺口同契约切 HTTP） ----

  async getWorkflowSchema(): Promise<WorkflowSchemaV2> {
    return WORKFLOW_V2_SCHEMA;
  }

  async validateWorkflow(draft: WorkflowDraftV2): Promise<WorkflowValidationResultV2> {
    return validateWorkflowV2(draft, this.capabilities);
  }

  async listWorkflowRuns(workflowId?: string): Promise<readonly WorkflowRunProjection[]> {
    return WORKFLOW_RUNS.filter((run) => workflowId === undefined || run.workflowId === workflowId);
  }

  async listQueues(): Promise<readonly WorkflowQueueSummary[]> {
    return WORKFLOW_QUEUES.map((queue) => ({ ...queue }));
  }

  async listWorkers(): Promise<readonly WorkflowWorkerSummary[]> {
    return WORKFLOW_WORKERS.map((worker) => ({ ...worker }));
  }

  private createBindingRecord(input: BindingInput): BindingRecord {
    return {
      bindingId: `bind-${this.bindings.length + 1}`,
      credentialRef: input.credentialRef,
      enabled: true,
      resourceId: input.resourceId,
      resourceType: input.resourceType,
      subjectId: input.subjectId,
      subjectType: input.subjectType,
      tenantId: this.tenantId,
      versionSelector: input.versionSelector
    };
  }

  private latestResource(resourceType: ResourceType, resourceId: string): ResourceVersion {
    const latest = this.versionsFor(resourceType, resourceId).sort(compareVersionDesc)[0];
    if (!latest) {
      throw new Error("resource not found");
    }
    return latest;
  }

  private findVersion(resourceType: ResourceType, resourceId: string, version: string): ResourceVersion {
    const found = this.resources.find(
      (resource) =>
        resource.resourceType === resourceType &&
        resource.resourceId === resourceId &&
        resource.version === version
    );
    if (!found) {
      throw new Error("resource version not found");
    }
    return found;
  }

  private versionsFor(resourceType: ResourceType, resourceId: string): ResourceVersion[] {
    return this.resources.filter(
      (resource) => resource.resourceType === resourceType && resource.resourceId === resourceId
    );
  }

  private canSee(resource: ResourceVersion): boolean {
    return resource.tenantId === this.tenantId || resource.visibility === "system" || resource.visibility === "public";
  }

  private recordAudit(action: string, resourceId: string, resourceVersion: string): void {
    this.audit = [
      {
        action,
        actorId: this.actorId,
        at: nowIso(),
        id: `audit-${this.audit.length + 1}`,
        resourceId,
        resourceVersion
      },
      ...this.audit
    ];
  }
}

function formatDiagnostic(
  diagnostic: WorkflowValidationResultV2["diagnostics"][number]
): string {
  return diagnostic.nodeId
    ? `${diagnostic.nodeId}.${diagnostic.field}: ${diagnostic.message}`
    : `${diagnostic.field}: ${diagnostic.message}`;
}

// TASK-002：workflow_run 投影 / 队列 / Worker 运营视图（Phase 3 契约对齐，in-memory 种子）。
const WORKFLOW_RUNS: readonly WorkflowRunProjection[] = [
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

const WORKFLOW_QUEUES: readonly WorkflowQueueSummary[] = [
  { depth: 3, name: "workflow 主队列", queueId: "workflow-main", workers: 2 },
  { depth: 0, name: "workflow 低优先级", queueId: "workflow-low", workers: 1 }
];

const WORKFLOW_WORKERS: readonly WorkflowWorkerSummary[] = [
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

function p1ViewTitle(view: P1View): string {
  const titles: Record<P1View, string> = {
    capabilities: "能力注册",
    eval: "能力评测",
    plugin_policy: "插件钩子",
    runtime_status: "运行时态",
    users_channels: "用户管理"
  };
  return titles[view];
}

function defaultConsoleSeed(): ConsoleSeed {
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

function uniqueResourceKeys(resources: readonly ResourceVersion[]) {
  const seen = new Set<string>();
  return resources.flatMap((resource) => {
    const key = `${resource.resourceType}:${resource.resourceId}`;
    if (seen.has(key)) {
      return [];
    }
    seen.add(key);
    return [{ resourceId: resource.resourceId, resourceType: resource.resourceType }];
  });
}

function toSummary(resource: ResourceVersion): ResourceSummary {
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

function page<T>(items: readonly T[], request: PageRequest): PageData<T> {
  const start = (request.page - 1) * request.pageSize;
  return {
    items: items.slice(start, start + request.pageSize),
    page: request.page,
    pageSize: request.pageSize,
    total: items.length
  };
}

function compareVersionDesc(left: ResourceVersion, right: ResourceVersion): number {
  return versionNumber(right.version) - versionNumber(left.version);
}

function versionNumber(version: string): number {
  const match = /^v(\d+)$/.exec(version);
  return match ? Number(match[1]) : 0;
}

function nextVersion(resources: readonly ResourceVersion[]): string {
  const maxVersion = Math.max(0, ...resources.map((resource) => versionNumber(resource.version)));
  return `v${maxVersion + 1}`;
}

function sameVersion(left: ResourceVersion, right: ResourceVersion): boolean {
  return (
    left.resourceType === right.resourceType &&
    left.resourceId === right.resourceId &&
    left.tenantId === right.tenantId &&
    left.version === right.version
  );
}

function nowIso(): string {
  return new Date().toISOString();
}

function cloneResource(resource: ResourceVersion): ResourceVersion {
  return { ...resource, spec: cloneJson(resource.spec) };
}

function cloneBinding(binding: BindingRecord): BindingRecord {
  return { ...binding };
}

function cloneRun(run: RunDetail): RunDetail {
  return cloneJson(run);
}

function cloneJson<T extends JsonRecord | RunDetail>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
