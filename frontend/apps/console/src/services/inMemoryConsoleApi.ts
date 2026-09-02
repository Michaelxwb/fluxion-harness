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
import {
  WORKFLOW_QUEUES,
  WORKFLOW_RUNS,
  WORKFLOW_WORKERS,
  cloneBinding,
  cloneJson,
  cloneResource,
  cloneRun,
  compareVersionDesc,
  defaultConsoleSeed,
  formatDiagnostic,
  nextVersion,
  nowIso,
  p1ViewTitle,
  page,
  sameVersion,
  toSummary,
  uniqueResourceKeys,
  validateAgentPublish,
  type ConsoleSeed
} from "./inMemoryConsoleSupport";
import { IN_MEMORY_RESOURCE_SCHEMAS } from "./inMemorySchemas";
import { validateWorkflowV2, WORKFLOW_V2_SCHEMA } from "./workflowV2";

export type { ConsoleSeed } from "./inMemoryConsoleSupport";

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

  async validatePublish(resource: ResourceVersion): Promise<ValidationResult> {
    // TASK-009 对齐（返工）：与后端 console_resources._agent_reference_issues 同源——
    // skill/mcp 引用可解析 + model_policy.primary_model_ref 存在 + Skill 依赖闭包
    // （required_capabilities 须由 tool 类型声明覆盖，同名 Skill 不可顶替）。
    // tool 引用不查资源：builtin/runtime 工具非版本化资源，与后端语义一致。
    if (resource.resourceType === "agent_definition") {
      return validateAgentPublish(
        resource,
        (kind) => this.listVisibleResources(kind),
        (kind, id) => this.latestResource(kind, id)
      );
    }
    return this.validateDraft(resource);
  }

  async publishVersion(resource: ResourceVersion): Promise<PublishResult> {
    const current = this.findVersion(resource.resourceType, resource.resourceId, resource.version);
    if (current.status !== "draft") {
      throw new Error("version conflict");
    }
    // 与后端发布链同源（RULE-04/S-04）：发布完整校验 fail-closed，失败不产生
    // published 版本——in-memory 与真实 HTTP 后端行为一致，避免测试误绿。
    const validation = await this.validatePublish(current);
    if (!validation.valid) {
      throw new Error(validation.diagnostics.join("；"));
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
