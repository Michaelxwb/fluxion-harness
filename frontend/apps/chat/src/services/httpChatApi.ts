import { createHttpClient, isRecord, type HttpClient } from "@fluxion/shared";

import type {
  ChatAccess,
  ChatApi,
  ChatRequest,
  ChatResponse,
  ChatStreamEvent,
  PersonalMemoryItem,
  UserProfile,
  WorkspaceAgent,
  WorkspaceApproval,
  WorkspaceHistoryEntry,
  WorkspaceTask
} from "../types/chat";

interface ChannelPayload {
  readonly execution_id: string | null;
  readonly kind: ChatResponse["kind"];
  readonly output: string;
  readonly platform_user_id: string | null;
  readonly request_id: string;
  readonly trace_id: string;
}

export function createHttpChatApi(
  accessToken: string,
  baseUrl = "",
  client: HttpClient = createHttpClient(baseUrl)
): ChatApi {
  const authorization = { Authorization: `Bearer ${accessToken}` };
  // P1-5（review 修复）：产品 API 要求 X-Tenant-ID——tenant 从 resolveAccess 响应捕获。
  let tenantId: string | null = null;
  const messageInit = (request: ChatRequest): RequestInit => ({
    body: JSON.stringify(toPayload(request)),
    headers: { ...authorization, "Content-Type": "application/json" },
    method: "POST"
  });

  return {
    async resolveAccess() {
      const access = await client.request(
        "/api/v1/channels/web/access",
        { headers: authorization },
        parseAccess
      );
      tenantId = access.tenantId ?? null;
      return access;
    },
    // closure TASK-009：经产品 API（GET /api/v1/agents/{id}）解析产品面信息。
    // P1-1（review 修复）：client.request 已解包 envelope.data，直接消费返回值，
    // 不再二次取 .data；并携带 X-Tenant-ID（缺失时后端 422 会被吞成降级占位）。
    async getAgentProduct(agentId) {
      if (tenantId === null) return undefined;
      try {
        const face = await client.request(
          `/api/v1/agents/${encodeURIComponent(agentId)}`,
          { headers: { ...authorization, "X-Tenant-ID": tenantId } },
          (value: unknown) => value
        );
        if (!isRecord(face)) return undefined;
        const name = face.display_name ?? face.name;
        return {
          agentId,
          displayName: typeof name === "string" ? name : "智能体",
          description: typeof face.description === "string" ? face.description : "",
          available: face.available === true
        };
      } catch {
        return undefined; // 降级占位，不暴露 raw agent_id
      }
    },
    async sendMessage(request) {
      let result: ChatResponse | null = null;
      let error: string | null = null;
      await client.streamEvents(
        "/api/v1/channels/web/access/messages:stream",
        messageInit(request),
        (event) => {
          if (event.event === "completed" && isRecord(event.data)) {
            result = fromPayload(parseChannelPayload(event.data));
          } else if (event.event === "error" && isRecord(event.data)) {
            error = parseEventError(event.data);
          }
        }
      );
      if (error !== null) throw new Error(error);
      if (result === null) throw new Error("Channel stream 未返回 completed 事件");
      return result;
    },
    async sendMessageStream(request, onEvent) {
      await client.streamEvents(
        "/api/v1/channels/web/access/messages:stream",
        messageInit(request),
        (event) => {
          handleStreamEvent(event, onEvent);
        }
      );
    },
    // ---- workspace 契约（Phase 5 TASK-014 后端已落地 /api/v1/workspace/*；
    // envelope 经 httpClient 解包，列表端点统一 {items}） ----
    async listAgents() {
      return client.request("/api/v1/workspace/agents", { headers: authorization }, parseAgents);
    },
    async listRecentTasks() {
      // P1-4（review 修复）：后端无 ?limit= 参数约定——取全量后客户端截前 5 条
      const tasks = await client.request(
        "/api/v1/workspace/tasks",
        { headers: authorization },
        parseTasks
      );
      return tasks.slice(0, 5);
    },
    async listTasks() {
      return client.request("/api/v1/workspace/tasks", { headers: authorization }, parseTasks);
    },
    async getTask(taskId) {
      return client.request(
        `/api/v1/workspace/tasks/${encodeURIComponent(taskId)}`,
        { headers: authorization },
        parseTask
      );
    },
    async listApprovals() {
      return client.request(
        "/api/v1/workspace/approvals",
        { headers: authorization },
        parseApprovals
      );
    },
    async decideApproval(approvalId, decision, comment) {
      await client.request(
        `/api/v1/workspace/approvals/${encodeURIComponent(approvalId)}/decision`,
        {
          body: JSON.stringify({ decision, comment }),
          headers: { ...authorization, "Content-Type": "application/json" },
          method: "POST"
        },
        parseVoid
      );
    },
    async listHistory() {
      return client.request(
        "/api/v1/workspace/history",
        { headers: authorization },
        parseHistoryEntries
      );
    },
    async getProfile() {
      return client.request("/api/v1/workspace/profile", { headers: authorization }, parseProfile);
    },
    async updateProfile(profile) {
      return client.request(
        "/api/v1/workspace/profile",
        {
          body: JSON.stringify(toProfilePayload(profile)),
          headers: { ...authorization, "Content-Type": "application/json" },
          method: "PUT"
        },
        parseProfile
      );
    },
    async listMemory() {
      return client.request("/api/v1/workspace/memory", { headers: authorization }, parseMemory);
    },
    async correctMemory(memoryId, corrected) {
      return client.request(
        `/api/v1/workspace/memory/${encodeURIComponent(memoryId)}`,
        {
          body: JSON.stringify({ content: corrected }),
          headers: { ...authorization, "Content-Type": "application/json" },
          method: "PATCH"
        },
        parseMemoryItem
      );
    },
    async deleteMemory(memoryId) {
      await client.request(
        `/api/v1/workspace/memory/${encodeURIComponent(memoryId)}`,
        { headers: authorization, method: "DELETE" },
        parseVoid
      );
    },
    async setAutoLearn(enabled) {
      await client.request(
        "/api/v1/workspace/memory/auto-learn",
        {
          body: JSON.stringify({ enabled }),
          headers: { ...authorization, "Content-Type": "application/json" },
          method: "PUT"
        },
        parseVoid
      );
    },
    // P2（review）：读取当前开关状态（GET 同端点 {enabled}；Phase 5 TASK-014 已落地）。
    // 读取失败时容错回退 true（页面另做容错加载），不让开关读取拖垮整页。
    async getAutoLearn() {
      try {
        const value = await client.request(
          "/api/v1/workspace/memory/auto-learn",
          { headers: authorization },
          (parsed: unknown) => parsed
        );
        const record = asRecord(value, "auto-learn");
        return typeof record.enabled === "boolean" ? record.enabled : true;
      } catch {
        return true;
      }
    }
  };
}

const WORKSPACE_ROUTE_SEGMENTS = new Set([
  "agents",
  "approvals",
  "chat",
  "history",
  "home",
  "memory",
  "settings",
  "tasks"
]);

/**
 * P1-2（review 修复）：access-token 入口 `#/{token}` 与 HashRouter 路由共存。
 * hash 单段且不是已知工作区路由首段时视为 token（含 `/` 的多段 hash 一定是路由）；
 * 返回 null 表示「这是路由 hash，不是 token」。
 */
export function extractAccessToken(hash: string): string | null {
  const match = /^#\/([^/]+)$/.exec(hash);
  if (!match) return null;
  const candidate = decodeURIComponent(match[1] ?? "");
  if (WORKSPACE_ROUTE_SEGMENTS.has(candidate)) return null;
  return candidate;
}

/** 兼容旧调用：无 token 返回空串。 */
export function accessTokenFromHash(hash: string): string {
  return extractAccessToken(hash) ?? "";
}

function handleStreamEvent(
  event: { readonly event: string; readonly data: unknown },
  onEvent: (event: ChatStreamEvent) => void
): void {
  if (event.event === "token" && isRecord(event.data) && typeof event.data.content === "string") {
    onEvent({ kind: "token", content: event.data.content });
    return;
  }
  if (event.event === "completed" && isRecord(event.data)) {
    onEvent({ kind: "completed", response: fromPayload(parseChannelPayload(event.data)) });
    return;
  }
  if (event.event === "error" && isRecord(event.data)) {
    onEvent({ kind: "error", message: parseEventError(event.data) });
  }
}

function toPayload(request: ChatRequest) {
  return {
    content: request.content,
    conversation_id: request.conversationId,
    message_id: request.messageId
  };
}

function parseAccess(value: unknown): ChatAccess {
  if (!isRecord(value)) throw new Error("Chat access 响应无效");
  return {
    accessId: requiredString(value.access_id, "access_id"),
    platformUserId: requiredString(value.platform_user_id, "platform_user_id"),
    agentId: requiredString(value.agent_id, "agent_id"),
    tenantId: typeof value.tenant_id === "string" ? value.tenant_id : undefined
  };
}

function parseChannelPayload(value: unknown): ChannelPayload {
  if (!isRecord(value)) throw new Error("Channel completed 事件无效");
  const kind = value.kind;
  if (kind !== "bound" && kind !== "unbound" && kind !== "message") {
    throw new Error("Channel kind 无效");
  }
  return {
    execution_id: nullableString(value.execution_id),
    kind,
    output: requiredString(value.output, "output"),
    platform_user_id: nullableString(value.platform_user_id),
    request_id: requiredString(value.request_id, "request_id"),
    trace_id: requiredString(value.trace_id, "trace_id")
  };
}

function parseEventError(value: unknown): string {
  return isRecord(value) && typeof value.message === "string" ? value.message : "消息发送失败";
}

function fromPayload(payload: ChannelPayload): ChatResponse {
  return {
    executionId: payload.execution_id ?? undefined,
    kind: payload.kind,
    output: payload.output,
    platformUserId: payload.platform_user_id ?? undefined,
    requestId: payload.request_id,
    traceId: payload.trace_id
  };
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(`${field} 无效`);
  return value;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

// ---------------------------------------------------------------------------
// TASK-001 workspace payload 解析（envelope.data → 契约类型）

function parseVoid(_value: unknown): void {
  void _value;
}

/** P1-4（review 修复）：后端列表端点统一 {items, ...} 分页 envelope——兼容裸数组（过渡）。 */
function parseItems(value: unknown, field: string): unknown[] {
  if (isRecord(value) && Array.isArray(value.items)) return value.items;
  if (Array.isArray(value)) return value;
  throw new Error(`${field} 无效`);
}

function parseAgents(value: unknown): WorkspaceAgent[] {
  return parseItems(value, "workspace.agents").map(parseAgent);
}

function parseAgent(value: unknown): WorkspaceAgent {
  const record = asRecord(value, "workspace.agent");
  return {
    agentId: requiredString(record.agent_id, "agent_id"),
    displayName: requiredString(record.display_name, "display_name"),
    description: typeof record.description === "string" ? record.description : "",
    capabilities: Array.isArray(record.capabilities)
      ? record.capabilities.filter((item): item is string => typeof item === "string")
      : [],
    available: record.available === true
  };
}

function parseTasks(value: unknown): WorkspaceTask[] {
  return parseItems(value, "workspace.tasks").map(parseTask);
}

function parseTask(value: unknown): WorkspaceTask {
  const record = asRecord(value, "workspace.task");
  const kind = record.kind;
  if (kind !== "chat" && kind !== "workflow") throw new Error("workspace.task kind 无效");
  const status = record.status;
  if (
    status !== "pending" &&
    status !== "running" &&
    status !== "succeeded" &&
    status !== "failed" &&
    status !== "cancelled"
  ) {
    throw new Error("workspace.task status 无效");
  }
  return {
    taskId: requiredString(record.task_id, "task_id"),
    title: requiredString(record.title, "title"),
    kind,
    status,
    progress: typeof record.progress === "number" ? record.progress : 0,
    result: typeof record.result === "string" ? record.result : undefined,
    agentId: typeof record.agent_id === "string" ? record.agent_id : undefined,
    startedAt: requiredString(record.started_at, "started_at"),
    updatedAt: requiredString(record.updated_at, "updated_at")
  };
}

function parseApprovals(value: unknown): WorkspaceApproval[] {
  return parseItems(value, "workspace.approvals").map(parseApproval);
}

function parseApproval(value: unknown): WorkspaceApproval {
  const record = asRecord(value, "workspace.approval");
  const status = record.status;
  return {
    approvalId: requiredString(record.approval_id, "approval_id"),
    taskId: requiredString(record.task_id, "task_id"),
    title: requiredString(record.title, "title"),
    message: requiredString(record.message, "message"),
    assignee: requiredString(record.assignee, "assignee"),
    createdAt: requiredString(record.created_at, "created_at"),
    // P2（review 修复）：透传 wire status（列表端点返回 pending 队列；缺省回退 pending）
    status:
      status === "approved" || status === "rejected" || status === "pending"
        ? status
        : "pending"
  };
}

function parseHistoryEntries(value: unknown): WorkspaceHistoryEntry[] {
  return parseItems(value, "workspace.history").map(parseHistoryEntry);
}

function parseHistoryEntry(value: unknown): WorkspaceHistoryEntry {
  const record = asRecord(value, "workspace.history_entry");
  const kind = record.kind;
  if (kind !== "chat" && kind !== "task") throw new Error("workspace.history kind 无效");
  return {
    entryId: requiredString(record.entry_id, "entry_id"),
    kind,
    title: requiredString(record.title, "title"),
    summary: requiredString(record.summary, "summary"),
    at: requiredString(record.at, "at"),
    taskId: typeof record.task_id === "string" ? record.task_id : undefined,
    conversationId: typeof record.conversation_id === "string" ? record.conversation_id : undefined,
    traceId: typeof record.trace_id === "string" ? record.trace_id : undefined
  };
}

function parseProfile(value: unknown): UserProfile {
  const record = asRecord(value, "workspace.profile");
  return {
    platformUserId: requiredString(record.platform_user_id, "platform_user_id"),
    displayName: requiredString(record.display_name, "display_name"),
    email: typeof record.email === "string" ? record.email : undefined,
    timezone: typeof record.timezone === "string" ? record.timezone : undefined,
    locale: typeof record.locale === "string" ? record.locale : undefined
  };
}

function toProfilePayload(profile: UserProfile): Record<string, unknown> {
  return {
    platform_user_id: profile.platformUserId,
    display_name: profile.displayName,
    email: profile.email,
    timezone: profile.timezone,
    locale: profile.locale
  };
}

function parseMemory(value: unknown): PersonalMemoryItem[] {
  return parseItems(value, "workspace.memory").map(parseMemoryItem);
}

function parseMemoryItem(value: unknown): PersonalMemoryItem {
  const record = asRecord(value, "workspace.memory_item");
  return {
    memoryId: requiredString(record.memory_id, "memory_id"),
    content: requiredString(record.content, "content"),
    source: requiredString(record.source, "source"),
    createdAt: requiredString(record.created_at, "created_at"),
    updatedAt: requiredString(record.updated_at, "updated_at")
  };
}

function asRecord(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`${field} 无效`);
  return value;
}
