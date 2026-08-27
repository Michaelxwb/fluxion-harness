/** Product API 语义 client（TASK-013 / FEAT-F11）。
 *
 * 前端 BFF 唯一数据层：Agent/User/Capability/Product-resource 的业务方法集中
 * 在此，底层复用 services/httpClient（envelope 解包 + ApiError + SSE）。
 * 契约冻结源：backend.design.md §3.4（API-B01..B09）+ frontend.design.md §3.5。
 */

import type { HttpClient } from "../services/httpClient";
import { createHttpClient } from "../services/httpClient";

export interface ProductClientOptions {
  readonly baseUrl?: string;
  readonly fetcher?: typeof fetch;
}

export interface PageRequest {
  readonly page?: number;
  readonly pageSize?: number;
}

export interface ResourceRef {
  readonly resource_id: string;
  readonly kind: string;
  readonly version: string;
  readonly status: string;
  readonly spec: Record<string, unknown>;
}

export interface AgentDraft {
  readonly resource_id?: string;
  readonly version?: string;
  readonly visibility?: "private" | "tenant" | "public";
  readonly spec: Record<string, unknown>;
}

export interface TestRunInput {
  readonly input: string;
}

export type CapabilityKind = "skill" | "tool" | "mcp";

export interface GrantInput {
  readonly type: CapabilityKind;
  readonly capability_ref: string;
  readonly version_pin: string;
  readonly granted_scope?: "invoke" | "manage";
}

export interface UserAccount {
  readonly platform_user_id: string;
  readonly display_name: string;
}

export interface User360View {
  readonly identity: {
    readonly platform_user_id: string;
    readonly display_name: string;
    readonly channels: readonly { channel_type: string; channel_user_id: string }[];
  };
  readonly profile: Record<string, unknown> | null;
  readonly preferences: Record<string, unknown> | null;
  readonly capabilities: readonly Record<string, unknown>[];
  readonly policy: readonly Record<string, unknown>[];
  readonly activity_count: number;
}

/** 冻结的 Product API kind 白名单（IA/路由不随 Resource 自动增长）。 */
export const PRODUCT_KINDS = [
  "agents",
  "models",
  "tools",
  "skills",
  "mcp",
  "runtime-profiles",
  "secrets",
  "policies",
  "evals"
] as const;

export type ProductKind = (typeof PRODUCT_KINDS)[number];

/** ProductKind 复数别名 → schema 端点的单数 ResourceType 枚举值。 */
const SCHEMA_KIND: Record<string, string> = {
  agents: "agent_definition",
  models: "model",
  tools: "tool",
  skills: "skill",
  mcp: "mcp",
  "runtime-profiles": "runtime_profile",
  secrets: "secret",
  policies: "policy",
  evals: "eval_set"
};

export function createProductClient(options: ProductClientOptions = {}) {
  const http: HttpClient = createHttpClient(options.baseUrl, options.fetcher);

  const read = <T>(path: string, parse: (value: unknown) => T): Promise<T> =>
    http.request<T>(path, { method: "GET" }, parse);
  const send = <T>(path: string, method: string, body?: unknown, parse?: (v: unknown) => T): Promise<T> =>
    http.request<T>(
      path,
      { method, body: body === undefined ? undefined : JSON.stringify(body) },
      (value) => (parse ? parse(value) : (value as T))
    );
  const asResource = (value: unknown) =>
    value as { resource_id: string; kind: string; version: string; status: string; spec: Record<string, unknown> };

  return {
    /** Agent（TASK-001/004） */
    createAgent: (draft: AgentDraft) =>
      send("/studio/agents", "POST", draft, asResource),
    getAgent: (agentId: string) =>
      read(`/studio/agents/${agentId}`, asResource),

    /** 试跑：SSE 流式（TASK-005），token/error 帧由消费方处理。 */
    testRunAgent(
      agentId: string,
      input: TestRunInput,
      onEvent: (event: { event: string; data: unknown }) => void
    ): Promise<void> {
      return http.streamEvents(`/studio/agents/${agentId}/test-run`, {
        method: "POST",
        body: JSON.stringify(input)
      }, onEvent);
    },

    /** Capability（TASK-006：skill/tool/mcp typed 绑定）。 */
    listCapabilities: (type?: CapabilityKind) =>
      read(`/studio/${type ?? "skills"}`, (value) => value as readonly Record<string, unknown>[]),

    /** 通用 Product resource CRUD（TASK-004）。 */
    listResources: (kind: ProductKind, page?: PageRequest) =>
      read(`/studio/${kind}?page=${page?.page ?? 1}&page_size=${page?.pageSize ?? 20}`, (value) => {
        const record = value as { items?: unknown[] };
        return (record.items ?? []) as readonly Record<string, unknown>[];
      }),
    getResource: (kind: ProductKind, resourceId: string) =>
      read(`/studio/${kind}/${resourceId}`, (value) => value as Record<string, unknown>),
    createResource: (kind: ProductKind, body: { resource_id?: string; version?: string; spec: Record<string, unknown> }) =>
      send(`/studio/${kind}`, "POST", body, (value) => value as Record<string, unknown>),
    getResourceSchema: (kind: string) =>
      read(
        `/api/v1/resources/${SCHEMA_KIND[kind] ?? kind}/schema`,
        (value) => value as Record<string, unknown>
      ),

    /** User Domain / User 360（TASK-007）。 */
    listUsers: (page?: PageRequest) =>
      read(`/admin/users?page=${page?.page ?? 1}&page_size=${page?.pageSize ?? 20}`, (value) => {
        const record = value as { items?: unknown[] };
        return (record.items ?? []) as readonly Record<string, unknown>[];
      }),
    getUser: (userId: string) =>
      read(`/admin/users/${userId}`, (value) => value as Record<string, unknown>),
    bindUser: (userId: string, agentId: string) =>
      send(
        `/api/v1/platform-users/${userId}/chat-access`,
        "POST",
        { agent_id: agentId },
        (value) => value as Record<string, unknown>
      ),
    getUser360: (userId: string) =>
      read(`/admin/users/${userId}/360`, (value) => value as User360View)
  };
}

export type ProductClient = ReturnType<typeof createProductClient>;
