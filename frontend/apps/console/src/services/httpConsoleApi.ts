import { createHttpClient, isRecord, type HttpClient } from "@fluxion/shared";

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
  IssuedChatAccess,
  JsonRecord,
  JsonSchemaNode,
  JsonValue,
  PageData,
  PageRequest,
  PlatformUser,
  PublishResult,
  ResourceCreateInput,
  ResourceStatus,
  ResourceSummary,
  ResourceType,
  ResourceVersion,
  ResourceVisibility,
  ConsoleDataSource,
  RollbackResult,
  RunDetail,
  ValidationResult,
  User360Summary,
  WorkflowDraftV2,
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
import type { P1View } from "../types/navigation";

export function createHttpConsoleApi(baseUrl = "", client = createHttpClient(baseUrl)): ConsoleApi {
  return new HttpConsoleApi(client);
}

class HttpConsoleApi implements ConsoleApi {
  readonly dataSource: ConsoleDataSource = "http";
  constructor(private readonly client: HttpClient) {}

  async listResources(resourceType?: ResourceType): Promise<PageData<ResourceSummary>> {
    const filter = resourceType ? `&resource_type=${encodeURIComponent(resourceType)}` : "";
    const page = await this.client.request(
      `/api/v1/resources?page=1&page_size=100${filter}`,
      undefined,
      parseResourcePage
    );
    return { ...page, items: page.items.map(toResourceSummary) };
  }

  async getResource(
    resourceType: ResourceType,
    resourceId: string,
    version?: string
  ): Promise<ResourceVersion> {
    const query = version ? `?version=${encodeURIComponent(version)}` : "";
    return this.client.request(
      `/api/v1/resources/${resourceType}/${encodeURIComponent(resourceId)}${query}`,
      undefined,
      parseResource
    );
  }

  async getResourceSchema(resourceType: ResourceType): Promise<JsonSchemaNode> {
    // ADR-012：spec model 单一真相源——表单结构来自后端 model_json_schema()。
    return this.client.request(
      `/api/v1/resources/${resourceType}/schema`,
      undefined,
      parseResourceSchema
    );
  }

  async createResource(input: ResourceCreateInput): Promise<ResourceVersion> {
    return this.client.request(
      `/api/v1/resources/${input.resourceType}`,
      jsonRequest("POST", {
        resource_id: input.resourceId,
        spec: input.spec,
        version: input.version,
        visibility: input.visibility
      }),
      parseResource
    );
  }

  async createDraftFromLatest(
    resourceType: ResourceType,
    resourceId: string
  ): Promise<ResourceVersion> {
    const versions = await this.listVersions(resourceType, resourceId, { page: 1, pageSize: 100 });
    const draft = versions.items.find((resource) => resource.status === "draft");
    if (draft) return draft;
    const latest = versions.items[0];
    if (!latest) throw new Error("resource not found");
    return this.createResource({
      resourceId,
      resourceType,
      spec: latest.spec,
      version: nextVersion(versions.items),
      visibility: latest.visibility
    });
  }

  async updateDraft(resource: ResourceVersion, spec: JsonRecord): Promise<ResourceVersion> {
    return this.client.request(
      `/api/v1/resources/${resource.resourceType}/${encodeURIComponent(resource.resourceId)}/versions/${encodeURIComponent(resource.version)}`,
      jsonRequest("PUT", { spec }),
      parseResource
    );
  }

  async validateDraft(resource: ResourceVersion): Promise<ValidationResult> {
    return this.client.request(
      `/api/v1/resources/${resource.resourceType}/${encodeURIComponent(resource.resourceId)}/versions/${encodeURIComponent(resource.version)}:validate`,
      jsonRequest("POST", {}),
      parseValidation
    );
  }

  async publishVersion(resource: ResourceVersion): Promise<PublishResult> {
    return this.client.request(
      `/api/v1/resources/${resource.resourceType}/${encodeURIComponent(resource.resourceId)}/versions/${encodeURIComponent(resource.version)}:publish`,
      jsonRequest("POST", {}),
      parsePublish
    );
  }

  async rollbackVersion(
    resource: ResourceVersion,
    targetVersion: string
  ): Promise<RollbackResult> {
    const result = await this.client.request(
      `/api/v1/resources/${resource.resourceType}/${encodeURIComponent(resource.resourceId)}:rollback`,
      jsonRequest("POST", { target_version: targetVersion }),
      parsePublish
    );
    return {
      newVersion: result.version,
      resourceId: result.resourceId,
      status: result.status,
      targetVersion
    };
  }

  async listVersions(
    resourceType: ResourceType,
    resourceId: string,
    request: PageRequest
  ): Promise<PageData<ResourceVersion>> {
    return this.client.request(
      `/api/v1/resources/${resourceType}/${encodeURIComponent(resourceId)}/versions?page=${request.page}&page_size=${request.pageSize}`,
      undefined,
      parseResourcePage
    );
  }

  async listVisibleResources(resourceType: ResourceType): Promise<readonly ResourceSummary[]> {
    return (await this.listResources(resourceType)).items;
  }

  async listBindings(
    request: PageRequest,
    resourceType?: ResourceType
  ): Promise<PageData<BindingRecord>> {
    const filter = resourceType ? `&resource_type=${resourceType}` : "";
    return this.client.request(
      `/api/v1/bindings?page=${request.page}&page_size=${request.pageSize}${filter}`,
      undefined,
      parseBindingPage
    );
  }

  async saveBinding(input: BindingInput): Promise<BindingRecord> {
    return this.client.request(
      "/api/v1/bindings",
      jsonRequest("POST", {
        credential_ref: input.credentialRef,
        resource_id: input.resourceId,
        resource_type: input.resourceType,
        subject_id: input.subjectId,
        subject_type: input.subjectType,
        version_selector: input.versionSelector
      }),
      parseBinding
    );
  }

  async listCredentials(): Promise<readonly CredentialMetadata[]> {
    return this.client.request(
      "/api/v1/credentials?page=1&page_size=100",
      undefined,
      parseCredentialPage
    ).then((page) => page.items);
  }

  async listRuns(): Promise<readonly RunDetail[]> {
    return this.client.request(
      "/api/v1/runs?page=1&page_size=100",
      undefined,
      parseRunPage
    ).then((page) => page.items);
  }

  async listAudit(request: PageRequest): Promise<PageData<AuditRecord>> {
    return this.client.request(
      `/api/v1/audit?page=${request.page}&page_size=${request.pageSize}`,
      undefined,
      parseAuditPage
    );
  }

  async listP1View(view: P1View): Promise<readonly ControlPlaneItem[]> {
    if (view === "eval") {
      return this.client.request("/api/v1/eval/runs", undefined, parseEvalRunList);
    }
    if (view === "users_channels") {
      const page = await this.client.request(
        "/api/v1/platform-users?page=1&page_size=100",
        undefined,
        parsePlatformUserPage
      );
      return page.items.map((user) => ({
        id: user.platformUserId,
        name: user.displayName,
        status: "active",
        detail: user.createdAt
      }));
    }
    if (view === "plugin_policy") {
      return this.client.request(
        "/api/v1/policies?page=1&page_size=100",
        undefined,
        parsePolicyList
      );
    }
    if (view === "capabilities") {
      return this.client.request("/api/v1/capabilities", undefined, parseCapabilityList);
    }
    return this.client.request("/api/v1/runtime-status", undefined, parseRuntimeStatus);
  }

  async listPlatformUsers(request: PageRequest): Promise<PageData<PlatformUser>> {
    return this.client.request(
      `/api/v1/platform-users?page=${request.page}&page_size=${request.pageSize}`,
      undefined,
      parsePlatformUserPage
    );
  }

  async createPlatformUser(platformUserId: string, displayName: string): Promise<PlatformUser> {
    return this.client.request(
      "/api/v1/platform-users",
      jsonRequest("POST", { display_name: displayName, platform_user_id: platformUserId }),
      parsePlatformUser
    );
  }

  async issueChatAccess(
    platformUserId: string,
    agentId: string
  ): Promise<IssuedChatAccess> {
    return this.client.request(
      `/api/v1/platform-users/${encodeURIComponent(platformUserId)}/chat-access`,
      jsonRequest("POST", { agent_id: agentId }),
      parseIssuedChatAccess
    );
  }

  async testRunAgent(
    agentId: string,
    input: { input: string },
    onEvent: (event: { event: string; data: unknown }) => void
  ): Promise<void> {
    await this.client.streamEvents(
      `/studio/agents/${agentId}/test-run`,
      { method: "POST", body: JSON.stringify(input) },
      onEvent
    );
  }

  async getUser360(platformUserId: string): Promise<User360Summary> {
    return this.client.request(
      `/admin/users/${platformUserId}/360`,
      { method: "GET" },
      parseUser360
    );
  }

  async revokeChatAccess(accessId: string): Promise<void> {
    await this.client.request(
      `/api/v1/chat-access/${encodeURIComponent(accessId)}:revoke`,
      jsonRequest("POST", {}),
      () => undefined
    );
  }

  // ---- TASK-002 workflow V2 契约（⛳依赖缺口端点冻结，envelope 经 httpClient 解包） ----

  async getWorkflowSchema(): Promise<WorkflowSchemaV2> {
    return this.client.request("/api/v1/workflows/schema", undefined, parseWorkflowSchema);
  }

  async validateWorkflow(draft: WorkflowDraftV2): Promise<WorkflowValidationResultV2> {
    return this.client.request(
      "/api/v1/workflows/validate",
      { body: JSON.stringify(draft), method: "POST" },
      parseWorkflowValidation
    );
  }

  async listWorkflowRuns(workflowId?: string): Promise<readonly WorkflowRunProjection[]> {
    // P1-3（review 修复）：对齐 Phase 3 后端真实路由——
    // GET /api/v1/workflows/{workflow_id}/runs（单工作流列表，{items,...} 分页）；
    // 跨工作流全量列表为 ⛳依赖缺口（后端待补 list-all 端点），暂用同前缀冻结。
    const path = workflowId
      ? `/api/v1/workflows/${encodeURIComponent(workflowId)}/runs`
      : "/api/v1/workflows/runs";
    return this.client.request(path, undefined, parseWorkflowRuns);
  }

  async listQueues(): Promise<readonly WorkflowQueueSummary[]> {
    return this.client.request("/api/v1/operations/queues", undefined, parseQueues);
  }

  async listWorkers(): Promise<readonly WorkflowWorkerSummary[]> {
    return this.client.request("/api/v1/operations/workers", undefined, parseWorkers);
  }

  // ---- Phase 5 TASK-006：Eval 实页（Phase 5 后端三端点，与 in-memory 同契约）----

  async listEvalSets(): Promise<readonly EvalSetSummary[]> {
    return this.client.request("/api/v1/admin/evals", undefined, parseEvalSets);
  }

  async listEvalRuns(): Promise<readonly EvalRunSummary[]> {
    return this.client.request("/api/v1/admin/evals/runs", undefined, parseEvalRuns);
  }

  async triggerEvalRun(input: EvalTriggerInput): Promise<EvalRunSummary> {
    return this.client.request(
      `/api/v1/admin/evals/${encodeURIComponent(input.evalSetId)}/run`,
      jsonRequest("POST", {
        run_id: `run-${input.evalSetId}-${Date.now()}`,
        eval_set_version: input.evalSetVersion,
        trace_id: input.traceId
      }),
      parseEvalRun
    );
  }
}

function jsonRequest(method: "POST" | "PUT", body: object): RequestInit {
  return { body: JSON.stringify(body), method };
}

function parseResourcePage(value: unknown): PageData<ResourceVersion> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseResource) };
}

function parseBindingPage(value: unknown): PageData<BindingRecord> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseBinding) };
}

function parsePlatformUserPage(value: unknown): PageData<PlatformUser> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parsePlatformUser) };
}

function parseCredentialPage(value: unknown): PageData<CredentialMetadata> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseCredential) };
}

function parseRunPage(value: unknown): PageData<RunDetail> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseRun) };
}

function parseAuditPage(value: unknown): PageData<AuditRecord> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseAudit) };
}

function parseEvalRunList(value: unknown): readonly ControlPlaneItem[] {
  const record = requiredRecord(value, "eval_runs");
  if (!Array.isArray(record.items)) throw new Error("eval_runs.items 无效");
  return record.items.map(parseEvalRunItem);
}

function parseEvalRunItem(value: unknown): ControlPlaneItem {
  const record = requiredRecord(value, "eval_run");
  const evalSetId = requiredString(record.eval_set_id, "eval_run.eval_set_id");
  return {
    id: requiredString(record.run_id, "eval_run.run_id"),
    name: `${evalSetId}@${requiredString(record.eval_set_version, "eval_run.eval_set_version")}`,
    status: requiredBoolean(record.passed, "eval_run.passed") ? "passed" : "failed",
    detail: `score ${requiredNumber(record.score, "eval_run.score")}`
  };
}

// ---- Phase 5 TASK-006：Eval 实页解析（与后端 /api/v1/admin/evals* envelope 契约）----

function parseEvalSets(value: unknown): readonly EvalSetSummary[] {
  const record = requiredRecord(value, "eval_sets");
  if (!Array.isArray(record.items)) throw new Error("eval_sets.items 无效");
  return record.items.map(parseEvalSetItem);
}

function parseEvalSetItem(value: unknown): EvalSetSummary {
  const record = requiredRecord(value, "eval_set");
  return {
    id: requiredString(record.id, "eval_set.id"),
    name: requiredString(record.name, "eval_set.name"),
    version: requiredString(record.version, "eval_set.version"),
    status: requiredString(record.status, "eval_set.status"),
    caseCount: requiredNumber(record.case_count, "eval_set.case_count")
  };
}

function parseEvalRuns(value: unknown): readonly EvalRunSummary[] {
  const record = requiredRecord(value, "eval_runs");
  if (!Array.isArray(record.items)) throw new Error("eval_runs.items 无效");
  return record.items.map(parseEvalRun);
}

function parseEvalRun(value: unknown): EvalRunSummary {
  const record = requiredRecord(value, "eval_run");
  return {
    runId: requiredString(record.run_id, "eval_run.run_id"),
    evalSetId: requiredString(record.eval_set_id, "eval_run.eval_set_id"),
    evalSetVersion: requiredString(record.eval_set_version, "eval_run.eval_set_version"),
    score: requiredNumber(record.score, "eval_run.score"),
    passed: requiredBoolean(record.passed, "eval_run.passed"),
    traceId: requiredString(record.trace_id, "eval_run.trace_id"),
    createdAt: requiredString(record.created_at, "eval_run.created_at")
  };
}

function parsePolicyList(value: unknown): readonly ControlPlaneItem[] {
  const record = requiredRecord(value, "policies");
  if (!Array.isArray(record.items)) throw new Error("policies.items 无效");
  return record.items.map(parsePolicyItem);
}

function parsePolicyItem(value: unknown): ControlPlaneItem {
  const record = requiredRecord(value, "policy");
  const allowed = Array.isArray(record.allowed_tools) ? record.allowed_tools.length : 0;
  return {
    id: requiredString(record.policy_id, "policy.policy_id"),
    name: requiredString(record.name, "policy.name"),
    status: requiredString(record.status, "policy.status"),
    detail: `v${requiredString(record.version, "policy.version")} · allowed_tools=${allowed}`
  };
}

function parseCapabilityList(value: unknown): readonly ControlPlaneItem[] {
  const record = requiredRecord(value, "capabilities");
  if (!Array.isArray(record.items)) throw new Error("capabilities.items 无效");
  return record.items.map(parseCapabilityItem);
}

function parseCapabilityItem(value: unknown): ControlPlaneItem {
  const record = requiredRecord(value, "capability");
  return {
    id: requiredString(record.capability_id, "capability.capability_id"),
    name: requiredString(record.capability_id, "capability.capability_id"),
    status: requiredString(record.status, "capability.status"),
    detail:
      `kind=${requiredString(record.kind, "capability.kind")}` +
      ` · provider=${requiredString(record.provider_id, "capability.provider_id")}`
  };
}

function parseRuntimeStatus(value: unknown): readonly ControlPlaneItem[] {
  const record = requiredRecord(value, "runtime_status");
  return [
    {
      id: requiredString(record.service_instance_id, "runtime_status.service_instance_id"),
      name: "Runtime",
      status: requiredString(record.status, "runtime_status.status"),
      detail:
        `providers=${requiredNumber(record.provider_count, "runtime_status.provider_count")}` +
        ` · plugins=${requiredNumber(record.plugin_count, "runtime_status.plugin_count")}`
    }
  ];
}

function parsePage(value: unknown): { items: unknown[]; page: number; pageSize: number; total: number } {
  const record = requiredRecord(value, "page");
  if (!Array.isArray(record.items)) throw new Error("API page items 无效");
  return {
    items: record.items,
    page: requiredNumber(record.page, "page"),
    pageSize: requiredNumber(record.page_size, "page_size"),
    total: requiredNumber(record.total, "total")
  };
}

function parseResource(value: unknown): ResourceVersion {
  const record = requiredRecord(value, "resource");
  return {
    resourceId: requiredString(record.resource_id, "resource_id"),
    resourceType: requiredResourceType(record.resource_type),
    spec: requiredJsonRecord(record.spec, "spec"),
    status: requiredStatus(record.status),
    tenantId: requiredString(record.tenant_id, "tenant_id"),
    updatedAt: optionalString(record.updated_at) ?? "",
    version: requiredString(record.version, "version"),
    visibility: requiredVisibility(record.visibility)
  };
}

function toResourceSummary(resource: ResourceVersion): ResourceSummary {
  const displayName = optionalString(resource.spec.display_name) ?? optionalString(resource.spec.name);
  return {
    currentVersion: resource.version,
    displayName: displayName ?? resource.resourceId,
    resourceId: resource.resourceId,
    resourceType: resource.resourceType,
    status: resource.status,
    updatedAt: resource.updatedAt,
    visibility: resource.visibility
  };
}

function parseBinding(value: unknown): BindingRecord {
  const record = requiredRecord(value, "binding");
  return {
    bindingId: requiredString(record.binding_id, "binding_id"),
    credentialRef: optionalString(record.credential_ref),
    enabled: requiredBoolean(record.enabled, "enabled"),
    resourceId: requiredString(record.resource_id, "resource_id"),
    resourceType: requiredResourceType(record.resource_type),
    subjectId: requiredString(record.subject_id, "subject_id"),
    subjectType: requiredSubjectType(record.subject_type),
    tenantId: requiredString(record.tenant_id, "tenant_id"),
    versionSelector: requiredString(record.version_selector, "version_selector")
  };
}

function parsePlatformUser(value: unknown): PlatformUser {
  const record = requiredRecord(value, "platform_user");
  return {
    createdAt: requiredString(record.created_at, "created_at"),
    displayName: requiredString(record.display_name, "display_name"),
    platformUserId: requiredString(record.platform_user_id, "platform_user_id")
  };
}

function parseIssuedChatAccess(value: unknown): IssuedChatAccess {
  const record = requiredRecord(value, "chat_access");
  return {
    accessId: requiredString(record.access_id, "access_id"),
    chatPath: requiredString(record.chat_path, "chat_path"),
    createdAt: requiredString(record.created_at, "created_at"),
    platformUserId: requiredString(record.platform_user_id, "platform_user_id"),
    agentId: requiredString(record.agent_id, "agent_id"),
    token: requiredString(record.token, "token")
  };
}

function parseCredential(value: unknown): CredentialMetadata {
  const record = requiredRecord(value, "credential");
  const status = requiredString(record.status, "status");
  if (status !== "active" && status !== "rotating" && status !== "disabled") {
    throw new Error("credential status 无效");
  }
  return {
    credentialRef: requiredString(record.credential_ref, "credential_ref"),
    lastRotatedAt: requiredString(record.last_rotated_at, "last_rotated_at"),
    provider: requiredString(record.provider, "provider"),
    status
  };
}

function parseRun(value: unknown): RunDetail {
  const record = requiredRecord(value, "run");
  const snapshot = requiredRecord(record.snapshot, "snapshot");
  const status = requiredString(record.status, "status");
  if (status !== "running" && status !== "succeeded" && status !== "failed") {
    throw new Error("run status 无效");
  }
  if (!Array.isArray(record.trace_events)) throw new Error("trace_events 无效");
  return {
    executionId: requiredString(record.execution_id, "execution_id"),
    snapshot: {
      mcps: parseVersionRefs(snapshot.mcps, "mcps"),
      plugins: parseVersionRefs(snapshot.plugins, "plugins"),
      policies: parseVersionRefs(snapshot.policies, "policies"),
      runtimeProfile: parseVersionRef(snapshot.runtime_profile, "runtime_profile"),
      skills: parseVersionRefs(snapshot.skills, "skills")
    },
    startedAt: requiredString(record.started_at, "started_at"),
    status,
    traceEvents: record.trace_events.map(parseTraceEvent)
  };
}

function parseVersionRefs(value: unknown, field: string) {
  if (!Array.isArray(value)) throw new Error(`${field} 无效`);
  return value.map((item) => parseVersionRef(item, field));
}

function parseVersionRef(value: unknown, field: string) {
  const record = requiredRecord(value, field);
  return {
    id: requiredString(record.id, `${field}.id`),
    version: requiredString(record.version, `${field}.version`)
  };
}

function parseTraceEvent(value: unknown) {
  const record = requiredRecord(value, "trace_event");
  return {
    at: requiredString(record.at, "trace_event.at"),
    event: requiredString(record.event, "trace_event.event"),
    id: requiredString(record.id, "trace_event.id")
  };
}

function parseAudit(value: unknown): AuditRecord {
  const record = requiredRecord(value, "audit");
  return {
    action: requiredString(record.action, "action"),
    actorId: requiredString(record.actor_id, "actor_id"),
    at: requiredString(record.at, "at"),
    id: requiredString(record.id, "id"),
    resourceId: requiredString(record.resource_id, "resource_id"),
    resourceVersion: requiredString(record.resource_version, "resource_version")
  };
}

function parseResourceSchema(value: unknown): JsonSchemaNode {
  const record = requiredRecord(value, "resource schema");
  if (!isRecord(record.schema)) throw new Error("schema 无效");
  return record.schema as unknown as JsonSchemaNode;
}

function parseValidation(value: unknown): ValidationResult {
  const record = requiredRecord(value, "validation");
  if (!Array.isArray(record.diagnostics) || !record.diagnostics.every((item) => typeof item === "string")) {
    throw new Error("diagnostics 无效");
  }
  return { diagnostics: record.diagnostics, valid: requiredBoolean(record.valid, "valid") };
}

function parsePublish(value: unknown): PublishResult {
  const record = requiredRecord(value, "publish");
  const eventStatus = requiredString(record.event_status, "event_status");
  if (eventStatus !== "pending" && eventStatus !== "published") throw new Error("event_status 无效");
  return {
    eventStatus,
    kubernetesWorkloadCreated: false,
    resourceId: requiredString(record.resource_id, "resource_id"),
    status: requiredStatus(record.status),
    version: requiredString(record.version, "version")
  };
}

function requiredRecord(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`${field} 无效`);
  return value;
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(`${field} 无效`);
  return value;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function requiredNumber(value: unknown, field: string): number {
  if (typeof value !== "number") throw new Error(`${field} 无效`);
  return value;
}

function requiredBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${field} 无效`);
  return value;
}

function requiredResourceType(value: unknown): ResourceType {
  if (
    [
      "runtime_profile",
      "agent_definition",
      "model",
      "tool",
      "secret",
      "skill",
      "mcp",
      "plugin",
      "policy",
      "workflow",
      "eval_set"
    ].includes(String(value))
  ) {
    return value as ResourceType;
  }
  throw new Error("resource_type 无效");
}

function requiredStatus(value: unknown): ResourceStatus {
  if (value === "draft" || value === "published" || value === "deprecated") return value;
  throw new Error("status 无效");
}

function requiredVisibility(value: unknown): ResourceVisibility {
  if (value === "system" || value === "public" || value === "tenant" || value === "private") return value;
  throw new Error("visibility 无效");
}

function requiredSubjectType(value: unknown): "user" | "tenant" {
  if (value === "user" || value === "tenant") return value;
  throw new Error("subject_type 无效");
}

function requiredJsonRecord(value: unknown, field: string): JsonRecord {
  if (!isJsonRecord(value)) throw new Error(`${field} 无效`);
  return value;
}

function isJsonRecord(value: unknown): value is JsonRecord {
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) return true;
  if (Array.isArray(value)) return value.every(isJsonValue);
  return isJsonRecord(value);
}

function nextVersion(resources: readonly ResourceVersion[]): string {
  const numeric = resources.map((resource) => Number(resource.version.replace(/^v/, ""))).filter(Number.isFinite);
  return `v${Math.max(0, ...numeric) + 1}`;
}

/** P2（review）：user_360 逐字段校验（原来裸强转，畸形 payload 会静默透传）。 */
function parseUser360(value: unknown): User360Summary {
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

function parseWorkflowSchema(value: unknown): WorkflowSchemaV2 {
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

function parseWorkflowValidation(value: unknown): WorkflowValidationResultV2 {
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

function parseWorkflowRuns(value: unknown): WorkflowRunProjection[] {
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

function parseQueues(value: unknown): WorkflowQueueSummary[] {
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

function parseWorkers(value: unknown): WorkflowWorkerSummary[] {
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
