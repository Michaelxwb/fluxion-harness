export type ResponseParser<T> = (value: unknown) => T;

export interface HttpClient {
  request<T>(path: string, init: RequestInit | undefined, parse: ResponseParser<T>): Promise<T>;
  readEventStream(path: string, init: RequestInit): Promise<string>;
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
    }
  };
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
