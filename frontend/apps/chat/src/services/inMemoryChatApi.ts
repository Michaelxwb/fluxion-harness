import type {
  ApprovalDecision,
  ChatApi,
  ChatRequest,
  ChatResponse,
  ChatStreamEvent,
  PersonalMemoryItem,
  RuntimeCall,
  UserProfile,
  WorkspaceAgent,
  WorkspaceApproval,
  WorkspaceHistoryEntry,
  WorkspaceTask
} from "../types/chat";

interface InMemoryChatSeed {
  readonly bindCode: string;
  readonly platformUserId: string;
  readonly agentId?: string;
  readonly agentDisplayName?: string;
}

/**
 * TASK-001 in-memory ChatApi：workspace/profile/memory 契约的测试替身（生产入口
 * main.tsx 走 httpChatApi；后端 /api/v1/workspace/* 已于 Phase 5 TASK-014 落地）。
 * 审批状态机、学习开关语义与后端契约对齐。
 */
export class InMemoryChatApi implements ChatApi {
  readonly runtimeCalls: RuntimeCall[] = [];
  private boundPlatformUserId?: string;
  private autoLearnEnabled = true;
  private approvals: Map<string, { item: WorkspaceApproval; decided: boolean }> = new Map([
    ...seedApprovals().map((item) => [item.approvalId, { item, decided: false }] as const)
  ]);
  private memory: Map<string, PersonalMemoryItem> = new Map([
    ...seedMemory().map((item) => [item.memoryId, item] as const)
  ]);
  private savedProfile?: UserProfile;

  constructor(private readonly seed: InMemoryChatSeed) {
    // access-token 流（seed 指定 agentId → resolveAccess 提供）即已绑定：
    // 对齐真实 Channel 语义（S-C110：绑定后以 platform_user_id 调用 Runtime）。
    if (seed.agentId) {
      this.boundPlatformUserId = seed.platformUserId;
    }
  }

  // 仅当 seed 指定 agentId 时提供 token 流路径（closure TASK-009）；
  // 否则 resolveAccess 保持 undefined → App 走 /bind-by-message 流（既有 bind 测试）。
  get resolveAccess():
    | (() => Promise<{ accessId: string; platformUserId: string; agentId: string }>)
    | undefined {
    if (!this.seed.agentId) return undefined;
    return async () => ({
      accessId: "access-in-memory",
      platformUserId: this.seed.platformUserId,
      agentId: this.seed.agentId as string
    });
  }

  /** closure TASK-009：产品面信息（in-memory 同契约）。
   * 未提供 agentDisplayName 视为未知 agent → undefined（对齐真实 API 404 语义）。 */
  async getAgentProduct(agentId: string) {
    if (agentId !== this.seed.agentId || !this.seed.agentDisplayName) return undefined;
    return {
      agentId,
      displayName: this.seed.agentDisplayName,
      description: "",
      available: true
    };
  }

  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    if (!this.boundPlatformUserId) {
      return this.handleUnbound(request);
    }
    this.runtimeCalls.push({
      content: request.content,
      platformUserId: this.boundPlatformUserId
    });
    this.learnFromMessage(request.content);
    return response(request, "message", `echo: ${request.content}`, this.boundPlatformUserId);
  }

  /** TASK-011（X408）：in-memory 流式通道（与 http sendMessageStream 同契约）。 */
  async sendMessageStream(
    request: ChatRequest,
    onEvent: (event: ChatStreamEvent) => void
  ): Promise<void> {
    const result = await this.sendMessage(request);
    if (result.kind === "message") {
      for (const token of result.output.split(/(?<=\s)/)) {
        if (token) onEvent({ kind: "token", content: token });
      }
    }
    onEvent({ kind: "completed", response: result });
  }

  private handleUnbound(request: ChatRequest): ChatResponse {
    if (request.content === `/bind ${this.seed.bindCode}`) {
      this.boundPlatformUserId = this.seed.platformUserId;
      return response(request, "bound", "身份绑定成功", this.boundPlatformUserId);
    }
    return response(request, "unbound", "请先使用 /bind <code> 完成绑定");
  }

  // ---- workspace 契约（TASK-001） ----

  async listAgents(): Promise<readonly WorkspaceAgent[]> {
    return [
      {
        agentId: this.seed.agentId ?? "agent-1",
        displayName: this.seed.agentDisplayName ?? "客服助手",
        description: "解答常见问题、发起任务",
        capabilities: ["常见问题解答", "任务发起"],
        available: true
      },
      {
        agentId: "agent-2",
        displayName: "数据分析助手",
        description: "整理数据并生成简报",
        capabilities: ["数据整理", "简报生成"],
        available: true
      }
    ];
  }

  async listRecentTasks(): Promise<readonly WorkspaceTask[]> {
    const tasks = await this.listTasks();
    return [...tasks]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .slice(0, 5);
  }

  async listTasks(): Promise<readonly WorkspaceTask[]> {
    return [
      {
        taskId: "task-1",
        title: "整理周报",
        kind: "workflow",
        status: "running",
        progress: 40,
        agentId: "agent-2",
        startedAt: "2026-08-29T10:00:00Z",
        updatedAt: "2026-08-29T10:05:00Z"
      },
      {
        taskId: "task-2",
        title: "客服对话",
        kind: "chat",
        status: "succeeded",
        progress: 100,
        result: "已解答",
        agentId: this.seed.agentId ?? "agent-1",
        startedAt: "2026-08-28T09:00:00Z",
        updatedAt: "2026-08-28T09:12:00Z"
      }
    ];
  }

  async getTask(taskId: string): Promise<WorkspaceTask> {
    const tasks = await this.listTasks();
    const found = tasks.find((task) => task.taskId === taskId);
    if (!found) throw new Error(`任务不存在: ${taskId}`);
    return found;
  }

  async listApprovals(): Promise<readonly WorkspaceApproval[]> {
    return [...this.approvals.values()]
      .filter((entry) => !entry.decided)
      .map((entry) => entry.item);
  }

  async decideApproval(
    approvalId: string,
    decision: ApprovalDecision,
    comment?: string
  ): Promise<void> {
    if (decision !== "approve" && decision !== "reject") {
      throw new Error(`非法审批决策: ${String(decision)}`);
    }
    const entry = this.approvals.get(approvalId);
    if (!entry) throw new Error(`审批事项不存在: ${approvalId}`);
    if (entry.decided) throw new Error(`审批事项已处理: ${approvalId}`);
    entry.decided = true;
    entry.item = { ...entry.item, status: decision === "approve" ? "approved" : "rejected" };
    void comment;
  }

  async listHistory(): Promise<readonly WorkspaceHistoryEntry[]> {
    return [
      {
        entryId: "entry-1",
        kind: "task",
        title: "整理周报",
        summary: "工作流运行中",
        at: "2026-08-29T10:05:00Z",
        taskId: "task-1",
        traceId: "trace-task-1"
      },
      {
        entryId: "entry-2",
        kind: "chat",
        title: "客服对话",
        summary: "已解答",
        at: "2026-08-28T09:12:00Z",
        conversationId: "conversation-seed",
        traceId: "trace-chat-seed"
      }
    ];
  }

  async getProfile(): Promise<UserProfile> {
    return (
      this.savedProfile ?? {
        platformUserId: this.seed.platformUserId,
        displayName: "用户A",
        email: "user-a@example.com",
        locale: "zh-CN"
      }
    );
  }

  async updateProfile(profile: UserProfile): Promise<UserProfile> {
    this.savedProfile = profile;
    return profile;
  }

  async listMemory(): Promise<readonly PersonalMemoryItem[]> {
    return [...this.memory.values()];
  }

  async correctMemory(memoryId: string, corrected: string): Promise<PersonalMemoryItem> {
    const item = this.memory.get(memoryId);
    if (!item) throw new Error(`记忆不存在: ${memoryId}`);
    const updated: PersonalMemoryItem = {
      ...item,
      content: corrected,
      updatedAt: new Date().toISOString()
    };
    this.memory.set(memoryId, updated);
    return updated;
  }

  async deleteMemory(memoryId: string): Promise<void> {
    if (!this.memory.delete(memoryId)) {
      throw new Error(`记忆不存在: ${memoryId}`);
    }
  }

  async setAutoLearn(enabled: boolean): Promise<void> {
    this.autoLearnEnabled = enabled;
  }

  async getAutoLearn(): Promise<boolean> {
    return this.autoLearnEnabled;
  }

  /** 学习开关开启时从消息提炼记忆（模拟 Phase 2 learner 语义；关闭后不再新增）。 */
  private learnFromMessage(content: string): void {
    if (!this.autoLearnEnabled) return;
    const match = /我常用的称呼是(.+)/.exec(content);
    if (!match) return;
    const memoryId = `memory-learned-${this.memory.size + 1}`;
    const now = new Date().toISOString();
    this.memory.set(memoryId, {
      memoryId,
      content: `用户常用称呼：${match[1] ?? ""}`,
      source: "conversation",
      createdAt: now,
      updatedAt: now
    });
  }
}

export function createInMemoryChatApi(seed: InMemoryChatSeed): InMemoryChatApi {
  return new InMemoryChatApi(seed);
}

function response(
  request: ChatRequest,
  kind: ChatResponse["kind"],
  output: string,
  platformUserId?: string
): ChatResponse {
  return {
    kind,
    output,
    platformUserId,
    requestId: request.messageId,
    traceId: `trace-${request.messageId}`
  };
}

function seedApprovals(): WorkspaceApproval[] {
  return [
    {
      approvalId: "approval-1",
      taskId: "task-1",
      title: "周报确认",
      message: "请确认周报内容后继续发布",
      assignee: "user-a",
      createdAt: "2026-08-29T10:01:00Z",
      status: "pending"
    },
    {
      approvalId: "approval-2",
      taskId: "task-1",
      title: "数据源授权",
      message: "工作流需要访问数据源，请确认授权",
      assignee: "user-a",
      createdAt: "2026-08-29T10:02:00Z",
      status: "pending"
    }
  ];
}

function seedMemory(): PersonalMemoryItem[] {
  return [
    {
      memoryId: "memory-1",
      content: "用户偏好简洁回复",
      source: "conversation",
      createdAt: "2026-08-28T08:00:00Z",
      updatedAt: "2026-08-28T08:00:00Z"
    },
    {
      memoryId: "memory-2",
      content: "用户所在时区为 UTC+8",
      source: "profile",
      createdAt: "2026-08-27T08:00:00Z",
      updatedAt: "2026-08-27T08:00:00Z"
    }
  ];
}
