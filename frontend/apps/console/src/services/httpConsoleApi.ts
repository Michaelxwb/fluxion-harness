import { createHttpClient, type HttpClient } from "@fluxion/shared";

import type {
  AuditRecord,
  BindingInput,
  BindingRecord,
  ConsoleApi,
  ConsoleDataSource,
  ControlPlaneItem,
  CredentialMetadata,
  EvalRunSummary,
  EvalSetSummary,
  EvalTriggerInput,
  IssuedChatAccess,
  JsonRecord,
  JsonSchemaNode,
  PageData,
  PageRequest,
  PlatformUser,
  PublishResult,
  ResourceCreateInput,
  ResourceSummary,
  ResourceType,
  ResourceVersion,
  RollbackResult,
  RunDetail,
  User360Summary,
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
  parseAuditPage,
  parseBinding,
  parseBindingPage,
  parseCapabilityList,
  parseCredentialPage,
  parseEvalRun,
  parseEvalRuns,
  parseEvalSets,
  parseIssuedChatAccess,
  parsePlatformUser,
  parsePlatformUserPage,
  parsePolicyList,
  parsePublish,
  parsePublishValidation,
  parseResource,
  parseResourcePage,
  parseResourceSchema,
  parseRunPage,
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
    return this.client.request("/api/v1/capabilities", undefined, parseCapabilityList);
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
