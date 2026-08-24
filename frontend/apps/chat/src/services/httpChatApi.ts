import { createHttpClient, isRecord, type HttpClient } from "@fluxion/shared";

import type { ChatAccess, ChatApi, ChatRequest, ChatResponse } from "../types/chat";

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
  return {
    async resolveAccess() {
      return client.request(
        "/api/v1/channels/web/access",
        { headers: authorization },
        parseAccess
      );
    },
    async sendMessage(request) {
      const stream = await client.readEventStream(
        "/api/v1/channels/web/access/messages:stream",
        {
          body: JSON.stringify(toPayload(request)),
          headers: { ...authorization, "Content-Type": "application/json" },
          method: "POST"
        }
      );
      return fromPayload(parseCompletedEvent(stream));
    }
  };
}

export function accessTokenFromHash(hash: string): string {
  const match = /^#\/([^/]+)$/.exec(hash);
  return match ? decodeURIComponent(match[1] ?? "") : "";
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

function parseCompletedEvent(stream: string): ChannelPayload {
  for (const block of stream.split("\n\n")) {
    const lines = block.split("\n");
    const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
    const data = lines.find((line) => line.startsWith("data: "))?.slice(6);
    if (event === "completed" && data) return parseChannelPayload(JSON.parse(data));
    if (event === "error" && data) throw new Error(parseEventError(JSON.parse(data)));
  }
  throw new Error("Channel stream 未返回 completed 事件");
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
