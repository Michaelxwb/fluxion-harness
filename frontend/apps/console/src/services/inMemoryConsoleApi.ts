import type {
  AgentWebChannel,
  ChannelVerifyResult,
  UserChatAccess,
  WebChannelEntry,
  AuthorizedUserSummary,
  McpConnectionTestResult,
  ModelLabProjection,
  ProjectionCredential,
  ProjectionModel,
  ProjectionProvider,
  ToolCallTestResult,
  AuditFilters,
  AuditRecord,
  BindingInput,
  BindingRecord,
  ConsoleApi,
  ControlPlaneItem,
  CredentialCreateInput,
  CredentialMetadata,
  EvalRunSummary,
  EvalSetSummary,
  EvalTriggerInput,
  JsonRecord,
  ModelConnectionTestResult,
  IssuedChatAccess,
  JsonSchemaNode,
  PageData,
  PageRequest,
  PlatformUser,
  CredentialProjection,
  CredentialProjectionPage,
  PublishOptions,
  PublishResult,
  ResourceListPage,
  ResourceSummary,
  RunListPage,
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
  private credentials: CredentialMetadata[];
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

  async listResources(
    resourceType?: ResourceType,
    request: ResourceListPage = { page: 1, pageSize: 100 }
  ): Promise<PageData<ResourceSummary>> {
    const keyword = request.keyword?.trim().toLowerCase() ?? "";
    const items = uniqueResourceKeys(this.resources)
      .map((key) => this.latestResource(key.resourceType, key.resourceId))
      .filter(
        (resource) =>
          (resourceType === undefined || resource.resourceType === resourceType) &&
          this.canSee(resource)
      )
      .map(toSummary)
      .filter(
        (item) =>
          (!request.status || item.status === request.status) &&
          (!keyword ||
            item.displayName.toLowerCase().includes(keyword) ||
            item.resourceId.toLowerCase().includes(keyword))
      )
      .sort((left, right) => left.resourceId.localeCompare(right.resourceId));
    return page(items, { page: request.page, pageSize: request.pageSize });
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
    // 与服务端一致：不传 version 时默认 "1"。
    const versioned = { version: "1", ...input };
    if (this.resources.some((resource) => sameVersion(resource, { ...versioned, status: "draft", tenantId: this.tenantId, updatedAt: "" }))) {
      throw new Error("resource version already exists");
    }
    const resource: ResourceVersion = {
      ...versioned,
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
    // 与后端 console_resources.update_draft 同语义：按存储态判定可变性——
    // 陈旧客户端拿着发布前的 draft 对象直接写已发布版本必须拒绝（此前只看
    // 传入对象自带的 status，脏写会把 published 翻回 draft，掩盖 S-06 类缺陷）。
    const stored = this.findVersion(resource.resourceType, resource.resourceId, resource.version);
    if (stored.status !== "draft") {
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

  async publishVersion(resource: ResourceVersion, options: PublishOptions = {}): Promise<PublishResult> {
    const current = this.findVersion(resource.resourceType, resource.resourceId, resource.version);
    if (current.status !== "draft") {
      throw new Error("version conflict");
    }
    // ADR-A011：乐观并发检查——expectedBaseVersion 与当前 published base 不符 → 冲突
    // （与后端 _check_expected_base 同语义；缺省 None 跳过检查，保持既有行为）。
    if (options.expectedBaseVersion) {
      const publishedBase = this.versionsFor(resource.resourceType, resource.resourceId)
        .filter((v) => v.status === "published")
        .map((v) => v.version)
        .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }))[0];
      if (publishedBase !== undefined && publishedBase !== options.expectedBaseVersion) {
        throw new Error("version conflict");
      }
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

  async deprecateVersion(resource: ResourceVersion, reason?: string): Promise<PublishResult> {
    void reason;
    const current = this.findVersion(resource.resourceType, resource.resourceId, resource.version);
    if (current.status !== "published") throw new Error("only published versions can be deprecated");
    const deprecated = cloneResource({ ...current, status: "deprecated", updatedAt: nowIso() });
    this.resources = this.resources.map((candidate) =>
      sameVersion(candidate, deprecated) ? deprecated : candidate
    );
    this.recordAudit("deprecate", deprecated.resourceId, deprecated.version);
    return {
      eventStatus: "published",
      kubernetesWorkloadCreated: false,
      resourceId: deprecated.resourceId,
      status: "deprecated",
      version: deprecated.version
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

  async listCredentials(request: PageRequest = { page: 1, pageSize: 100 }): Promise<readonly CredentialMetadata[]> {
    const start = (request.page - 1) * request.pageSize;
    return this.credentials.slice(start, start + request.pageSize).map((credential) => ({ ...credential }));
  }

  async listCredentialProjection(
    request: CredentialProjectionPage = { page: 1, pageSize: 20 }
  ): Promise<PageData<CredentialProjection>> {
    // FEAT-04：与 HTTP 投影同语义（当前 SECRET 行 + Provider 配置引用关联）。
    const keyword = request.keyword?.trim().toLowerCase() ?? "";
    const secrets = uniqueResourceKeys(this.resources)
      .map((key) => this.latestResource(key.resourceType, key.resourceId))
      .filter((resource) => resource.resourceType === "secret" && this.canSee(resource))
      .filter((resource) => {
        const spec = resource.spec as Record<string, unknown>;
        if (request.status !== undefined && resource.status !== request.status) return false;
        if (request.purpose !== undefined && String(spec.purpose ?? "") !== request.purpose) return false;
        if (request.revoked !== undefined && (spec.revoked === true) !== request.revoked) return false;
        return (
          !keyword ||
          String(spec.name ?? "").toLowerCase().includes(keyword) ||
          resource.resourceId.toLowerCase().includes(keyword)
        );
      })
      .sort((left, right) => left.resourceId.localeCompare(right.resourceId));
    const providers = uniqueResourceKeys(this.resources)
      .map((key) => this.latestResource(key.resourceType, key.resourceId))
      .filter((resource) => resource.resourceType === "model_provider" && this.canSee(resource));
    const rows: CredentialProjection[] = secrets.map((resource) => {
      const spec = resource.spec as Record<string, unknown>;
      const secretRef = String(spec.secret_ref ?? "");
      const consumers = providers
        .filter((provider) => String((provider.spec as Record<string, unknown>).credential_ref ?? "") === secretRef)
        .map((provider) => {
          const providerSpec = provider.spec as Record<string, unknown>;
          return {
            providerId: provider.resourceId,
            providerName: String(providerSpec.display_name ?? provider.resourceId)
          };
        })
        .sort((left, right) => left.providerId.localeCompare(right.providerId));
      const seen = new Map(consumers.map((consumer) => [consumer.providerId, consumer]));
      const deduped = [...seen.values()];
      return {
        credentialId: resource.resourceId,
        displayName: String(spec.name ?? resource.resourceId),
        secretRef,
        purpose: String(spec.purpose ?? ""),
        revoked: spec.revoked === true,
        updatedAt: resource.updatedAt,
        consumerCount: deduped.length,
        consumers: deduped,
        status: resource.status,
        version: resource.version
      };
    });
    return page(rows, { page: request.page, pageSize: request.pageSize });
  }

  async createCredential(input: CredentialCreateInput): Promise<ResourceVersion> {
    // TASK-009：in-memory 明文只写（不回显明文）；与真实 HTTP 后端同语义。
    // 同时落 resources（服务端生成 id/version），保证列表/详情链路同 HTTP 一致。
    const resourceId = `cred_${input.name}`;
    const credentialRef = `secret://${this.tenantId}/${resourceId}@1`;
    this.credentials.push({
      credentialRef,
      provider: input.name,
      status: "active",
      lastRotatedAt: nowIso()
    });
    const resource: ResourceVersion = {
      resourceType: "secret",
      resourceId,
      tenantId: this.tenantId,
      version: "1",
      status: "draft",
      visibility: "private",
      spec: { name: input.name, secret_ref: credentialRef, purpose: input.purpose ?? "" },
      updatedAt: nowIso()
    };
    this.resources = [...this.resources, resource];
    return cloneResource(resource);
  }

  async rotateCredential(resourceId: string, secret: string): Promise<ResourceVersion> {
    // TASK-009 行操作·轮换：新版本 SecretRef + working draft 落档（明文只写）。
    void secret; // in-memory 不存明文（同 HTTP 语义：明文不回显、不落资源）
    const working = await this.createDraftFromLatest("secret", resourceId);
    const previousRef = String(working.spec.secret_ref ?? "");
    const rotatedRef = previousRef.replace(/@\d+$/, "") + "@2";
    this.credentials = this.credentials.map((credential) =>
      credential.credentialRef === previousRef
        ? { ...credential, credentialRef: rotatedRef, lastRotatedAt: nowIso() }
        : credential
    );
    const updated = await this.updateDraft(working, {
      ...working.spec,
      secret_ref: rotatedRef
    });
    return updated;
  }

  async disableCredential(resourceId: string): Promise<ResourceVersion> {
    // TASK-009 行操作·禁用：spec 标记 revoked；resolve fail-closed 由真实 store 承担。
    const working = await this.createDraftFromLatest("secret", resourceId);
    this.credentials = this.credentials.map((credential) =>
      credential.credentialRef === String(working.spec.secret_ref ?? "")
        ? { ...credential, status: "disabled" }
        : credential
    );
    return this.updateDraft(working, { ...working.spec, revoked: true });
  }

  async createModelProvider(spec: JsonRecord): Promise<ResourceVersion> {
    // TASK-010：studio 产品端点同契约——服务端生成 id/version。
    return this.createResource({
      resourceId: `model-provider_${nowIso().slice(11, 19).replace(/:/g, "")}`,
      resourceType: "model_provider",
      spec,
      version: "1",
      visibility: "private"
    });
  }

  async createModelDefinition(spec: JsonRecord): Promise<ResourceVersion> {
    return this.createResource({
      resourceId: `model-definition_${nowIso().slice(11, 19).replace(/:/g, "")}`,
      resourceType: "model_definition",
      spec,
      version: "1",
      visibility: "private"
    });
  }

  async testModelProviderConnection(providerId: string): Promise<ModelConnectionTestResult> {
    // TASK-010：in-memory 与真实 HTTP 同语义——provider 存在即可达，
    // discovered_models 镜像本地 stub（deepseek-chat/reasoner）。
    const exists = this.resources.some(
      (resource) =>
        resource.resourceType === "model_provider" && resource.resourceId === providerId
    );
    if (!exists) {
      return { reachable: false, discoveredModels: [], error: `Provider ${providerId} 不存在` };
    }
    return {
      reachable: true,
      discoveredModels: ["deepseek-chat", "deepseek-reasoner"],
      error: null
    };
  }

  async createAgent(spec: JsonRecord): Promise<ResourceVersion> {
    const sequence = this.resources.filter(
      (resource) => resource.resourceType === "agent_definition"
    ).length + 1;
    return this.createResource({
      resourceId: `agent_${String(sequence).padStart(4, "0")}`,
      resourceType: "agent_definition",
      spec,
      version: "1",
      visibility: "private"
    });
  }

  async createWorkflow(spec: JsonRecord): Promise<ResourceVersion> {
    const sequence = this.resources.filter(
      (resource) => resource.resourceType === "workflow"
    ).length + 1;
    return this.createResource({
      resourceId: `workflow_${String(sequence).padStart(4, "0")}`,
      resourceType: "workflow",
      spec,
      version: "1",
      visibility: "private"
    });
  }

  async createSkill(spec: JsonRecord): Promise<ResourceVersion> {
    const sequence = this.resources.filter(
      (resource) => resource.resourceType === "skill"
    ).length + 1;
    return this.createResource({
      resourceId: `skill_${String(sequence).padStart(4, "0")}`,
      resourceType: "skill",
      spec,
      version: "1",
      visibility: "private"
    });
  }

  async createTool(spec: JsonRecord): Promise<ResourceVersion> {
    const sequence = this.resources.filter(
      (resource) => resource.resourceType === "tool"
    ).length + 1;
    return this.createResource({
      resourceId: `tool_${String(sequence).padStart(4, "0")}`,
      resourceType: "tool",
      spec,
      version: "1",
      visibility: "private"
    });
  }

  async createMcpServer(spec: JsonRecord): Promise<ResourceVersion> {
    const sequence = this.resources.filter(
      (resource) => resource.resourceType === "mcp"
    ).length + 1;
    return this.createResource({
      resourceId: `mcp_${String(sequence).padStart(4, "0")}`,
      resourceType: "mcp",
      spec,
      version: "1",
      visibility: "private"
    });
  }

  async getModelLabProjection(): Promise<ModelLabProjection> {
    const current = new Map<string, ResourceVersion>();
    for (const resource of this.resources) {
      const existing = current.get(`${resource.resourceType}:${resource.resourceId}`);
      if (!existing || resource.version > existing.version) {
        current.set(`${resource.resourceType}:${resource.resourceId}`, resource);
      }
    }
    const providers: ProjectionProvider[] = [];
    const models: ProjectionModel[] = [];
    const credentials: ProjectionCredential[] = [];
    for (const resource of current.values()) {
      if (resource.resourceType === "model_provider") {
        const spec = resource.spec as { base_url?: string; credential_ref?: string };
        providers.push({
          resourceId: resource.resourceId,
          displayName: (resource.spec.name as string) ?? resource.resourceId,
          version: resource.version,
          status: resource.status,
          baseUrl: spec.base_url ?? "-",
          credentialRef: spec.credential_ref ?? ""
        });
      } else if (resource.resourceType === "model_definition") {
        const spec = resource.spec as { name?: string; provider_ref?: { id?: string } };
        models.push({
          resourceId: resource.resourceId,
          name: spec.name ?? resource.resourceId,
          version: resource.version,
          providerId: spec.provider_ref?.id ?? ""
        });
      } else if (resource.resourceType === "secret") {
        const spec = resource.spec as { name?: string; secret_ref?: string };
        credentials.push({
          label: spec.name ?? resource.resourceId,
          value: spec.secret_ref ?? ""
        });
      }
    }
    return { providers, models, credentials };
  }

  async createPolicy(spec: JsonRecord): Promise<ResourceVersion> {
    const sequence = this.resources.filter(
      (resource) => resource.resourceType === "policy"
    ).length + 1;
    return this.createResource({
      resourceId: `policy_${String(sequence).padStart(4, "0")}`,
      resourceType: "policy",
      spec,
      version: "1",
      visibility: "private"
    });
  }

  async testMcpConnection(mcpId: string): Promise<McpConnectionTestResult> {
    const resource = this.resources.find(
      (item) => item.resourceType === "mcp" && item.resourceId === mcpId
    );
    if (!resource) throw new Error("mcp not found");
    return { reachable: true, discoveredTools: ["mcp__weather__lookup"], error: null };
  }

  async testToolCall(toolId: string): Promise<ToolCallTestResult> {
    const resource = this.resources.find(
      (item) => item.resourceType === "tool" && item.resourceId === toolId
    );
    if (!resource) throw new Error("tool not found");
    const spec = resource.spec as { url?: string };
    if (!spec.url) {
      return { reachable: false, statusCode: null, bodyExcerpt: null, error: "platform_service 类型暂不支持 Test Call" };
    }
    return { reachable: true, statusCode: 200, bodyExcerpt: "{}", error: null };
  }

  async listRuns(request: RunListPage = { page: 1, pageSize: 100 }): Promise<PageData<RunDetail>> {
    const keyword = request.keyword?.trim().toLowerCase() ?? "";
    const filtered = this.runs.filter(
      (run) =>
        (!request.status || run.status === request.status) &&
        (!keyword ||
          run.executionId.toLowerCase().includes(keyword) ||
          (run.traceId ?? "").toLowerCase().includes(keyword))
    );
    return page(filtered.map(cloneRun), { page: request.page, pageSize: request.pageSize });
  }

  async listAudit(request: PageRequest, filters?: AuditFilters): Promise<PageData<AuditRecord>> {
    const filtered = this.audit.filter((record) => {
      if (filters?.action && record.action !== filters.action) return false;
      if (filters?.actorId && record.actorId !== filters.actorId) return false;
      if (filters?.targetType && record.targetType !== filters.targetType) return false;
      if (filters?.createdFrom && record.at < filters.createdFrom) return false;
      if (filters?.createdTo && record.at > filters.createdTo) return false;
      return true;
    });
    return page(filtered.map((record) => ({ ...record })), request);
  }

  async listP1View(view: P1View, request: PageRequest = { page: 1, pageSize: 100 }): Promise<readonly ControlPlaneItem[]> {
    if (this.p1ViewPending.has(view)) {
      return await new Promise<readonly ControlPlaneItem[]>(() => undefined);
    }
    if (this.p1ViewErrors.has(view)) {
      throw new Error(`${p1ViewTitle(view)} 加载失败`);
    }
    const items = this.p1Views[view] ?? [];
    const start = (request.page - 1) * request.pageSize;
    return items.slice(start, start + request.pageSize).map((item) => ({ ...item }));
  }

  async listPlatformUsers(request: PageRequest & { keyword?: string }): Promise<PageData<PlatformUser>> {
    const keyword = request.keyword?.trim().toLowerCase() ?? "";
    const filtered = this.users.filter(
      (user) =>
        !keyword ||
        user.platformUserId.toLowerCase().includes(keyword) ||
        user.displayName.toLowerCase().includes(keyword)
    );
    return page(filtered.map((user) => ({ ...user })), request);
  }

  async createPlatformUser(platformUserId: string, displayName: string): Promise<PlatformUser> {
    if (this.users.some((user) => user.platformUserId === platformUserId)) {
      throw new Error("platform user already exists");
    }
    const user = { createdAt: nowIso(), displayName, platformUserId };
    this.users = [...this.users, user];
    return { ...user };
  }

  // ---- TASK-013（§9.1）：Agent 用户授权（jsdom 测试与 http 同契约）----
  private authorizedUsers: AuthorizedUserSummary[] = [];

  async listAuthorizedUsers(agentId: string): Promise<readonly AuthorizedUserSummary[]> {
    return this.authorizedUsers
      .filter((row) => row.bindingId.startsWith(`${agentId}:`) && row.enabled)
      .map((row) => ({ ...row }));
  }

  async authorizeAgentUser(agentId: string, platformUserId: string): Promise<void> {
    const user = this.users.find((item) => item.platformUserId === platformUserId);
    if (!user) {
      throw new Error("platform user not found");
    }
    if (
      this.authorizedUsers.some(
        (row) => row.platformUserId === platformUserId && row.enabled && row.bindingId.startsWith(`${agentId}:`)
      )
    ) {
      throw new Error("user already authorized");
    }
    const agent = this.resources.find(
      (item) =>
        item.resourceType === "agent_definition" && item.resourceId === agentId
    );
    const overlap: string[] = [];
    const additions: string[] = [];
    for (const ref of this.capabilities) {
      const inAgent = (agent?.spec as { capabilities?: { capability_ref?: string }[] } | undefined)
        ?.capabilities?.some((item) => item.capability_ref === ref) ?? false;
      (inAgent ? overlap : additions).push(ref);
    }
    this.authorizedUsers = [
      ...this.authorizedUsers,
      {
        platformUserId,
        displayName: user.displayName,
        bindingId: `${agentId}:${platformUserId}`,
        enabled: true,
        capabilityOverlap: overlap,
        capabilityAdditions: additions
      }
    ];
  }

  async revokeAgentUserAuthorization(
    agentId: string,
    platformUserId: string
  ): Promise<void> {
    const exists = this.authorizedUsers.some(
      (row) => row.platformUserId === platformUserId && row.enabled && row.bindingId.startsWith(`${agentId}:`)
    );
    if (!exists) {
      throw new Error("authorization not found");
    }
    this.authorizedUsers = this.authorizedUsers.map((row) =>
      row.platformUserId === platformUserId && row.bindingId.startsWith(`${agentId}:`)
        ? { ...row, enabled: false }
        : row
    );
  }

  // ---- TASK-014（§9.2）：渠道投影 + verify（jsdom 与 http 同契约）----
  private channelEntries: { agentId: string; entry: WebChannelEntry }[] = [];

  async listAgentChannels(agentId: string): Promise<AgentWebChannel> {
    const agent = this.resources.find(
      (item) => item.resourceType === "agent_definition" && item.resourceId === agentId
    );
    return {
      channelType: "web",
      status: agent?.status === "published" ? "active" : "inactive",
      entries: this.channelEntries
        .filter((row) => row.agentId === agentId)
        .map((row) => ({ ...row.entry }))
    };
  }

  async listUserChatAccess(platformUserId: string): Promise<readonly UserChatAccess[]> {
    return this.channelEntries
      .filter((row) => row.entry.platformUserId === platformUserId)
      .map((row) => ({
        accessId: row.entry.accessId,
        agentId: row.agentId,
        createdAt: row.entry.createdAt
      }));
  }

  async verifyAgentWebChannel(agentId: string): Promise<ChannelVerifyResult> {
    const channel = await this.listAgentChannels(agentId);
    const problems: string[] = [];
    if (channel.status !== "active") {
      problems.push("Agent 未发布：Web Chat 入口要求已发布 Agent");
    }
    if (channel.entries.length === 0) {
      problems.push("无活跃 Web Chat 入口：请先开通渠道并生成入口");
    }
    return { channelType: "web", ok: problems.length === 0, problems };
  }

  async issueChatAccess(
    platformUserId: string,
    agentId: string
  ): Promise<IssuedChatAccess> {
    const accessId = `chat-access-${this.chatAccessIds.size + 1}`;
    const token = `test-token-${this.chatAccessIds.size + 1}`;
    this.chatAccessIds.add(accessId);
    this.channelEntries = [
      ...this.channelEntries,
      {
        agentId,
        entry: {
          accessId,
          platformUserId,
          displayName:
            this.users.find((user) => user.platformUserId === platformUserId)
              ?.displayName ?? platformUserId,
          createdAt: nowIso()
        }
      }
    ];
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
    onEvent({ event: "completed", data: { output: "你好！", trace_id: `trace-test-${agentId}` } });
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
    this.channelEntries = this.channelEntries.filter(
      (row) => row.entry.accessId !== accessId
    );
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
