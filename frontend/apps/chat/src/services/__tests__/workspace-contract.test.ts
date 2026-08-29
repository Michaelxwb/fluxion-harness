/**
 * TASK-001 契约一致性验收（Acceptance-Refs: S-02~S-08 数据源前置）。
 *
 * 真实边界：in-memory 与 http 双实现 vs 同一 `ChatApi` TS 契约（不 mock 契约本身；
 * http 侧经真实 createHttpClient + fake fetcher 验证 envelope 解包路径唯一）。
 */
import { describe, expect, it } from "vitest";

import { createHttpClient } from "@fluxion/shared";

import { createHttpChatApi, extractAccessToken } from "../httpChatApi";
import { createInMemoryChatApi } from "../inMemoryChatApi";
import type {
  PersonalMemoryItem,
  UserProfile,
  WorkspaceAgent,
  WorkspaceApproval,
  WorkspaceHistoryEntry,
  WorkspaceTask
} from "../../types/chat";

const WORKSPACE_METHODS = [
  "listAgents",
  "listRecentTasks",
  "listTasks",
  "getTask",
  "listApprovals",
  "decideApproval",
  "listHistory",
  "getProfile",
  "updateProfile",
  "listMemory",
  "correctMemory",
  "deleteMemory",
  "setAutoLearn"
] as const;

function seededInMemoryApi() {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a",
    agentId: "agent-1",
    agentDisplayName: "客服助手"
  });
}

describe("TASK-001 Chat workspace 契约一致性", () => {
  it("in-memory 与 http 双实现暴露同一方法集合（契约冻结）", () => {
    const inMemory = seededInMemoryApi();
    const http = createHttpChatApi("token-1");
    for (const method of WORKSPACE_METHODS) {
      expect(typeof (inMemory as unknown as Record<string, unknown>)[method], `in-memory.${method}`).toBe(
        "function"
      );
      expect(typeof (http as unknown as Record<string, unknown>)[method], `http.${method}`).toBe(
        "function"
      );
    }
  });

  it("in-memory 返回形状符合契约（agents/tasks/approvals/history/profile/memory）", async () => {
    const api = seededInMemoryApi();

    const agents = await api.listAgents();
    expect(agents.length).toBeGreaterThan(0);
    for (const agent of agents) {
      assertWorkspaceAgent(agent);
    }

    const recent = await api.listRecentTasks();
    const tasks = await api.listTasks();
    expect(recent.length).toBeLessThanOrEqual(tasks.length);
    for (const task of tasks) {
      assertWorkspaceTask(task);
    }
    const detail = await api.getTask(tasks[0]!.taskId);
    assertWorkspaceTask(detail);

    const approvals = await api.listApprovals();
    for (const approval of approvals) {
      assertWorkspaceApproval(approval);
    }

    const history = await api.listHistory();
    expect(history.length).toBeGreaterThan(0);
    for (const entry of history) {
      assertHistoryEntry(entry);
    }

    assertUserProfile(await api.getProfile());
    for (const item of await api.listMemory()) {
      assertMemoryItem(item);
    }
  });

  it("http 实现返回同一形状（envelope data 解包后契约一致；后端列表统一 {items} 分页）", async () => {
    const { api } = httpApiWithEnvelopes({
      "GET /api/v1/workspace/agents": { items: [wireAgent()] },
      "GET /api/v1/workspace/tasks": { items: [wireTask()] },
      "GET /api/v1/workspace/tasks/task-1": wireTask(),
      "GET /api/v1/workspace/approvals": { items: [wireApproval()] },
      "GET /api/v1/workspace/history": { items: [wireHistoryEntry()] },
      "GET /api/v1/workspace/profile": wireProfile(),
      "GET /api/v1/workspace/memory": { items: [wireMemory()] }
    });

    assertWorkspaceAgent((await api.listAgents())[0]!);
    assertWorkspaceTask((await api.listRecentTasks())[0]!);
    assertWorkspaceTask((await api.listTasks())[0]!);
    assertWorkspaceTask(await api.getTask("task-1"));
    assertWorkspaceApproval((await api.listApprovals())[0]!);
    assertHistoryEntry((await api.listHistory())[0]!);
    assertUserProfile(await api.getProfile());
    assertMemoryItem((await api.listMemory())[0]!);
  });

  it("envelope 解包唯一路径：非 0 code 经 httpClient 抛 ApiError（携带 message 与 request_id）", async () => {
    const { api, requests } = httpApiWithEnvelopes({
      "GET /api/v1/workspace/tasks": { __error: { code: 50001, message: "工作区任务不可用", request_id: "req-err-1" } }
    });
    await expect(api.listTasks()).rejects.toMatchObject({
      name: "ApiError",
      code: 50001,
      message: "工作区任务不可用",
      requestId: "req-err-1"
    });
    expect(requests.at(-1)?.path).toBe("/api/v1/workspace/tasks");
  });

  it("P1-4 回归：listRecentTasks 不使用 ?limit 参数，客户端截前 5 条", async () => {
    const sevenTasks = Array.from({ length: 7 }, (_, index) => ({
      ...wireTask(),
      task_id: `task-${index + 1}`
    }));
    const { api, requests } = httpApiWithEnvelopes({
      "GET /api/v1/workspace/tasks": { items: sevenTasks }
    });

    const recent = await api.listRecentTasks();
    expect(recent).toHaveLength(5);
    expect(recent[0]!.taskId).toBe("task-1");
    expect(requests.at(-1)?.path).toBe("/api/v1/workspace/tasks");
  });

  it("P1-1/P1-5 回归：getAgentProduct 单次解包 + X-Tenant-ID（tenant 经 resolveAccess 捕获）", async () => {
    const { api, requests } = httpApiWithEnvelopes({
      "GET /api/v1/channels/web/access": {
        access_id: "access-1",
        agent_id: "agent-1",
        platform_user_id: "user-a",
        tenant_id: "tenant-a"
      },
      "GET /api/v1/agents/agent-1": {
        agent_id: "agent-1",
        available: true,
        description: "解答常见问题",
        display_name: "客服助手"
      }
    });

    // 未 resolveAccess 前不发起产品请求（无 tenant → 降级 undefined，不 422）
    expect(await api.getAgentProduct?.("agent-1")).toBeUndefined();

    await api.resolveAccess?.();
    const face = await api.getAgentProduct?.("agent-1");
    // P1-1：face 直接来自 envelope.data（不再二次取 .data）
    expect(face).toEqual({
      agentId: "agent-1",
      available: true,
      description: "解答常见问题",
      displayName: "客服助手"
    });
    // P1-5：产品请求携带 X-Tenant-ID（经 resolveAccess 捕获的 tenant_id）
    const agentCall = requests.find((r) => r.path === "/api/v1/agents/agent-1");
    expect(agentCall).toBeDefined();
    expect(agentCall?.headers["X-Tenant-ID"]).toBe("tenant-a");
    expect(agentCall?.headers.Authorization).toBe("Bearer token-1");
  });

  it("http 写操作命中冻结端点与 envelope 解包路径", async () => {
    const { api, requests } = httpApiWithEnvelopes({
      "POST /api/v1/workspace/approvals/appr-1/decision": { decided: true },
      "PUT /api/v1/workspace/profile": wireProfile(),
      "PATCH /api/v1/workspace/memory/mem-1": wireMemory(),
      "DELETE /api/v1/workspace/memory/mem-1": { deleted: true },
      "PUT /api/v1/workspace/memory/auto-learn": { enabled: false }
    });

    await api.decideApproval("appr-1", "approve");
    await api.updateProfile(seedProfile());
    await api.correctMemory("mem-1", "纠正后的内容");
    await api.deleteMemory("mem-1");
    await api.setAutoLearn(false);

    expect(requests.map((r) => `${r.method} ${r.path}`)).toEqual([
      "POST /api/v1/workspace/approvals/appr-1/decision",
      "PUT /api/v1/workspace/profile",
      "PATCH /api/v1/workspace/memory/mem-1",
      "DELETE /api/v1/workspace/memory/mem-1",
      "PUT /api/v1/workspace/memory/auto-learn"
    ]);
  });
});

describe("P1-2 回归：access-token 入口与 HashRouter 路由共存", () => {
  it("单段非路由 hash 视为 token；路由 hash 与多段 hash 不当作 token", () => {
    expect(extractAccessToken("#/abc-token-123")).toBe("abc-token-123");
    expect(extractAccessToken("#/home")).toBeNull();
    expect(extractAccessToken("#/chat")).toBeNull();
    expect(extractAccessToken("#/agents/agent-1")).toBeNull();
    expect(extractAccessToken("#/tasks/task-1")).toBeNull();
    expect(extractAccessToken("")).toBeNull();
    expect(extractAccessToken("#/")).toBeNull();
  });
});

describe("TASK-001 in-memory 审批状态机与学习开关语义", () => {
  it("通过后审批从待确认消失；拒绝可带留言；重复决策被拒绝", async () => {
    const api = seededInMemoryApi();
    const before = await api.listApprovals();
    expect(before.length).toBeGreaterThan(0);
    const target = before[0]!;

    await api.decideApproval(target.approvalId, "approve");
    const after = await api.listApprovals();
    expect(after.find((a) => a.approvalId === target.approvalId)).toBeUndefined();

    await expect(api.decideApproval(target.approvalId, "reject", "太晚了")).rejects.toThrow();

    const second = (await api.listApprovals())[0];
    if (second) {
      await api.decideApproval(second.approvalId, "reject", "内容不符合要求");
      expect((await api.listApprovals()).find((a) => a.approvalId === second.approvalId)).toBeUndefined();
    }
  });

  it("非法决策值被拒绝且列表保持不变", async () => {
    const api = seededInMemoryApi();
    const before = await api.listApprovals();
    await expect(
      // biome-ignore lint: 测试非法输入类型
      api.decideApproval(before[0]!.approvalId, "maybe" as unknown as "approve")
    ).rejects.toThrow();
    expect(await api.listApprovals()).toEqual(before);
  });

  it("Profile 编辑保存往返生效", async () => {
    const api = seededInMemoryApi();
    const original = await api.getProfile();
    const updated = await api.updateProfile({ ...original, displayName: "新昵称" });
    expect(updated.displayName).toBe("新昵称");
    expect((await api.getProfile()).displayName).toBe("新昵称");
  });

  it("Memory 纠正/删除生效", async () => {
    const api = seededInMemoryApi();
    const items = await api.listMemory();
    expect(items.length).toBeGreaterThan(0);
    const target = items[0]!;

    const corrected = await api.correctMemory(target.memoryId, "用户偏好简体中文");
    expect(corrected.content).toBe("用户偏好简体中文");

    await api.deleteMemory(target.memoryId);
    expect((await api.listMemory()).find((m) => m.memoryId === target.memoryId)).toBeUndefined();
  });

  it("自动学习关闭后不再新增 Memory（Phase 2 learning control 契约）", async () => {
    const api = seededInMemoryApi();
    await api.sendMessage({
      content: "/bind WEB-CODE",
      conversationId: "conversation-1",
      messageId: "message-bind"
    });
    const before = (await api.listMemory()).length;

    await api.setAutoLearn(false);
    await api.sendMessage({
      content: "我常用的称呼是老王",
      conversationId: "conversation-1",
      messageId: "message-learn-1"
    });
    await api.sendMessage({
      content: "我常用的称呼是老王",
      conversationId: "conversation-1",
      messageId: "message-learn-2"
    });
    expect(await api.listMemory()).toHaveLength(before);

    await api.setAutoLearn(true);
    await api.sendMessage({
      content: "我常用的称呼是老王",
      conversationId: "conversation-1",
      messageId: "message-learn-3"
    });
    expect((await api.listMemory()).length).toBeGreaterThan(before);
  });
});

// ---------------------------------------------------------------------------
// helpers





function seedProfile(): UserProfile {
  return {
    platformUserId: "user-a",
    displayName: "用户A",
    email: "user-a@example.com",
    locale: "zh-CN"
  };
}


function assertWorkspaceAgent(agent: WorkspaceAgent): void {
  expect(typeof agent.agentId).toBe("string");
  expect(typeof agent.displayName).toBe("string");
  expect(typeof agent.description).toBe("string");
  expect(Array.isArray(agent.capabilities)).toBe(true);
  expect(typeof agent.available).toBe("boolean");
}

function assertWorkspaceTask(task: WorkspaceTask): void {
  expect(typeof task.taskId).toBe("string");
  expect(typeof task.title).toBe("string");
  expect(["chat", "workflow"]).toContain(task.kind);
  expect(["pending", "running", "succeeded", "failed", "cancelled"]).toContain(task.status);
  expect(typeof task.progress).toBe("number");
  expect(typeof task.startedAt).toBe("string");
}

function assertWorkspaceApproval(approval: WorkspaceApproval): void {
  expect(typeof approval.approvalId).toBe("string");
  expect(typeof approval.taskId).toBe("string");
  expect(typeof approval.title).toBe("string");
  expect(typeof approval.message).toBe("string");
  expect(typeof approval.assignee).toBe("string");
  expect(approval.status).toBe("pending");
}

function assertHistoryEntry(entry: WorkspaceHistoryEntry): void {
  expect(typeof entry.entryId).toBe("string");
  expect(["chat", "task"]).toContain(entry.kind);
  expect(typeof entry.title).toBe("string");
  expect(typeof entry.at).toBe("string");
}

function assertUserProfile(profile: UserProfile): void {
  expect(typeof profile.platformUserId).toBe("string");
  expect(typeof profile.displayName).toBe("string");
}

function assertMemoryItem(item: PersonalMemoryItem): void {
  expect(typeof item.memoryId).toBe("string");
  expect(typeof item.content).toBe("string");
  expect(typeof item.source).toBe("string");
}

type EnvelopeBucket = Record<string, unknown>;

// http 测试 bucket 模拟后端 envelope data —— 冻结 wire 格式为 snake_case。
function wireAgent(): Record<string, unknown> {
  return {
    agent_id: "agent-1",
    display_name: "客服助手",
    description: "解答常见问题",
    capabilities: ["常见问题解答"],
    available: true
  };
}

function wireTask(): Record<string, unknown> {
  return {
    task_id: "task-1",
    title: "整理周报",
    kind: "workflow",
    status: "running",
    progress: 40,
    started_at: "2026-08-29T10:00:00Z",
    updated_at: "2026-08-29T10:05:00Z"
  };
}

function wireApproval(): Record<string, unknown> {
  return {
    approval_id: "appr-1",
    task_id: "task-1",
    title: "周报确认",
    message: "请确认周报内容",
    assignee: "user-a",
    created_at: "2026-08-29T10:01:00Z",
    status: "pending"
  };
}

function wireHistoryEntry(): Record<string, unknown> {
  return {
    entry_id: "entry-1",
    kind: "task",
    title: "整理周报",
    summary: "工作流运行中",
    at: "2026-08-29T10:05:00Z",
    task_id: "task-1",
    trace_id: "trace-1"
  };
}

function wireProfile(): Record<string, unknown> {
  return {
    platform_user_id: "user-a",
    display_name: "用户A",
    email: "user-a@example.com",
    locale: "zh-CN"
  };
}

function wireMemory(): Record<string, unknown> {
  return {
    memory_id: "mem-1",
    content: "用户偏好简洁回复",
    source: "conversation",
    created_at: "2026-08-28T08:00:00Z",
    updated_at: "2026-08-28T08:00:00Z"
  };
}

function httpApiWithEnvelopes(buckets: EnvelopeBucket): {
  api: ReturnType<typeof createHttpChatApi>;
  requests: { method: string; path: string; headers: Record<string, string> }[];
} {
  const requests: { method: string; path: string; headers: Record<string, string> }[] = [];
  const fetcher: typeof fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    const headers: Record<string, string> = {};
    for (const [key, value] of Object.entries(init?.headers ?? {})) {
      headers[key] = String(value);
    }
    requests.push({ method, path, headers });
    const bucket = buckets[`${method} ${path}`];
    if (bucket && typeof bucket === "object" && "__error" in bucket) {
      const error = (bucket as { __error: Record<string, unknown> }).__error;
      return jsonResponse(200, { code: error.code, message: error.message, request_id: error.request_id, data: null });
    }
    if (bucket === undefined) {
      return jsonResponse(200, { code: 40400, message: `no bucket for ${method} ${path}`, request_id: "req-miss", data: null });
    }
    return jsonResponse(200, { code: 0, message: "ok", request_id: "req-ok", data: bucket });
  }) as typeof fetch;
  const client = createHttpClient("", fetcher);
  return { api: createHttpChatApi("token-1", "", client), requests };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
