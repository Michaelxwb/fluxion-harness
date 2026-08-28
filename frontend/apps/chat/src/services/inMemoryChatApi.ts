import type { ChatApi, ChatRequest, ChatResponse, RuntimeCall } from "../types/chat";

interface InMemoryChatSeed {
  readonly bindCode: string;
  readonly platformUserId: string;
  readonly agentId?: string;
  readonly agentDisplayName?: string;
}

export class InMemoryChatApi implements ChatApi {
  readonly runtimeCalls: RuntimeCall[] = [];
  private boundPlatformUserId?: string;

  constructor(private readonly seed: InMemoryChatSeed) {}

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
    return response(request, "message", `echo: ${request.content}`, this.boundPlatformUserId);
  }

  private handleUnbound(request: ChatRequest): ChatResponse {
    if (request.content === `/bind ${this.seed.bindCode}`) {
      this.boundPlatformUserId = this.seed.platformUserId;
      return response(request, "bound", "身份绑定成功", this.boundPlatformUserId);
    }
    return response(request, "unbound", "请先使用 /bind <code> 完成绑定");
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
