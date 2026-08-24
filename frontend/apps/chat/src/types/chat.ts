export type ChatResultKind = "bound" | "unbound" | "message";

export interface ChatRequest {
  readonly content: string;
  readonly conversationId: string;
  readonly messageId: string;
}

export interface ChatAccess {
  readonly accessId: string;
  readonly platformUserId: string;
  readonly runtimeProfileId: string;
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

export interface ChatApi {
  resolveAccess?(): Promise<ChatAccess>;
  sendMessage(request: ChatRequest): Promise<ChatResponse>;
}
