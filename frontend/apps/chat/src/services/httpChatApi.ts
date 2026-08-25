import { createHttpClient, isRecord, type HttpClient } from "@fluxion/shared";

import type {
  ChatAccess,
  ChatApi,
  ChatRequest,
  ChatResponse,
  ChatStreamEvent
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
  const messageInit = (request: ChatRequest): RequestInit => ({
    body: JSON.stringify(toPayload(request)),
    headers: { ...authorization, "Content-Type": "application/json" },
    method: "POST"
  });

  return {
    async resolveAccess() {
      return client.request(
        "/api/v1/channels/web/access",
        { headers: authorization },
        parseAccess
      );
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
    }
  };
}

export function accessTokenFromHash(hash: string): string {
  const match = /^#\/([^/]+)$/.exec(hash);
  return match ? decodeURIComponent(match[1] ?? "") : "";
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
    runtimeProfileId: requiredString(value.runtime_profile_id, "runtime_profile_id")
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
