export type ChatResultKind = "bound" | "unbound" | "message";

export interface ChatRequest {
  readonly content: string;
  readonly conversationId: string;
  readonly messageId: string;
}

export interface ChatAccess {
  readonly accessId: string;
  readonly platformUserId: string;
  readonly agentId: string;
  /** 后端 resolve_access 返回的租户（http 实现内部用于 X-Tenant-ID）。 */
  readonly tenantId?: string;
}

export interface ChatResponse {
  readonly executionId?: string;
  readonly kind: ChatResultKind;
  readonly output: string;
  readonly platformUserId?: string;
  readonly requestId: string;
  readonly traceId: string;
}

export interface RuntimeCall {
  readonly content: string;
  readonly platformUserId: string;
}

export interface ChatStreamEvent {
  readonly kind: "token" | "completed" | "error";
  readonly content?: string;
  readonly response?: ChatResponse;
  readonly message?: string;
}

/** closure TASK-009（P1C-05 二层）：Agent 产品面信息（经产品 API 解析）。 */
export interface AgentProductFace {
  readonly agentId: string;
  readonly displayName: string;
  readonly description: string;
  readonly available: boolean;
}

// ---------------------------------------------------------------------------
// TASK-001：Workspace 契约（⛳依赖缺口端点契约冻结，Phase 2/3 后端就绪后同契约切 HTTP）
// 端点冻结：GET /api/v1/workspace/agents | /tasks | /tasks/{id} | /approvals
//           POST /api/v1/workspace/approvals/{id}/decision
//           GET /api/v1/workspace/history | /profile | /memory
//           PUT /api/v1/workspace/profile | /memory/auto-learn
//           PATCH /api/v1/workspace/memory/{id} | DELETE /api/v1/workspace/memory/{id}

/** AgentDefinition 产品模型（FEAT-P4-03）：不暴露 RuntimeProfile 等底层字段。 */
export interface WorkspaceAgent {
  readonly agentId: string;
  readonly displayName: string;
  readonly description: string;
  readonly capabilities: readonly string[];
  readonly available: boolean;
}

export type WorkspaceTaskKind = "chat" | "workflow";
export type WorkspaceTaskStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

/** 对话/Workflow 运行统一展示（FEAT-P4-04）。 */
export interface WorkspaceTask {
  readonly taskId: string;
  readonly title: string;
  readonly kind: WorkspaceTaskKind;
  readonly status: WorkspaceTaskStatus;
  /** 0-100；无进度语义时为 0。 */
  readonly progress: number;
  readonly result?: string;
  readonly agentId?: string;
  readonly startedAt: string;
  readonly updatedAt: string;
}

export type ApprovalDecision = "approve" | "reject";

/** HumanTask 审批队列项（FEAT-P4-05；按 Phase 3 recv_async/send 语义设计）。 */
export interface WorkspaceApproval {
  readonly approvalId: string;
  readonly taskId: string;
  readonly title: string;
  readonly message: string;
  readonly assignee: string;
  readonly createdAt: string;
  readonly status: "pending" | "approved" | "rejected";
}

/** 对话 + 任务统一时间线项（FEAT-P4-06，时间倒序）。 */
export interface WorkspaceHistoryEntry {
  readonly entryId: string;
  readonly kind: "chat" | "task";
  readonly title: string;
  readonly summary: string;
  readonly at: string;
  readonly taskId?: string;
  readonly conversationId?: string;
  readonly traceId?: string;
}

/** Phase 2 Profile 域契约对齐（FEAT-P4-07）。 */
export interface UserProfile {
  readonly platformUserId: string;
  readonly displayName: string;
  readonly email?: string;
  readonly timezone?: string;
  readonly locale?: string;
}

/** Phase 2 Personal Memory 契约对齐（FEAT-P4-07）。 */
export interface PersonalMemoryItem {
  readonly memoryId: string;
  readonly content: string;
  readonly source: string;
  readonly createdAt: string;
  readonly updatedAt: string;
}

/** X409 设置页偏好（remediation §15.1；in-memory 先行，后续对齐 Phase 2 preference 契约）。 */
export interface UserPreference {
  readonly theme: "light" | "dark" | "system";
  readonly language: "zh-CN" | "en-US";
  readonly notifications: boolean;
}

export interface ChatApi {
  resolveAccess?(): Promise<ChatAccess>;
  /** 产品面信息（displayName/icon/description）；实现不可用时返回 undefined。 */
  getAgentProduct?(agentId: string): Promise<AgentProductFace | undefined>;
  sendMessage(request: ChatRequest): Promise<ChatResponse>;
  sendMessageStream?(
    request: ChatRequest,
    onEvent: (event: ChatStreamEvent) => void
  ): Promise<void>;
  // ---- TASK-001 workspace 契约（in-memory 与 http 双实现共享） ----
  listAgents(): Promise<readonly WorkspaceAgent[]>;
  listRecentTasks(): Promise<readonly WorkspaceTask[]>;
  listTasks(): Promise<readonly WorkspaceTask[]>;
  getTask(taskId: string): Promise<WorkspaceTask>;
  listApprovals(): Promise<readonly WorkspaceApproval[]>;
  decideApproval(
    approvalId: string,
    decision: ApprovalDecision,
    comment?: string
  ): Promise<void>;
  listHistory(): Promise<readonly WorkspaceHistoryEntry[]>;
  getProfile(): Promise<UserProfile>;
  updateProfile(profile: UserProfile): Promise<UserProfile>;
  listMemory(): Promise<readonly PersonalMemoryItem[]>;
  correctMemory(memoryId: string, corrected: string): Promise<PersonalMemoryItem>;
  deleteMemory(memoryId: string): Promise<void>;
  setAutoLearn(enabled: boolean): Promise<void>;
  /** P2（review）：读取当前自动学习开关（挂载时加载，不再首次恒显 true）。 */
  getAutoLearn(): Promise<boolean>;
}
