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

export interface ChatApi {
  resolveAccess?(): Promise<ChatAccess>;
  /** 产品面信息（displayName/icon/description）；实现不可用时返回 undefined。 */
  getAgentProduct?(agentId: string): Promise<AgentProductFace | undefined>;
  sendMessage(request: ChatRequest): Promise<ChatResponse>;
  sendMessageStream?(
    request: ChatRequest,
    onEvent: (event: ChatStreamEvent) => void
  ): Promise<void>;
}
