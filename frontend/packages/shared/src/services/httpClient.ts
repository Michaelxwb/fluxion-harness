export type ResponseParser<T> = (value: unknown) => T;

export interface SseEvent {
  readonly event: string;
  readonly data: unknown;
}

export interface HttpClient {
  request<T>(path: string, init: RequestInit | undefined, parse: ResponseParser<T>): Promise<T>;
  readEventStream(path: string, init: RequestInit): Promise<string>;
  streamEvents(path: string, init: RequestInit, onEvent: (event: SseEvent) => void): Promise<void>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: number,
    readonly requestId: string,
    readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function createHttpClient(baseUrl = "", fetcher: typeof fetch = fetch): HttpClient {
  return {
    async request(path, init, parse) {
      const response = await fetcher(`${baseUrl}${path}`, withJsonHeaders(init));
      const envelope = parseEnvelope(await response.json());
      if (!response.ok || envelope.code !== 0) {
        throw new ApiError(envelope.message, envelope.code, envelope.requestId, response.status);
      }
      return parse(envelope.data);
    },
    async readEventStream(path, init) {
      const response = await fetcher(`${baseUrl}${path}`, init);
      if (!response.ok) {
        const envelope = parseEnvelope(await response.json());
        throw new ApiError(envelope.message, envelope.code, envelope.requestId, response.status);
      }
      return response.text();
    },
    async streamEvents(path, init, onEvent) {
      const response = await fetcher(`${baseUrl}${path}`, init);
      if (!response.ok) {
        const envelope = parseEnvelope(await response.json());
        throw new ApiError(envelope.message, envelope.code, envelope.requestId, response.status);
      }
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error("响应不支持流式读取");
      }
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = parseSseBlock(block);
          if (event !== null) onEvent(event);
          boundary = buffer.indexOf("\n\n");
        }
      }
      const tail = parseSseBlock(buffer);
      if (tail !== null) onEvent(tail);
    }
  };
}

function parseSseBlock(block: string): SseEvent | null {
  if (!block.trim()) return null;
  let event = "message";
  let data: unknown = null;
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) {
      event = line.slice(7);
    } else if (line.startsWith("data: ")) {
      const raw = line.slice(6);
      try {
        data = JSON.parse(raw);
      } catch {
        data = raw;
      }
    }
  }
  return { event, data };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function withJsonHeaders(init: RequestInit | undefined): RequestInit {
  return {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers
    }
  };
}

function parseEnvelope(value: unknown): {
  readonly code: number;
  readonly data: unknown;
  readonly message: string;
  readonly requestId: string;
} {
  if (
    !isRecord(value) ||
    typeof value.code !== "number" ||
    typeof value.message !== "string" ||
    typeof value.request_id !== "string" ||
    !("data" in value)
  ) {
    throw new Error("API 返回了无效响应结构");
  }
  return {
    code: value.code,
    data: value.data,
    message: value.message,
    requestId: value.request_id
  };
}
