import type { ChatApi, ChatRequest, ChatResponse, RuntimeCall } from "../types/chat";

interface InMemoryChatSeed {
  readonly bindCode: string;
  readonly platformUserId: string;
}

export class InMemoryChatApi implements ChatApi {
  readonly runtimeCalls: RuntimeCall[] = [];
  private boundPlatformUserId?: string;

  constructor(private readonly seed: InMemoryChatSeed) {}

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
