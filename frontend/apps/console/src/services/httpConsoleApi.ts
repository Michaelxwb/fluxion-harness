import { createHttpClient, type HttpClient } from "@fluxion/shared";

import type {
  AgentWebChannel,
  AuditFilters,
  AuditRecord,
  AuthorizedUserSummary,
  ChannelVerifyResult,
  BindingInput,
  BindingRecord,
  ConsoleApi,
  ConsoleDataSource,
  ControlPlaneItem,
  CredentialCreateInput,
  CredentialMetadata,
  CredentialProjection,
  CredentialProjectionPage,
  EvalRunSummary,
  EvalSetSummary,
  EvalTriggerInput,
  IssuedChatAccess,
  JsonRecord,
  JsonSchemaNode,
  ModelConnectionTestResult,
  PageData,
  PageRequest,
  PlatformUser,
  PublishOptions,
  PublishResult,
  ResourceCreateInput,
  ResourceListPage,
  ResourceSummary,
  RunListPage,
  McpConnectionTestResult,
  ModelLabProjection,
  ToolCallTestResult,
  ResourceType,
  ResourceVersion,
  RollbackResult,
  RunDetail,
  User360Summary,
  UserChatAccess,
  ValidationResult,
  WorkflowDraftV2,
  WorkflowQueueSummary,
  WorkflowRunProjection,
  WorkflowSchemaV2,
  WorkflowValidationResultV2,
  WorkflowWorkerSummary
} from "../types/console";
import type { P1View } from "../types/navigation";
import {
  parseAgentWebChannel,
  parseAuditPage,
  parseAuthorizedUserList,
  parseChannelVerifyResult,
  parseBinding,
  parseBindingPage,
  parseCapabilityList,
  parseCredentialPage,
  parseCredentialProjectionPage,
  parseEvalRun,
  parseEvalRuns,
  parseEvalSets,
  parseIssuedChatAccess,
  parsePlatformUser,
  parsePlatformUserPage,
  parsePolicyList,
  parseModelConnectionTest,
  parsePublish,
  parsePublishValidation,
  parseResource,
  parseResourcePage,
  parseResourceSchema,
  parseRunPage,
  parseUserChatAccessList,
  parseValidation,
  toResourceSummary
} from "./httpConsoleParsers";
import {
  parseQueues,
  parseUser360,
  parseWorkers,
  parseWorkflowRuns,
  parseWorkflowSchema,
  parseWorkflowValidation
} from "./httpConsoleWorkflowParsers";

export function createHttpConsoleApi(baseUrl = "", client = createHttpClient(baseUrl)): ConsoleApi {
  return new HttpConsoleApi(client);
}

class HttpConsoleApi implements ConsoleApi {
  readonly dataSource: ConsoleDataSource = "http";
  constructor(private readonly client: HttpClient) {}

  async listResources(
    resourceType?: ResourceType,
    page: ResourceListPage = { page: 1, pageSize: 100 }
  ): Promise<PageData<ResourceSummary>> {
    const params = new URLSearchParams({
      page: String(page.page),
      page_size: String(page.pageSize)
    });
    if (resourceType) params.set("resource_type", resourceType);
    if (page.keyword?.trim()) params.set("keyword", page.keyword.trim());
    if (page.status) params.set("status", page.status);
    const result = await this.client.request(
      `/api/v1/resources?${params.toString()}`,
      undefined,
      parseResourcePage
    );
    return { ...result, items: result.items.map(toResourceSummary) };
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
    // TASK-021 返工：走后端 working-draft 端点（remediation §14.3）——服务端
    // 创建/复用 working draft 并处理 fork 冲突，客户端不再自行 fork 版本
    // （避免与后端版本语义漂移）。
    return this.client.request(
      `/api/v1/resources/${resourceType}/${encodeURIComponent(resourceId)}:working-draft`,
      jsonRequest("POST", {}),
      parseResource
    );
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

  async validatePublish(resource: ResourceVersion): Promise<ValidationResult> {
    // TASK-009 后端 `:validate-publish`：data.{ valid, issues } → ValidationResult
    return this.client.request(
      `/api/v1/resources/${resource.resourceType}/${encodeURIComponent(resource.resourceId)}/versions/${encodeURIComponent(resource.version)}:validate-publish`,
      jsonRequest("POST", {}),
      parsePublishValidation
    );
  }

  async publishVersion(resource: ResourceVersion, options: PublishOptions = {}): Promise<PublishResult> {
    // ADR-A011：前后端发布契约统一——三字段可选传递（publish_note /
    // expected_base_version / gate）；乐观并发检查不再被空 body 静默跳过。
    const body: Record<string, unknown> = {};
    if (options.publishNote) body.publish_note = options.publishNote;
    if (options.expectedBaseVersion) body.expected_base_version = options.expectedBaseVersion;
    if (options.gate) body.gate = options.gate;
    return this.client.request(
      `/api/v1/resources/${resource.resourceType}/${encodeURIComponent(resource.resourceId)}/versions/${encodeURIComponent(resource.version)}:publish`,
      jsonRequest("POST", body),
      parsePublish
    );
  }

  async deprecateVersion(resource: ResourceVersion, reason?: string): Promise<PublishResult> {
    return this.client.request(
      `/api/v1/resources/${resource.resourceType}/${encodeURIComponent(resource.resourceId)}/versions/${encodeURIComponent(resource.version)}:deprecate`,
      jsonRequest("POST", { reason: reason ?? null }),
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

  async listCredentialProjection(
    page: CredentialProjectionPage = { page: 1, pageSize: 20 }
  ): Promise<PageData<CredentialProjection>> {
    const params = new URLSearchParams({
      page: String(page.page),
      page_size: String(page.pageSize)
    });
    if (page.keyword?.trim()) params.set("keyword", page.keyword.trim());
    if (page.purpose?.trim()) params.set("purpose", page.purpose.trim());
    if (page.status) params.set("status", page.status);
    if (page.revoked !== undefined) params.set("revoked", String(page.revoked));
    return this.client.request(
      `/api/v1/credentials/projection?${params.toString()}`,
      undefined,
      parseCredentialProjectionPage
    );
  }

  async listCredentials(page: PageRequest = { page: 1, pageSize: 100 }): Promise<readonly CredentialMetadata[]> {
    // FEAT-04 说明：凭据列表最终由 Credential Projection API 承载（含服务端
    // 搜索/消费者关联）；本参数化仅消除固定 100，调用方按页取数。
    return this.client.request(
      `/api/v1/credentials?page=${page.page}&page_size=${page.pageSize}`,
      undefined,
      parseCredentialPage
    ).then((result) => result.items);
  }

  async createCredential(input: CredentialCreateInput): Promise<ResourceVersion> {
    // TASK-009：明文只写——POST /api/v1/credentials，服务端生成 id，响应不回显明文。
    return this.client.request(
      "/api/v1/credentials",
      jsonRequest("POST", {
        name: input.name,
        secret: input.secret,
        purpose: input.purpose ?? ""
      }),
      parseResource
    );
  }

  async rotateCredential(resourceId: string, secret: string): Promise<ResourceVersion> {
    // TASK-009 行操作·轮换：新明文只写；旧引用版本化保留，消费者按需重新 pin。
    return this.client.request(
      `/api/v1/credentials/${encodeURIComponent(resourceId)}:rotate`,
      jsonRequest("POST", { secret }),
      parseResource
    );
  }

  async disableCredential(resourceId: string): Promise<ResourceVersion> {
    // TASK-009 行操作·禁用：store revoke 后 resolve fail-closed。
    return this.client.request(
      `/api/v1/credentials/${encodeURIComponent(resourceId)}:disable`,
      jsonRequest("POST", {}),
      parseResource
    );
  }

  async createModelProvider(spec: JsonRecord): Promise<ResourceVersion> {
    // TASK-010：连接模型服务——studio 产品端点（服务端生成 id/version；
    // 注意 studio 路由无 /api/v1 前缀）。
    return this.client.request(
      "/studio/model-providers",
      jsonRequest("POST", { spec }),
      parseResource
    );
  }

  async createModelDefinition(spec: JsonRecord): Promise<ResourceVersion> {
    // TASK-010：Discover/手工添加模型——studio 产品端点。
    return this.client.request(
      "/studio/model-definitions",
      jsonRequest("POST", { spec }),
      parseResource
    );
  }

  async testModelProviderConnection(providerId: string): Promise<ModelConnectionTestResult> {
    // TASK-010：Test Connection 接线既有端点（可达性 + discovered_models）。
    return this.client.request(
      `/api/v1/model-providers/${encodeURIComponent(providerId)}:test-connection`,
      jsonRequest("POST", {}),
      parseModelConnectionTest
    );
  }

  async createAgent(spec: JsonRecord): Promise<ResourceVersion> {
    return this.client.request(
      "/studio/agents",
      jsonRequest("POST", { spec }),
      parseResource
    );
  }

  async createWorkflow(spec: JsonRecord): Promise<ResourceVersion> {
    return this.client.request(
      "/studio/workflows",
      jsonRequest("POST", { spec }),
      parseResource
    );
  }

  async createSkill(spec: JsonRecord): Promise<ResourceVersion> {
    return this.client.request(
      "/studio/skills",
      jsonRequest("POST", { spec }),
      parseResource
    );
  }

  async createTool(spec: JsonRecord): Promise<ResourceVersion> {
    return this.client.request(
      "/studio/tools",
      jsonRequest("POST", { spec }),
      parseResource
    );
  }

  async createMcpServer(spec: JsonRecord): Promise<ResourceVersion> {
    return this.client.request(
      "/studio/mcp",
      jsonRequest("POST", { spec }),
      parseResource
    );
  }

  async createPolicy(spec: JsonRecord): Promise<ResourceVersion> {
    return this.client.request(
      "/studio/policies",
      jsonRequest("POST", { spec }),
      parseResource
    );
  }

  async getModelLabProjection(): Promise<ModelLabProjection> {
    return this.client.request("/studio/model-lab/projection", undefined, (value) => {
      const record = value as Record<string, unknown>;
      return {
        providers: ((record.providers as readonly Record<string, unknown>[]) ?? []).map((item) => ({
          resourceId: String(item.resource_id),
          displayName: String(item.display_name),
          version: String(item.version),
          status: String(item.status),
          baseUrl: String(item.base_url),
          credentialRef: String(item.credential_ref)
        })),
        models: ((record.models as readonly Record<string, unknown>[]) ?? []).map((item) => ({
          resourceId: String(item.resource_id),
          name: String(item.name),
          version: String(item.version),
          providerId: String(item.provider_id)
        })),
        credentials: ((record.credentials as readonly Record<string, unknown>[]) ?? []).map((item) => ({
          label: String(item.label),
          value: String(item.value)
        }))
      };
    });
  }

  async testMcpConnection(mcpId: string): Promise<McpConnectionTestResult> {
    return this.client.request(
      `/api/v1/mcp-servers/${encodeURIComponent(mcpId)}:test-connection`,
      jsonRequest("POST", {}),
      (value) => {
        const record = value as Record<string, unknown>;
        return {
          reachable: record.reachable === true,
          discoveredTools: Array.isArray(record.discovered_tools)
            ? record.discovered_tools.filter((item): item is string => typeof item === "string")
            : [],
          error: typeof record.error === "string" ? record.error : null
        };
      }
    );
  }

  async testToolCall(toolId: string): Promise<ToolCallTestResult> {
    return this.client.request(
      `/api/v1/tools/${encodeURIComponent(toolId)}:test-call`,
      jsonRequest("POST", {}),
      (value) => {
        const record = value as Record<string, unknown>;
        return {
          reachable: record.reachable === true,
          statusCode: typeof record.status_code === "number" ? record.status_code : null,
          bodyExcerpt: typeof record.body_excerpt === "string" ? record.body_excerpt : null,
          error: typeof record.error === "string" ? record.error : null
        };
      }
    );
  }

  async listRuns(page: RunListPage = { page: 1, pageSize: 100 }): Promise<PageData<RunDetail>> {
    const params = new URLSearchParams({
      page: String(page.page),
      page_size: String(page.pageSize)
    });
    if (page.status) params.set("status", page.status);
    if (page.keyword?.trim()) params.set("keyword", page.keyword.trim());
    return this.client.request(
      `/api/v1/runs?${params.toString()}`,
      undefined,
      parseRunPage
    );
  }

  async listAudit(request: PageRequest, filters?: AuditFilters): Promise<PageData<AuditRecord>> {
    const params = new URLSearchParams({
      page: String(request.page),
      page_size: String(request.pageSize)
    });
    if (filters?.action) params.set("action", filters.action);
    if (filters?.actorId) params.set("actor_id", filters.actorId);
    if (filters?.targetType) params.set("target_type", filters.targetType);
    if (filters?.createdFrom) params.set("created_from", filters.createdFrom);
    if (filters?.createdTo) params.set("created_to", filters.createdTo);
    return this.client.request(`/api/v1/audit?${params.toString()}`, undefined, parseAuditPage);
  }

  async listP1View(view: P1View, page: PageRequest = { page: 1, pageSize: 100 }): Promise<readonly ControlPlaneItem[]> {
    if (view === "users_channels") {
      const result = await this.client.request(
        `/api/v1/platform-users?page=${page.page}&page_size=${page.pageSize}`,
        undefined,
        parsePlatformUserPage
      );
      return result.items.map((user) => ({
        id: user.platformUserId,
        name: user.displayName,
        status: "active",
        detail: user.createdAt
      }));
    }
    if (view === "plugin_policy") {
      return this.client.request(
        `/api/v1/policies?page=${page.page}&page_size=${page.pageSize}`,
        undefined,
        parsePolicyList
      );
    }
    return this.client.request("/api/v1/capabilities", undefined, parseCapabilityList);
  }

  async listAuthorizedUsers(agentId: string): Promise<readonly AuthorizedUserSummary[]> {
    return this.client.request(
      `/studio/agents/${encodeURIComponent(agentId)}/authorized-users`,
      undefined,
      parseAuthorizedUserList
    );
  }

  async authorizeAgentUser(agentId: string, platformUserId: string): Promise<void> {
    await this.client.request(
      `/studio/agents/${encodeURIComponent(agentId)}/authorized-users`,
      jsonRequest("POST", { platform_user_id: platformUserId }),
      () => undefined
    );
  }

  async revokeAgentUserAuthorization(
    agentId: string,
    platformUserId: string
  ): Promise<void> {
    await this.client.request(
      `/studio/agents/${encodeURIComponent(agentId)}/authorized-users/${encodeURIComponent(platformUserId)}:revoke`,
      jsonRequest("POST", {}),
      () => undefined
    );
  }

  async listPlatformUsers(request: PageRequest & { keyword?: string }): Promise<PageData<PlatformUser>> {
    const params = new URLSearchParams({
      page: String(request.page),
      page_size: String(request.pageSize)
    });
    if (request.keyword?.trim()) params.set("keyword", request.keyword.trim());
    return this.client.request(
      `/api/v1/platform-users?${params.toString()}`,
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

  async listAgentChannels(agentId: string): Promise<AgentWebChannel> {
    return this.client.request(
      `/studio/agents/${encodeURIComponent(agentId)}/channels`,
      undefined,
      parseAgentWebChannel
    );
  }

  async listUserChatAccess(platformUserId: string): Promise<readonly UserChatAccess[]> {
    return this.client.request(
      `/api/v1/platform-users/${encodeURIComponent(platformUserId)}/chat-access`,
      undefined,
      parseUserChatAccessList
    );
  }

  async verifyAgentWebChannel(agentId: string): Promise<ChannelVerifyResult> {
    return this.client.request(
      `/studio/agents/${encodeURIComponent(agentId)}/channels/web:verify`,
      jsonRequest("POST", {}),
      parseChannelVerifyResult
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
    // Phase 5 TASK-011：两端点均已落地——
    // GET /api/v1/workflows/{workflow_id}/runs（单工作流）+
    // GET /api/v1/workflows/runs（跨工作流 list-all，tenant 分页）。
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
