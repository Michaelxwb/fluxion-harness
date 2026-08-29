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
  | "model"
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

export interface CredentialMetadata {
  readonly credentialRef: string;
  readonly provider: string;
  readonly status: "active" | "rotating" | "disabled";
  readonly lastRotatedAt: string;
}

/** Phase 5 TASK-006：EvalSet 列表项（GET /api/v1/admin/evals）。 */
export interface EvalSetSummary {
  readonly id: string;
  readonly name: string;
  readonly version: string;
  readonly status: string;
  readonly caseCount: number;
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
  publishVersion(resource: ResourceVersion): Promise<PublishResult>;
  rollbackVersion(resource: ResourceVersion, targetVersion: string): Promise<RollbackResult>;
  listVersions(resourceType: ResourceType, resourceId: string, page: PageRequest): Promise<PageData<ResourceVersion>>;
  listVisibleResources(resourceType: ResourceType): Promise<readonly ResourceSummary[]>;
  listBindings(request: PageRequest, resourceType?: ResourceType): Promise<PageData<BindingRecord>>;
  saveBinding(input: BindingInput): Promise<BindingRecord>;
  listCredentials(): Promise<readonly CredentialMetadata[]>;
  listRuns(): Promise<readonly RunDetail[]>;
  // ---- Phase 5 TASK-006：Eval 实页契约（in-memory 先行，http 同契约）----
  listEvalSets(): Promise<readonly EvalSetSummary[]>;
  listEvalRuns(): Promise<readonly EvalRunSummary[]>;
  triggerEvalRun(input: EvalTriggerInput): Promise<EvalRunSummary>;
  listAudit(page: PageRequest): Promise<PageData<AuditRecord>>;
  listP1View(view: P1View): Promise<readonly ControlPlaneItem[]>;
  listPlatformUsers(request: PageRequest): Promise<PageData<PlatformUser>>;
  createPlatformUser(platformUserId: string, displayName: string): Promise<PlatformUser>;
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

/** ⛳依赖缺口：workflow 队列运营视图（in-memory 先行）。 */
export interface WorkflowQueueSummary {
  readonly queueId: string;
  readonly name: string;
  readonly depth: number;
  readonly workers: number;
}

/** ⛳依赖缺口：运行 Worker 状态（in-memory 先行）。 */
export interface WorkflowWorkerSummary {
  readonly workerId: string;
  readonly status: "running" | "idle" | "stopped";
  readonly queues: readonly string[];
  readonly startedAt: string;
  readonly runningWorkflows: number;
}
