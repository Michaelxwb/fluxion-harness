import type {
  AuditRecord,
  BindingInput,
  BindingRecord,
  ConsoleApi,
  ControlPlaneItem,
  CredentialMetadata,
  JsonRecord,
  JsonValue,
  IssuedChatAccess,
  PageData,
  PageRequest,
  PlatformUser,
  PublishResult,
  ResourceSummary,
  ResourceCreateInput,
  ResourceType,
  ResourceVersion,
  RollbackResult,
  RunDetail,
  ValidationResult
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
}

export function createInMemoryConsoleApi(seed: ConsoleSeed = defaultConsoleSeed()): ConsoleApi {
  return new InMemoryConsoleApi(seed);
}

class InMemoryConsoleApi implements ConsoleApi {
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
    return page(items, { page: 1, pageSize: items.length || 20 });
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
      return validateWorkflow(current.spec, this.capabilities);
    }
    const hasModel = typeof current.spec.model === "string" && current.spec.model.length > 0;
    const hasTimeout = typeof current.spec.timeout_ms === "number";
    const diagnostics = hasModel && hasTimeout ? ["校验通过"] : ["model 与 timeout_ms 必须存在"];
    return { valid: hasModel && hasTimeout, diagnostics };
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
    runtimeProfileId: string
  ): Promise<IssuedChatAccess> {
    const accessId = `chat-access-${this.chatAccessIds.size + 1}`;
    const token = `test-token-${this.chatAccessIds.size + 1}`;
    this.chatAccessIds.add(accessId);
    return {
      accessId,
      chatPath: `/chat/#/${token}`,
      createdAt: nowIso(),
      platformUserId,
      runtimeProfileId,
      token
    };
  }

  async revokeChatAccess(accessId: string): Promise<void> {
    if (!this.chatAccessIds.delete(accessId)) throw new Error("chat access not found");
  }

  private createBindingRecord(input: BindingInput): BindingRecord {
    return {
      bindingId: `bind-${this.bindings.length + 1}`,
      credentialRef: input.credentialRef,
      enabled: true,
      policyId: input.policyId,
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

function validateWorkflow(spec: JsonRecord, capabilities: ReadonlySet<string>): ValidationResult {
  if (typeof spec.name !== "string" || !spec.name.trim()) {
    return invalidWorkflow("name 必须存在");
  }
  if (typeof spec.engine_ref !== "string" || !spec.engine_ref.startsWith("workflow-engine://")) {
    return invalidWorkflow("engine_ref 必须使用 workflow-engine://");
  }
  if (!Array.isArray(spec.steps) || spec.steps.length === 0) {
    return invalidWorkflow("steps 必须是非空数组");
  }
  const steps = spec.steps.filter(isJsonRecord);
  if (steps.length !== spec.steps.length) {
    return invalidWorkflow("steps 条目必须是对象");
  }
  const references = steps.map((step) => step.capability_ref);
  const invalidRef = references.find(
    (reference) => typeof reference !== "string" || !capabilities.has(reference)
  );
  if (invalidRef !== undefined) {
    return invalidWorkflow(`Capability ref 不可用: ${String(invalidRef)}`);
  }
  return { diagnostics: ["校验通过"], valid: true };
}

function invalidWorkflow(message: string): ValidationResult {
  return { diagnostics: [message], valid: false };
}

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

function isJsonRecord(value: JsonValue): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
