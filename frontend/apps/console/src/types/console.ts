import type { User360View } from "@fluxion/shared";

import type { P1View } from "./navigation";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | JsonRecord;

export interface JsonRecord {
  readonly [key: string]: JsonValue;
}

/**
 * ADR-012：后端 spec model 的 model_json_schema() 直接作为表单单一真相源。
 * 这里只声明渲染器消费的结构子集（pydantic 输出的其余键原样透传）。
 */
export interface JsonSchemaNode {
  readonly type?: string | readonly string[];
  readonly title?: string;
  readonly description?: string;
  readonly properties?: Readonly<Record<string, JsonSchemaNode>>;
  readonly required?: readonly string[];
  readonly items?: JsonSchemaNode;
  readonly enum?: readonly JsonValue[];
  readonly const?: JsonValue;
  readonly default?: JsonValue;
  readonly "$ref"?: string;
  readonly "$defs"?: Readonly<Record<string, JsonSchemaNode>>;
  readonly additionalProperties?: boolean | JsonSchemaNode;
  readonly anyOf?: readonly JsonSchemaNode[];
}

export type ResourceType =
  | "runtime_profile"
  | "agent_definition"
  | "model_provider"
  | "model_definition"
  | "tool"
  | "secret"
  | "skill"
  | "mcp"
  | "plugin"
  | "policy"
  | "workflow"
  | "eval_set";
export type ResourceStatus = "draft" | "published" | "deprecated";
export type ResourceVisibility = "system" | "public" | "tenant" | "private";

export interface PageRequest {
  readonly page: number;
  readonly pageSize: number;
}

export interface PageData<T> extends PageRequest {
  readonly items: readonly T[];
  readonly total: number;
}

export interface ResourceVersion {
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly tenantId: string;
  readonly version: string;
  readonly status: ResourceStatus;
  readonly visibility: ResourceVisibility;
  readonly spec: JsonRecord;
  readonly updatedAt: string;
}

export interface ResourceSummary {
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly displayName: string;
  readonly currentVersion: string;
  readonly status: ResourceStatus;
  readonly visibility: ResourceVisibility;
  readonly updatedAt: string;
}

export interface ResourceCreateInput {
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly version: string;
  readonly visibility: ResourceVisibility;
  readonly spec: JsonRecord;
}

export interface ValidationResult {
  readonly valid: boolean;
  readonly diagnostics: readonly string[];
}

export interface PublishResult {
  readonly resourceId: string;
  readonly version: string;
  readonly status: ResourceStatus;
  readonly eventStatus: "pending" | "published";
  readonly kubernetesWorkloadCreated: false;
}

/** Release Gate 参数（ADR-A011：仅 AGENT_DEFINITION 发布评估 gate）。 */
export interface ReleaseGateParams {
  readonly candidateEvalRunId: string;
  readonly baselineEvalRunId: string;
  readonly threshold?: number;
}

/** publish 可选参数（ADR-A011 前后端契约统一：三字段可选）。 */
export interface PublishOptions {
  readonly publishNote?: string;
  readonly expectedBaseVersion?: string;
  readonly gate?: ReleaseGateParams;
}

export interface RollbackResult {
  readonly resourceId: string;
  readonly targetVersion: string;
  readonly newVersion: string;
  readonly status: ResourceStatus;
}

export interface BindingRecord {
  readonly bindingId: string;
  readonly tenantId: string;
  readonly subjectType: "user" | "tenant";
  readonly subjectId: string;
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly versionSelector: string;
  readonly credentialRef: string | null;
  readonly enabled: boolean;
}

/** TASK-025：Model 页聚合投影（一次请求返回页面所需数据）。 */
export interface ProjectionProvider {
  readonly resourceId: string;
  readonly displayName: string;
  readonly version: string;
  readonly status: string;
  readonly baseUrl: string;
  readonly credentialRef: string;
}

export interface ProjectionModel {
  readonly resourceId: string;
  readonly name: string;
  readonly version: string;
  readonly providerId: string;
}

export interface ProjectionCredential {
  readonly label: string;
  readonly value: string;
}

export interface ModelLabProjection {
  readonly providers: readonly ProjectionProvider[];
  readonly models: readonly ProjectionModel[];
  readonly credentials: readonly ProjectionCredential[];
}

/** TASK-021（§8.10）：审计组合过滤（后端查询参数下推）。 */
export interface AuditFilters {
  readonly action?: string;
  readonly actorId?: string;
  readonly targetType?: string;
  readonly createdFrom?: string;
  readonly createdTo?: string;
}

/** TASK-018：MCP 连接测试结果（:test-connection 契约）。 */
export interface McpConnectionTestResult {
  readonly reachable: boolean;
  readonly discoveredTools: readonly string[];
  readonly error: string | null;
}

/** TASK-017：Tool Test Call 结果（http_api 真实出站）。 */
export interface ToolCallTestResult {
  readonly reachable: boolean;
  readonly statusCode: number | null;
  readonly bodyExcerpt: string | null;
  readonly error: string | null;
}

/** TASK-014（§9.2）：Agent 渠道产品投影——Web Chat 入口（token 不回显）。 */
export interface WebChannelEntry {
  readonly accessId: string;
  readonly platformUserId: string;
  readonly displayName: string;
  readonly createdAt: string;
}

export interface AgentWebChannel {
  readonly channelType: "web";
  readonly status: "active" | "inactive";
  readonly entries: readonly WebChannelEntry[];
}

export interface ChannelVerifyResult {
  readonly channelType: "web";
  readonly ok: boolean;
  readonly problems: readonly string[];
}

/** TASK-013：Agent → 用户授权产品投影（§9.1；不暴露 Binding 内部结构）。 */
export interface AuthorizedUserSummary {
  readonly platformUserId: string;
  readonly displayName: string;
  readonly bindingId: string;
  readonly enabled: boolean;
  /** 用户 grant 与 Agent 默认能力的交集（生效能力）。 */
  readonly capabilityOverlap: readonly string[];
  /** 用户持有但 Agent 未默认开放的能力（相对增项）。 */
  readonly capabilityAdditions: readonly string[];
}

export interface CredentialMetadata {
  readonly credentialRef: string;
  readonly provider: string;
  readonly status: "active" | "rotating" | "disabled";
  readonly lastRotatedAt: string;
}

/** TASK-009：凭据创建输入（明文只写，服务端不回显）。 */
export interface CredentialCreateInput {
  readonly name: string;
  readonly secret: string;
  readonly purpose?: string;
}

/** TASK-010：Provider 连接测试结果（:test-connection 契约）。 */
export interface ModelConnectionTestResult {
  readonly reachable: boolean;
  readonly discoveredModels: readonly string[];
  readonly error: string | null;
}

/** Phase 5 TASK-006：EvalSet 列表项（GET /api/v1/admin/evals）。 */
export interface EvalSetSummary {
  readonly id: string;
  readonly name: string;
  readonly version: string;
  readonly status: string;
  readonly caseCount: number;
  readonly targetKind: "agent_definition" | "workflow" | "runtime_profile";
  readonly targetId: string;
  readonly targetVersion: string;
}

/** Phase 5 TASK-006：EvalRun 列表项/详情（GET /api/v1/admin/evals/runs）。 */
export interface EvalRunSummary {
  readonly runId: string;
  readonly evalSetId: string;
  readonly evalSetVersion: string;
  readonly score: number;
  readonly passed: boolean;
  readonly traceId: string;
  readonly createdAt: string;
}

/** Phase 5 TASK-006：触发评测入参（POST /api/v1/admin/evals/{id}/run）。 */
export interface EvalTriggerInput {
  readonly evalSetId: string;
  readonly evalSetVersion: string;
  readonly traceId: string;
}

export interface VersionRef {
  readonly id: string;
  readonly version: string;
}

export interface TraceEvent {
  readonly id: string;
  readonly event: string;
  readonly at: string;
}

export interface RunDetail {
  readonly executionId: string;
  readonly status: "running" | "succeeded" | "failed";
  readonly startedAt: string;
  readonly snapshot: {
    readonly runtimeProfile: VersionRef;
    readonly skills: readonly VersionRef[];
    readonly mcps: readonly VersionRef[];
    readonly plugins: readonly VersionRef[];
    readonly policies: readonly VersionRef[];
  };
  readonly traceEvents: readonly TraceEvent[];
}

export interface AuditRecord {
  readonly id: string;
  readonly action: string;
  readonly actorId: string;
  readonly resourceId: string;
  readonly resourceVersion: string;
  readonly at: string;
  /** TASK-021（§8.10）：详情 SideSheet 关联字段（规则 23）与 before/after 快照。 */
  readonly requestId?: string;
  readonly traceId?: string | null;
  readonly targetType?: string;
  readonly before?: Record<string, unknown> | null;
  readonly after?: Record<string, unknown> | null;
}

export interface ControlPlaneItem {
  readonly id: string;
  readonly name: string;
  readonly status: string;
  readonly detail: string;
}

export interface PlatformUser {
  readonly platformUserId: string;
  readonly displayName: string;
  readonly createdAt: string;
}

/** P2（review）：User 360 契约类型下沉 shared（`@fluxion/shared` 单一事实源），
 * 不再本地重写一份结构相同类型（防漂移）。 */
export type User360Summary = User360View;

export interface IssuedChatAccess {
  readonly accessId: string;
  readonly platformUserId: string;
  readonly agentId: string;
  readonly token: string;
  readonly chatPath: string;
  readonly createdAt: string;
}

export interface BindingInput {
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly subjectType: "user" | "tenant";
  readonly subjectId: string;
  readonly versionSelector: string;
  readonly credentialRef: string | null;
}

export type ConsoleDataSource = "http" | "in-memory";

export interface ConsoleApi {
  /** 数据源标记（P2 review）：⛳ 依赖缺口端点当前仅 in-memory 展示，UI 据此标注"示例数据"。 */
  readonly dataSource: ConsoleDataSource;
  listResources(resourceType?: ResourceType): Promise<PageData<ResourceSummary>>;
  getResourceSchema(resourceType: ResourceType): Promise<JsonSchemaNode>;
  getResource(resourceType: ResourceType, resourceId: string, version?: string): Promise<ResourceVersion>;
  createResource(input: ResourceCreateInput): Promise<ResourceVersion>;
  createDraftFromLatest(resourceType: ResourceType, resourceId: string): Promise<ResourceVersion>;
  updateDraft(resource: ResourceVersion, spec: JsonRecord): Promise<ResourceVersion>;
  validateDraft(resource: ResourceVersion): Promise<ValidationResult>;
  /** 发布完整校验（TASK-009 后端 `:validate-publish`）：返回可操作问题清单。 */
  validatePublish(resource: ResourceVersion): Promise<ValidationResult>;
  publishVersion(resource: ResourceVersion, options?: PublishOptions): Promise<PublishResult>;
  deprecateVersion(resource: ResourceVersion, reason?: string): Promise<PublishResult>;
  rollbackVersion(resource: ResourceVersion, targetVersion: string): Promise<RollbackResult>;
  listVersions(resourceType: ResourceType, resourceId: string, page: PageRequest): Promise<PageData<ResourceVersion>>;
  listVisibleResources(resourceType: ResourceType): Promise<readonly ResourceSummary[]>;
  listBindings(request: PageRequest, resourceType?: ResourceType): Promise<PageData<BindingRecord>>;
  saveBinding(input: BindingInput): Promise<BindingRecord>;
  listCredentials(): Promise<readonly CredentialMetadata[]>;
  createCredential(input: CredentialCreateInput): Promise<ResourceVersion>;
  /** TASK-009 行操作·轮换：新明文只写，服务端生成新版本 SecretRef。 */
  rotateCredential(resourceId: string, secret: string): Promise<ResourceVersion>;
  /** TASK-009 行操作·禁用：store 层 revoke（resolve fail-closed），spec 标记 revoked。 */
  disableCredential(resourceId: string): Promise<ResourceVersion>;
  /** TASK-010：连接模型服务——studio 产品端点创建（服务端生成 id/version）。 */
  createModelProvider(spec: JsonRecord): Promise<ResourceVersion>;
  createModelDefinition(spec: JsonRecord): Promise<ResourceVersion>;
  /** TASK-010：Provider 连接测试（可达性 + 模型发现，真实探测 base_url/models）。 */
  testModelProviderConnection(providerId: string): Promise<ModelConnectionTestResult>;
  /** TASK-011：产品语义 Agent 创建；resource_id/version 均由服务端生成。 */
  createAgent(spec: JsonRecord): Promise<ResourceVersion>;
  /** TASK-015：产品语义 Workflow 创建（studio 端点，服务端生成 id/version）。 */
  createWorkflow(spec: JsonRecord): Promise<ResourceVersion>;
  /** TASK-016：产品语义 Skill 创建（studio 端点，服务端生成 id/version）。 */
  createSkill(spec: JsonRecord): Promise<ResourceVersion>;
  /** TASK-017：产品语义 Tool 创建（studio 端点，服务端生成 id/version）。 */
  createTool(spec: JsonRecord): Promise<ResourceVersion>;
  /** TASK-018：产品语义 MCP Server 创建（studio 端点，服务端生成 id/version）。 */
  createMcpServer(spec: JsonRecord): Promise<ResourceVersion>;
  /** TASK-022：产品语义 Policy 创建（studio 端点，服务端生成 id/version）。 */
  createPolicy(spec: JsonRecord): Promise<ResourceVersion>;
  /** TASK-025：Model 页聚合投影（单请求消除 O(N)）。 */
  getModelLabProjection(): Promise<ModelLabProjection>;
  /** TASK-018：MCP 连接测试（真实握手 + 发现远端工具）。 */
  testMcpConnection(mcpId: string): Promise<McpConnectionTestResult>;
  /** TASK-017：Tool Test Call（http_api 真实出站，规则 18 timeout）。 */
  testToolCall(toolId: string): Promise<ToolCallTestResult>;
  listRuns(): Promise<readonly RunDetail[]>;
  // ---- Phase 5 TASK-006：Eval 实页契约（in-memory 先行，http 同契约）----
  listEvalSets(): Promise<readonly EvalSetSummary[]>;
  listEvalRuns(): Promise<readonly EvalRunSummary[]>;
  triggerEvalRun(input: EvalTriggerInput): Promise<EvalRunSummary>;
  listAudit(
    page: PageRequest,
    filters?: AuditFilters
  ): Promise<PageData<AuditRecord>>;
  listP1View(view: P1View): Promise<readonly ControlPlaneItem[]>;
  listPlatformUsers(request: PageRequest): Promise<PageData<PlatformUser>>;
  createPlatformUser(platformUserId: string, displayName: string): Promise<PlatformUser>;
  /** TASK-013（§9.1）：Agent 用户授权——Binding 产品投影 + 授权/撤销。 */
  listAuthorizedUsers(agentId: string): Promise<readonly AuthorizedUserSummary[]>;
  authorizeAgentUser(agentId: string, platformUserId: string): Promise<void>;
  revokeAgentUserAuthorization(agentId: string, platformUserId: string): Promise<void>;
  /** TASK-014（§9.2）：渠道投影 + Web Chat verify（真实 resolve 链检查）。 */
  listAgentChannels(agentId: string): Promise<AgentWebChannel>;
  verifyAgentWebChannel(agentId: string): Promise<ChannelVerifyResult>;
  issueChatAccess(platformUserId: string, agentId: string): Promise<IssuedChatAccess>;
  revokeChatAccess(accessId: string): Promise<void>;
  getUser360(platformUserId: string): Promise<User360Summary>;
  testRunAgent(
    agentId: string,
    input: { input: string },
    onEvent: (event: { event: string; data: unknown }) => void
  ): Promise<void>;
  // ---- TASK-002 workflow V2 契约（in-memory 先行，⛳依赖缺口同契约切 HTTP） ----
  getWorkflowSchema(): Promise<WorkflowSchemaV2>;
  validateWorkflow(draft: WorkflowDraftV2): Promise<WorkflowValidationResultV2>;
  listWorkflowRuns(workflowId?: string): Promise<readonly WorkflowRunProjection[]>;
  listQueues(): Promise<readonly WorkflowQueueSummary[]>;
  listWorkers(): Promise<readonly WorkflowWorkerSummary[]>;
}

/** closure TASK-008（P1C-04）：能力选择 typed 三元组（Picker 契约）。 */
export type CapabilitySelectionType = "skill" | "tool" | "mcp";

export interface CapabilitySelection {
  readonly type: CapabilitySelectionType;
  readonly capabilityRef: string;
  readonly versionPin: string;
}

// ---------------------------------------------------------------------------
// TASK-002：WorkflowDefinition V2 契约（Phase 3 resources/workflow_nodes.py 对齐；
// 节点字段与 spec JSON 同形（snake_case），9 种节点以 type 为 discriminator）。
// ⛳依赖缺口端点冻结：GET /api/v1/workflows/schema、POST /api/v1/workflows/validate、
// GET /api/v1/workflows/runs、GET /api/v1/operations/queues、GET /api/v1/operations/workers

export type WorkflowV2NodeKind =
  | "capability"
  | "agent"
  | "condition"
  | "switch"
  | "parallel"
  | "transform"
  | "wait"
  | "human_task"
  | "subworkflow";

export interface WorkflowV2RetryPolicy {
  readonly max_attempts: number;
  readonly delay_ms: number;
}

export interface WorkflowV2NodeBase {
  readonly id: string;
  readonly depends_on?: readonly string[];
  readonly timeout_ms?: number;
  readonly retry_policy?: WorkflowV2RetryPolicy;
  readonly output_schema?: JsonRecord;
}

export interface WorkflowV2CapabilityNode extends WorkflowV2NodeBase {
  readonly type: "capability";
  readonly capability_ref: string;
  readonly input?: JsonRecord;
}

export interface WorkflowV2AgentNode extends WorkflowV2NodeBase {
  readonly type: "agent";
  readonly agent_ref: string;
  readonly prompt?: string;
  readonly max_turns?: number;
  readonly input?: JsonRecord;
}

export interface WorkflowV2ConditionNode extends WorkflowV2NodeBase {
  readonly type: "condition";
  readonly expression: string;
  readonly then: readonly string[];
  readonly else?: readonly string[];
}

export interface WorkflowV2SwitchCase {
  readonly value: string;
  readonly node_ids: readonly string[];
}

export interface WorkflowV2SwitchNode extends WorkflowV2NodeBase {
  readonly type: "switch";
  readonly expression: string;
  readonly cases: readonly WorkflowV2SwitchCase[];
  readonly default?: readonly string[];
}

export interface WorkflowV2ParallelBranch {
  readonly branch_id: string;
  readonly node_ids: readonly string[];
}

export interface WorkflowV2ParallelNode extends WorkflowV2NodeBase {
  readonly type: "parallel";
  readonly branches: readonly WorkflowV2ParallelBranch[];
  readonly join_policy?: "all" | "any";
}

export interface WorkflowV2TransformNode extends WorkflowV2NodeBase {
  readonly type: "transform";
  readonly source: string;
  readonly transform: string;
}

export interface WorkflowV2WaitNode extends WorkflowV2NodeBase {
  readonly type: "wait";
  readonly duration_seconds: number;
}

export interface WorkflowV2HumanTaskNode extends WorkflowV2NodeBase {
  readonly type: "human_task";
  readonly assignee: string;
  readonly message?: string;
  readonly timeout_seconds?: number;
}

export interface WorkflowV2SubworkflowNode extends WorkflowV2NodeBase {
  readonly type: "subworkflow";
  readonly workflow_ref: string;
  readonly input?: JsonRecord;
}

export type WorkflowV2Node =
  | WorkflowV2CapabilityNode
  | WorkflowV2AgentNode
  | WorkflowV2ConditionNode
  | WorkflowV2SwitchNode
  | WorkflowV2ParallelNode
  | WorkflowV2TransformNode
  | WorkflowV2WaitNode
  | WorkflowV2HumanTaskNode
  | WorkflowV2SubworkflowNode;

/** Studio 草稿容器（design §3.5 状态划分：local WorkflowDraftV2）。 */
export interface WorkflowDraftV2 {
  readonly name: string;
  readonly display_name?: string;
  readonly description?: string;
  readonly steps: readonly WorkflowV2Node[];
}

/** 校验诊断：逐字段定位（E-02；field 为 spec JSON 字段名）。 */
export interface WorkflowV2Diagnostic {
  readonly nodeId?: string;
  readonly field: string;
  readonly message: string;
}

export interface WorkflowValidationResultV2 {
  readonly valid: boolean;
  readonly diagnostics: readonly WorkflowV2Diagnostic[];
}

/** Workflow Studio 表单渲染契约：每类节点的字段集（frozen，随 V2 schema 升级）。 */
export interface WorkflowNodeFieldSchema {
  readonly field: string;
  readonly required: boolean;
  readonly title: string;
  readonly description?: string;
  readonly type: "string" | "number" | "boolean" | "object" | "array";
}

export interface WorkflowNodeKindSchema {
  readonly kind: WorkflowV2NodeKind;
  readonly title: string;
  readonly fields: readonly WorkflowNodeFieldSchema[];
}

export interface WorkflowSchemaV2 {
  readonly nodeKinds: readonly WorkflowNodeKindSchema[];
}

/** Phase 3 workflow_run 投影契约（design §3.5 listWorkflowRuns）。 */
export type WorkflowRunStatus = "running" | "succeeded" | "failed" | "cancelled" | "paused";

export interface WorkflowRunNodeState {
  readonly status: "running" | "succeeded" | "failed" | "skipped";
  readonly output?: unknown;
  readonly error?: string;
}

export interface WorkflowRunProjection {
  readonly runId: string;
  readonly workflowId: string;
  readonly workflowVersion: string;
  readonly executionId: string;
  readonly traceId: string;
  readonly status: WorkflowRunStatus;
  readonly nodeStates: Readonly<Record<string, WorkflowRunNodeState>>;
  readonly pinnedRefs: readonly { readonly kind: string; readonly id: string; readonly version: string }[];
  readonly createdAt: string;
  readonly updatedAt: string;
}

/** workflow 队列运营视图（Phase 5 TASK-010 后端 /api/v1/operations/queues 已落地）。 */
export interface WorkflowQueueSummary {
  readonly queueId: string;
  readonly name: string;
  readonly depth: number;
  readonly workers: number;
}

/** 运行 Worker 状态（Phase 5 TASK-010 后端 /api/v1/operations/workers 已落地）。 */
export interface WorkflowWorkerSummary {
  readonly workerId: string;
  readonly status: "running" | "idle" | "stopped";
  readonly queues: readonly string[];
  readonly startedAt: string;
  readonly runningWorkflows: number;
}
